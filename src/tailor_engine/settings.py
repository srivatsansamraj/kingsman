"""Where the engine finds the candidate's library and keeps what it has read.

  library/         the candidate's authored material: projects and bullets, skills, education, the
                   capability vocabulary and map, the Word template; while it does not exist, the
                   fictional example library in examples/library/ is used
  data/postings/   postings read so far, one JSON file each
  data/readings/   each posting's saved requirement reading (model call 1)
  data/mappings/   each posting's saved capability mapping (model call 2)
  data/choices/    each posting's saved project choice (model call 3, hybrid mode)
  data/pages/      pages built from them: the dashboard's, and the command line's in data/pages/cli/
  benchmark/       the measurement tools' benchmark (`tailor_bench`); examples/benchmark/ is an illustrative one

Each location can be overridden by an environment variable, so an installed copy does not depend on this
source tree's layout. So can which model answers model calls 2 and 3 (`CLASSIFIER`, `CALL2`).
"""

from __future__ import annotations

import os
from pathlib import Path


def _path_from_environment(variable: str, default: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else default


def default_library(root: Path) -> Path:
    """The user's own library/ once it exists, else the example library that ships with the repository."""
    own = root / "library"
    return own if own.exists() else root / "examples" / "library"


REPOSITORY_ROOT = _path_from_environment("TAILOR_ROOT", Path(__file__).resolve().parents[2])
LIBRARY = _path_from_environment("TAILOR_LIBRARY", default_library(REPOSITORY_ROOT))
DATA = _path_from_environment("TAILOR_DATA", REPOSITORY_ROOT / "data")
POSTINGS = DATA / "postings"
READINGS = DATA / "readings"
MAPPINGS = DATA / "mappings"
CHOICES = DATA / "choices"
PAGES = DATA / "pages"
WORD_TEMPLATE = LIBRARY / "resume-template.docx"
# The measurement tools' benchmark (`tailor_bench`): the user's own, not in version control. The example one is
# examples/benchmark.
BENCHMARK = _path_from_environment("TAILOR_BENCHMARK", REPOSITORY_ROOT / "benchmark")

# Which model answers call 3 in the command line and the dashboard: "jev", TypeSafe's Jev with Opus as its fallback,
# or "opus", Opus alone. TAILOR_CLASSIFIER=opus forces Opus for calls 2 and 3 both.
CLASSIFIERS = ("jev", "opus")
CLASSIFIER = os.environ.get("TAILOR_CLASSIFIER") or "jev"
# Which model answers call 2 when the classifier is Jev: "opus", since Opus's mapping is the more accurate at the
# requirement level, or "jev", with Opus as its fallback. One of CLASSIFIERS. The command line's default (`read
# --call2`), and the dashboard's until its page mode is set (Accuracy is Opus, Speed is Jev).
CALL2 = os.environ.get("TAILOR_CALL2") or "opus"
