"""The type checker over the source, the tests and the tools: mypy in strict mode, configured in pyproject.toml.

Skipped where mypy is not installed (a development dependency: `pip install -e .[dev]`).
"""

from __future__ import annotations

import importlib.util
import unittest

from tailor_engine import settings

HAVE_MYPY = importlib.util.find_spec("mypy") is not None


@unittest.skipUnless(HAVE_MYPY, "mypy is not installed")
class StrictTypes(unittest.TestCase):
    def test_source_tests_and_tools_pass_strict_checking(self) -> None:
        from mypy import api

        root = settings.REPOSITORY_ROOT
        report, errors, status = api.run(
            ["--config-file", str(root / "pyproject.toml"), str(root / "src"), str(root / "tests"), str(root / "tools")]
        )
        self.assertEqual(status, 0, report + errors)


if __name__ == "__main__":
    unittest.main()
