"""Closed-JSON command line access to the portable offline evaluation APIs."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from importlib import resources
import json
import os
import stat
import sys

from .analysis.blinding import verify_mapping
from .analysis.unblinding import _verify_result
from .campaign import compile_campaign
from .core import (
    MAX_BYTES, MAX_ITEMS, Assessment, Case, EvaluationError, HumanReview, Rubric, Run,
    SCHEMA, Score, Variant, compare, decode, digest, encode, json_schema,
    score_run,
)
from .protocol import (
    CAMPAIGN_SCHEMA, RESOLVED_CAMPAIGN_SCHEMA, CampaignSpec, ResolvedCampaign,
    campaign_decode, campaign_encode, campaign_json_schema,
    resolved_campaign_json_schema,
)
from .protocol.analysis import ANALYSIS_TYPES, AnalysisResult, AnalysisSpec, UnblindedComparison
from .protocol.publication import (
    RECORD_TYPES, ContentRef, PublishedResultManifest, ResultSetEntry,
    publication_decode, publication_json_schema,
)
from .publication import ObjectStore, publish_manifest, seal_result_set, verify_result_set
from .analysis import blind_result_set, unblind


CLI_SCHEMA = "codex.agent-evals-cli-result/v1"
CAPABILITIES_SCHEMA = "codex.agent-evals-capabilities/v1"
CONFORMANCE_SCHEMA = "codex.agent-evals-conformance/v1"


class _UsageError(EvaluationError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError("invalid command arguments")


def _read_bytes(path: str) -> bytes:
    """Read one bounded regular file without following its final symlink."""
    required = (getattr(os, "O_NOFOLLOW", None), getattr(os, "O_NONBLOCK", None))
    if any(type(flag) is not int or flag == 0 for flag in required):
        raise EvaluationError("safe record reader unavailable")
    flags = os.O_RDONLY | required[0] | required[1] | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise EvaluationError("unreadable record file") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise EvaluationError("unsafe record file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise EvaluationError("record byte bound")
        after = os.fstat(descriptor)
        raw = b"".join(chunks)
        if (
            not stat.S_ISREG(after.st_mode)
            or (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
        ):
            raise EvaluationError("record changed while reading")
        return raw
    except EvaluationError:
        raise
    except OSError:
        raise EvaluationError("unreadable record file") from None
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            os.close(descriptor)
        except OSError:
            if not active_error:
                raise EvaluationError("unreadable record file") from None


def _read_text(path: str) -> str:
    try:
        return _read_bytes(path).decode("utf-8")
    except UnicodeError:
        raise EvaluationError("unreadable record file") from None


def _read_record(path: str) -> object:
    """Compatibility entry point for one closed core record."""
    return decode(_read_text(path))


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise EvaluationError("duplicate JSON key")
        result[key] = value
    return result


def _json_file(path: str) -> object:
    try:
        return json.loads(
            _read_text(path), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("nonfinite JSON")),
        )
    except EvaluationError:
        raise
    except (ValueError, RecursionError, OverflowError):
        raise EvaluationError("malformed JSON document") from None


def _core_record(path: str, record_type: type) -> object:
    value = decode(_read_text(path))
    if type(value) is not record_type:
        raise EvaluationError("unexpected record kind")
    return value


def _campaign(path: str, record_type: type) -> object:
    value = campaign_decode(_read_text(path))
    if type(value) is not record_type:
        raise EvaluationError("unexpected campaign record")
    return value


def _publication(path: str, record_type: type) -> object:
    value = publication_decode(_read_text(path))
    if type(value) is not record_type:
        raise EvaluationError("unexpected publication record")
    return value


def _content_ref_data(raw: object) -> ContentRef:
    if type(raw) is not dict or set(raw) != {
        "digest", "byte_size", "media_type", "record_schema", "access_class",
    }:
        raise EvaluationError("invalid content reference selector")
    try:
        ref = ContentRef(**raw)
        from .protocol.publication import validate_content_ref
        validate_content_ref(ref)
        if ref.media_type != "application/json":
            raise EvaluationError("unsupported content reference selector")
        return ref
    except EvaluationError:
        raise
    except (TypeError, AttributeError):
        raise EvaluationError("invalid content reference selector") from None


def _content_ref(path: str) -> ContentRef:
    return _content_ref_data(_json_file(path))


def _entry(raw: object) -> ResultSetEntry:
    if type(raw) is not dict or set(raw) != {
        "trial_id", "execution_ref", "evidence_ref", "classification",
        "missingness", "evidence_verification",
    }:
        raise EvaluationError("invalid result-set entry")
    values = dict(raw)
    for name in ("execution_ref", "evidence_ref"):
        if values[name] is not None:
            values[name] = _content_ref_data(values[name])
    try:
        return ResultSetEntry(**values)
    except TypeError:
        raise EvaluationError("invalid result-set entry") from None


def _entries(path: str) -> tuple[ResultSetEntry, ...]:
    raw = _json_file(path)
    if type(raw) is not list or len(raw) > 100000:
        raise EvaluationError("invalid result-set entries")
    return tuple(_entry(item) for item in raw)


def _schema_catalog() -> dict[str, tuple[str, object]]:
    catalog: dict[str, tuple[str, object]] = {
        SCHEMA: ("skill-evaluation-v1.schema.json", json_schema()),
        CAMPAIGN_SCHEMA: ("campaign-spec-v1.schema.json", campaign_json_schema()),
        RESOLVED_CAMPAIGN_SCHEMA: ("resolved-campaign-v1.schema.json", resolved_campaign_json_schema()),
    }
    for record_type in RECORD_TYPES | ANALYSIS_TYPES:
        schema = publication_json_schema(record_type)
        filename = schema["title"].split("/")[0].split(".")[-1] + "-v1.schema.json"
        catalog[schema["title"]] = (filename, schema)
    return catalog


def _result(command: str, **values: object) -> dict[str, object]:
    return {
        "schema": CLI_SCHEMA, "command": command, "status": "complete",
        **values, "authority_effect": "none",
    }


def _capabilities() -> dict[str, object]:
    def unavailable() -> dict[str, object]:
        return {"available": False, "reason": "unsupported-offline"}
    return {
        "schema": CAPABILITIES_SCHEMA,
        "status": "complete",
        "offline": {
            "available": True,
            "operations": [
                "schema", "suite-validation", "campaign-compilation",
                "result-set-sealing", "manifest-projection", "blind-analysis",
                "grading", "rescoring", "comparison", "conformance",
            ],
        },
        "workspace": unavailable(), "execution": unavailable(),
        "serving": unavailable(), "connectors": unavailable(),
        "network_effects": unavailable(), "authority_effect": "none",
    }


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="agent-evals", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser("schema", add_help=False)
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    schema_commands.add_parser("list", add_help=False)
    show = schema_commands.add_parser("show", add_help=False)
    show.add_argument("--name", required=True)
    commands.add_parser("capabilities", add_help=False)

    suite = commands.add_parser("suite", add_help=False)
    suite_commands = suite.add_subparsers(dest="suite_command", required=True)
    validate_suite = suite_commands.add_parser("validate", add_help=False)
    validate_suite.add_argument("--input", action="append", required=True)

    campaign = commands.add_parser("campaign", add_help=False)
    campaign_commands = campaign.add_subparsers(dest="campaign_command", required=True)
    compile_command = campaign_commands.add_parser("compile", add_help=False)
    compile_command.add_argument("--input", required=True)

    result_set = commands.add_parser("result-set", add_help=False)
    result_commands = result_set.add_subparsers(dest="result_command", required=True)
    validate_result = result_commands.add_parser("validate", add_help=False)
    validate_result.add_argument("--store", required=True)
    validate_result.add_argument("--campaign", required=True)
    validate_result.add_argument("--ref", required=True)
    seal = result_commands.add_parser("seal", add_help=False)
    seal.add_argument("--store", required=True)
    seal.add_argument("--campaign", required=True)
    seal.add_argument("--entries", required=True)
    seal.add_argument("--predecessor")

    manifest = commands.add_parser("manifest", add_help=False)
    manifest_commands = manifest.add_subparsers(dest="manifest_command", required=True)
    validate_manifest = manifest_commands.add_parser("validate", add_help=False)
    validate_manifest.add_argument("--input", required=True)
    write_manifest = manifest_commands.add_parser("write", add_help=False)
    write_manifest.add_argument("--store", required=True)
    write_manifest.add_argument("--campaign", required=True)
    write_manifest.add_argument("--result-set", required=True)
    write_manifest.add_argument("--retention-policy-sha256", required=True)

    analysis = commands.add_parser("analysis", add_help=False)
    analysis_commands = analysis.add_subparsers(dest="analysis_command", required=True)
    packet = analysis_commands.add_parser("packet", add_help=False)
    packet.add_argument("--store", required=True)
    packet.add_argument("--campaign", required=True)
    packet.add_argument("--result-set", required=True)
    packet.add_argument("--spec", required=True)
    validate_analysis = analysis_commands.add_parser("validate", add_help=False)
    validate_analysis.add_argument("--input", required=True)
    validate_analysis.add_argument("--store")
    validate_analysis.add_argument("--campaign")
    validate_analysis.add_argument("--mapping")
    unblind_command = analysis_commands.add_parser("unblind", add_help=False)
    unblind_command.add_argument("--store", required=True)
    unblind_command.add_argument("--campaign", required=True)
    unblind_command.add_argument("--result", required=True)
    unblind_command.add_argument("--mapping", required=True)

    for name in ("grade", "rescore"):
        grade = commands.add_parser(name, add_help=False)
        grade.add_argument("--case", required=True)
        grade.add_argument("--run", required=True)
        grade.add_argument("--rubric", required=True)
        grade.add_argument("--assessment")
        grade.add_argument("--human-review")

    compare_command = commands.add_parser("compare", add_help=False)
    compare_command.add_argument("--case", required=True)
    compare_command.add_argument("--variant", required=True)
    compare_command.add_argument("--rubric", required=True)
    compare_command.add_argument("--run", action="append", required=True)
    compare_command.add_argument("--score", action="append", required=True)
    parse = commands.add_parser("parse", add_help=False)
    parse.add_argument("record")
    commands.add_parser("conformance", add_help=False)
    return parser


def _suite_validate(paths: list[str]) -> dict[str, object]:
    if len(paths) > MAX_ITEMS:
        raise EvaluationError("suite input count bound")
    records: list[object] = []
    for path in paths:
        raw = _json_file(path)
        items = raw if type(raw) is list else [raw]
        if not items:
            raise EvaluationError("empty suite input")
        for item in items:
            records.append(decode(json.dumps(item, sort_keys=True, separators=(",", ":"))))
    identities: dict[tuple[type, str], object] = {}
    cases: dict[str, Case] = {}
    rubrics: list[Rubric] = []
    counts: dict[str, int] = {}
    for record in records:
        counts[type(record).__name__] = counts.get(type(record).__name__, 0) + 1
        if isinstance(record, Case):
            key = (Case, record.case_id)
            cases[digest(record)] = record
        elif isinstance(record, Variant):
            key = (Variant, record.variant_id)
        elif isinstance(record, Rubric):
            key = (Rubric, record.rubric_id)
            rubrics.append(record)
        else:
            key = (type(record), digest(record))
        if key in identities:
            raise EvaluationError("duplicate suite record identity")
        identities[key] = record
    if any(rubric.case_sha256 not in cases for rubric in rubrics):
        raise EvaluationError("unbound suite rubric")
    return _result(
        "suite.validate", record_count=len(records), counts=dict(sorted(counts.items())),
        content_sha256=digest([json.loads(encode(item)) for item in records]),
    )


def _analysis_validate(args: argparse.Namespace) -> dict[str, object]:
    result = publication_decode(_read_text(args.input))
    if type(result) not in ANALYSIS_TYPES:
        raise EvaluationError("unexpected analysis record")
    supplied = (args.store, args.campaign, args.mapping)
    if any(supplied) and not all(supplied):
        raise EvaluationError("analysis binding selectors must be complete")
    if all(supplied):
        if type(result) is not AnalysisResult:
            raise EvaluationError("binding validation requires an analysis result")
        store = ObjectStore(args.store)
        campaign = _campaign(args.campaign, ResolvedCampaign)
        mapping_ref = _content_ref(args.mapping)
        _, packet = verify_mapping(store, mapping_ref, campaign)
        _verify_result(store, result, mapping_ref, packet)
    return _result(
        "analysis.validate", kind=type(result).__name__,
        content_sha256=digest(asdict(result)),
        bindings="verified" if all(supplied) else "not-requested",
    )


def _conformance() -> dict[str, object]:
    catalog = _schema_catalog()
    package = resources.files("agent_evals").joinpath("resources")
    for _, (filename, generated) in catalog.items():
        try:
            packaged = json.loads(package.joinpath(filename).read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeError, ValueError):
            raise EvaluationError("packaged schema unavailable") from None
        if packaged != generated:
            raise EvaluationError("packaged schema mismatch")

    # This checks package-local imports and never discovers a checkout.
    forbidden = ("codex", "workspace", "connector", "execution")
    checked_modules = []
    pending = [(resources.files("agent_evals"), "")]
    while pending:
        item, relative = pending.pop()
        if item.is_dir():
            pending.extend(
                (child, f"{relative}/{child.name}" if relative else child.name)
                for child in item.iterdir()
            )
            continue
        if not item.name.endswith(".py"):
            continue
        try:
            tree = ast.parse(item.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            raise EvaluationError("portable module unavailable") from None
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module]
            if any(module.split(".")[0] in forbidden for module in imported):
                raise EvaluationError("forbidden portable import")
        checked_modules.append(relative)
    checked_modules.sort()
    return {
        "schema": CONFORMANCE_SCHEMA, "status": "complete",
        "checks": {
            "packaged_schemas": {"status": "passed", "count": len(catalog)},
            "portable_import_boundary": {"status": "passed", "modules": checked_modules},
            "offline_capabilities": {"status": "passed"},
        },
        "authority_effect": "none",
    }


def _run(args: argparse.Namespace) -> object:
    if args.command == "schema":
        catalog = _schema_catalog()
        if args.schema_command == "list":
            return _result("schema.list", schemas=sorted(catalog))
        if args.name not in catalog:
            raise EvaluationError("unknown schema selector")
        return catalog[args.name][1]
    if args.command == "capabilities":
        return _capabilities()
    if args.command == "suite":
        return _suite_validate(args.input)
    if args.command == "campaign":
        spec = _campaign(args.input, CampaignSpec)
        return json.loads(campaign_encode(compile_campaign(spec)))
    if args.command == "result-set":
        store = ObjectStore(args.store)
        campaign = _campaign(args.campaign, ResolvedCampaign)
        if args.result_command == "validate":
            ref = _content_ref(args.ref)
            value = verify_result_set(store, ref, campaign)
            return _result(
                "result-set.validate", ref=asdict(ref), revision=value.revision,
                entries=len(value.entries),
            )
        predecessor = _content_ref(args.predecessor) if args.predecessor else None
        ref = seal_result_set(store, campaign, _entries(args.entries), predecessor=predecessor)
        return _result("result-set.seal", ref=asdict(ref))
    if args.command == "manifest":
        if args.manifest_command == "validate":
            manifest = _publication(args.input, PublishedResultManifest)
            return _result(
                "manifest.validate", content_sha256=digest(asdict(manifest)),
                summaries=len(manifest.summaries),
            )
        store = ObjectStore(args.store)
        campaign = _campaign(args.campaign, ResolvedCampaign)
        ref = publish_manifest(
            store, _content_ref(args.result_set), campaign,
            retention_policy_sha256=args.retention_policy_sha256,
        )
        return _result("manifest.write", ref=asdict(ref))
    if args.command == "analysis":
        if args.analysis_command == "validate":
            return _analysis_validate(args)
        store = ObjectStore(args.store)
        campaign = _campaign(args.campaign, ResolvedCampaign)
        if args.analysis_command == "packet":
            spec = _publication(args.spec, AnalysisSpec)
            packet_ref, mapping_ref = blind_result_set(
                store, _content_ref(args.result_set), campaign, spec,
            )
            return _result(
                "analysis.packet", packet_ref=asdict(packet_ref),
                mapping_ref=asdict(mapping_ref),
            )
        comparison_ref = unblind(
            store, _content_ref(args.result), _content_ref(args.mapping), campaign,
        )
        comparison = store.read_record(comparison_ref)
        if type(comparison) is not UnblindedComparison:
            raise EvaluationError("unexpected comparison record")
        return _result("analysis.unblind", ref=asdict(comparison_ref))
    if args.command in ("grade", "rescore"):
        case = _core_record(args.case, Case)
        run = _core_record(args.run, Run)
        rubric = _core_record(args.rubric, Rubric)
        assessment = _core_record(args.assessment, Assessment) if args.assessment else None
        human = _core_record(args.human_review, HumanReview) if args.human_review else None
        return json.loads(encode(score_run(case, run, rubric, assessment=assessment, human_review=human)))
    if args.command == "compare":
        if len(args.run) != 3 or len(args.score) != 3:
            raise EvaluationError("comparison needs exactly three runs and scores")
        return compare(
            _core_record(args.case, Case), _core_record(args.variant, Variant),
            _core_record(args.rubric, Rubric),
            tuple(_core_record(path, Run) for path in args.run),
            tuple(_core_record(path, Score) for path in args.score),
        )
    if args.command == "parse":
        return json.loads(encode(decode(_read_text(args.record))))
    if args.command == "conformance":
        return _conformance()
    raise EvaluationError("unknown command")


def _emit(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Preserve the original schema command while making list/show explicit.
    if arguments == ["schema"]:
        arguments = ["schema", "show", "--name", SCHEMA]
    if arguments and arguments[0] in {"workspace", "execute", "execution", "serve", "connector"}:
        failure = {
            "schema": CLI_SCHEMA, "status": "unavailable",
            "reason": "unsupported-offline-capability", "authority_effect": "none",
        }
        _emit(failure)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 3
    try:
        args = _parser().parse_args(arguments)
        _emit(_run(args))
        return 0
    except Exception:
        failure = {
            "schema": CLI_SCHEMA, "status": "invalid",
            "reason": "input-or-binding-error", "authority_effect": "none",
        }
        _emit(failure)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2
