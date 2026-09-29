"""The formatter and the linter over the source, the tests and the tools: ruff, configured in pyproject.toml.

Skipped where ruff is not installed (a development dependency: `pip install -e .[dev]`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tailor_engine import settings

RUFF = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
HAVE_RUFF = shutil.which(RUFF) is not None


@unittest.skipUnless(HAVE_RUFF, "ruff is not installed")
class Style(unittest.TestCase):
    def run_ruff(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        root = settings.REPOSITORY_ROOT
        return subprocess.run(
            [RUFF, *arguments, str(root / "src"), str(root / "tests"), str(root / "tools")],
            capture_output=True,
            text=True,
            cwd=root,
        )

    def test_the_linter_finds_nothing(self) -> None:
        result = self.run_ruff("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_file_is_formatted(self) -> None:
        result = self.run_ruff("format", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
