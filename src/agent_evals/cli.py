"""Command-line access to portable parsing, schema, and grading operations."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys

from .core import (
    MAX_BYTES, Assessment, Case, EvaluationError, HumanReview, Rubric, Run,
    SCHEMA, decode, encode, json_schema, score_run,
)


def _read_record(path: str) -> object:
    """Read one regular file from one descriptor, bounded across size changes."""
    required = (getattr(os, "O_NOFOLLOW", None), getattr(os, "O_NONBLOCK", None))
    if any(type(flag) is not int or flag == 0 for flag in required):
        raise EvaluationError("safe record reader unavailable")
    flags = os.O_RDONLY | required[0] | required[1] | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvaluationError("unreadable record file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise EvaluationError("unsafe record file")
        chunks = []
        remaining = MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_BYTES:
            raise EvaluationError("record byte bound")
        return decode(raw.decode("utf-8"))
    except EvaluationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise EvaluationError("unreadable record file") from exc
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            os.close(descriptor)
        except OSError as exc:
            if not active_error:
                raise EvaluationError("unreadable record file") from exc


def _typed_record(path: str, record_type: type) -> object:
    record = _read_record(path)
    if type(record) is not record_type:
        raise EvaluationError("unexpected record kind")
    return record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-evals",
        description="Inspect and grade public-safe records without executing an agent.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="emit the codex.skill-evaluation/v1 JSON Schema")
    parse = commands.add_parser("parse", help="validate and normalize one record")
    parse.add_argument("record")
    grade = commands.add_parser("grade", aliases=["rescore"], help="grade preserved evidence")
    grade.add_argument("--case", required=True)
    grade.add_argument("--run", required=True)
    grade.add_argument("--rubric", required=True)
    grade.add_argument("--assessment")
    grade.add_argument("--human-review")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(json_schema(), indent=2, sort_keys=True))
        elif args.command == "parse":
            print(encode(_read_record(args.record)), end="")
        else:
            case = _typed_record(args.case, Case)
            run = _typed_record(args.run, Run)
            rubric = _typed_record(args.rubric, Rubric)
            assessment = _typed_record(args.assessment, Assessment) if args.assessment else None
            human_review = _typed_record(args.human_review, HumanReview) if args.human_review else None
            print(encode(score_run(case, run, rubric, assessment=assessment, human_review=human_review)), end="")
        return 0
    except (EvaluationError, TypeError, AttributeError):
        print(json.dumps({
            "schema": SCHEMA,
            "status": "invalid",
            "reason": "input-or-binding-error",
            "authority_effect": "none",
        }, sort_keys=True), file=sys.stderr)
        return 2
