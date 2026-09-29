"""The benchmark: the user's postings in four bands, each with two advisors' pages and a saved reading.

  benchmark/index.json            [{"id", "board", "band", "title", "url"}]; band is "lane:...", "adjacent:...",
                                  "distant:..." or "control:..." (how far the role sits from the candidate's)
  benchmark/postings/<id>.json    the posting as fetched
  benchmark/labels/<id>.<advisor>.json
                                  each advisor's page for it: {"page": [{"project", "bullets"}], ...}
  benchmark/readings/, mappings/  the saved model readings the benchmark numbers are built on
  benchmark/choices/              call 3's saved answers, for --hybrid
  benchmark/runs/                 each benchmark run's record, and each timed run's folder

The folder is benchmark/ under the repository root, or the one TAILOR_BENCHMARK names. The user's own benchmark is
not in version control; examples/benchmark/ is an illustrative one for the example library (its labels were
written by hand to show the format). ONBOARDING.md, section 14.2, says how to build one. Postings used to accept
and reject changes give development-set numbers, not estimates for unseen postings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tailor_engine import settings
from tailor_engine.reading import store
from tailor_engine.records import CapabilityMapping, Posting, ProjectChoice, Reading
from tailor_engine.rendering.judge_input import PageIds

BENCHMARK_FOLDER = settings.BENCHMARK
READINGS = BENCHMARK_FOLDER / "readings"
MAPPINGS = BENCHMARK_FOLDER / "mappings"
CHOICES = BENCHMARK_FOLDER / "choices"  # call 3's answers, for hybrid mode
RUNS = BENCHMARK_FOLDER / "runs"
# The files a run leaves in its folder under RUNS.
FACTS_FILE, PAGE_FILE, JUDGE_FILE, TIMINGS_FILE, PAGE_TEXT_FILE = (
    "facts.json",
    "page.json",
    "judge.json",
    "timings.json",
    "page.txt",
)
ADVISORS = ("labeller-1", "labeller-2")
BANDS = ("lane", "adjacent", "distant", "control")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def index() -> list[dict[str, str]]:
    """The benchmark's postings. Stops with a message naming the folder when it has no index."""
    path = BENCHMARK_FOLDER / "index.json"
    if not path.exists():
        raise SystemExit(
            f"no benchmark: {path} is missing. Build one as ONBOARDING.md, section 14.2, says, or run the example "
            "one with TAILOR_BENCHMARK=examples/benchmark"
        )
    entries: list[dict[str, str]] = _read(path)
    return entries


def band_of(entry: dict[str, str]) -> str:
    """The posting's band, the part of its index label before any ":"."""
    band = entry["band"].split(":")[0]
    if band not in BANDS:
        raise ValueError(f"{entry['id']}: band '{band}' is not one of {BANDS}")
    return band


def posting(posting_id: str) -> Posting:
    found: Posting = _read(BENCHMARK_FOLDER / "postings" / f"{posting_id}.json")
    return found


def reading(posting_text: str, posting_id: str) -> Reading | None:
    return store.load_reading(posting_text, posting_id, READINGS)


def mapping(posting_text: str) -> CapabilityMapping | None:
    return store.load_mapping(posting_text, MAPPINGS)


def choice(posting_text: str) -> ProjectChoice | None:
    return store.load_choice(posting_text, CHOICES)


def run_data(label: str) -> Path:
    """Where a timed run keeps its posting, reading, mapping and project choice by default, apart from data/."""
    return RUNS / label / "data"


def advisor_page(posting_id: str, advisor: str) -> PageIds:
    """An advisor's page for a posting.

    A label file without its page or a project without its bullets raises, so a malformed label cannot count as an empty
    page.
    """
    data = _read(BENCHMARK_FOLDER / "labels" / f"{posting_id}.{advisor}.json")
    return [(str(item["project"]), [str(bullet) for bullet in item["bullets"]]) for item in data["page"]]
