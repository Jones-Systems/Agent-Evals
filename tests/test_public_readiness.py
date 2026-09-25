from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PublicReadinessTests(unittest.TestCase):
    def test_public_ci_uses_only_github_hosted_runners(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("runs-on: [self-hosted", workflow)
        self.assertEqual(
            workflow.count(
                "github.event.repository.private && 'jones-systems-vps' "
                "|| 'ubuntu-latest'"
            ),
            2,
        )
        self.assertNotIn("Jones-Systems/GitHub-Actions", workflow)
        for check_name in (
            "python / Linux / Python ${{ matrix.python-version }}",
            "python / Package",
        ):
            self.assertIn(check_name, workflow)

    def test_public_governance_files_are_present(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertTrue((ROOT / "CONTRIBUTING.md").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())


if __name__ == "__main__":
    unittest.main()
