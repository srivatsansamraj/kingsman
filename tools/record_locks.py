"""Records the tests' behaviour locks again on the example library, for a change meant to alter pages.

  python tools/record_locks.py [--library examples/library] [--data examples/data] [--out tests/fixtures]

Writes, exactly as the tests read them:
  placements.json             the library's warnings and the length of the text outside the projects (BASELINE_AS_OF)
  example-pages.json          each posting's engine page: selected, page_order, reach, coverage, score; or its refusal
  example-documents.json      each page's skill rows and coursework (postings that get a page only)
  example-hybrid-pages.json   each posting's hybrid page from its saved call 3 answer; or its refusal
  jev-call2-request.json      call 2's Jev request for --request-posting, from its saved reading: any change to what
                              call 2 asks Jev fails `tests/test_jev.py` until this is recorded again
and prints each posting's engine match, whose projects its hybrid page holds, and its requirement count, for the ids the
tests name (`LOW_MATCH`, `HIGH_MATCH`, `IN_LIBRARY`). No model is called: every posting needs its saved reading,
mapping and a current call 3 answer. Read the differences in the fixtures before committing them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from tailor_engine.library import Library
from tailor_engine.reading import jev_mapping, store
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.records import Posting, Reading, Requirement
from tailor_engine.rendering.document import document_from_page
from tailor_engine.selection.page import Refused, build_hybrid_page, build_page
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

# The dates the tests pin (EXAMPLES_AS_OF and BASELINE_AS_OF in tests/test_selection_regression.py).
EXAMPLES_AS_OF = dt.date(2026, 9, 28)
BASELINE_AS_OF = dt.date(2026, 9, 21)
LOCKS = {
    "pages": "example-pages.json",
    "documents": "example-documents.json",
    "hybrid": "example-hybrid-pages.json",
    "placements": "placements.json",
}


def saved_inputs(data: Path, posting_id: str) -> tuple[Posting, Reading, list[Requirement]]:
    posting: Posting = json.loads((data / "postings" / f"{posting_id}.json").read_text(encoding="utf-8"))
    reading = store.load_reading(posting["text"], posting_id, data / "readings")
    mapping = store.load_mapping(posting["text"], data / "mappings")
    if reading is None or mapping is None:
        raise SystemExit(f"{posting_id}: no saved reading or mapping in {data}")
    return posting, reading, mapping["requirements"]


def record(library_folder: Path, data: Path, posting_ids: list[str]) -> dict[str, Any]:
    """Every lock, and a summary of each posting for the ids the tests name."""
    library = Library.load(library_folder, as_of=EXAMPLES_AS_OF)
    listing_hash = projects_hash(library)
    pages: dict[str, Any] = {}
    documents: dict[str, Any] = {}
    hybrid: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    for posting_id in posting_ids:
        posting, reading, mapping = saved_inputs(data, posting_id)
        engine_match: float | None = None
        try:
            page = build_page(posting, reading, library, mapping)
        except Refused as refusal:
            pages[posting_id] = {"refused": str(refusal)}
        else:
            pages[posting_id] = {
                "selected": page["selected"],
                "page_order": [entry["group"] for entry in page["page"]],
                "reach": page["reach"],
                "coverage": page["coverage"],
                "score": page["score"],
            }
            document = document_from_page(page)
            documents[posting_id] = {
                "skills": [[row.title, list(row.members)] for row in document.skill_rows],
                "coursework": [[line.degree_id, list(line.courses)] for line in document.coursework],
            }
            engine_match = page["coverage"]
        choice = store.load_choice(posting["text"], data / "choices")
        if choice is None or not store.choice_is_current(choice, listing_hash):
            raise SystemExit(f"{posting_id}: no current call 3 answer in {data / 'choices'}")
        try:
            hybrid_page = build_hybrid_page(posting, reading, library, mapping, choice)
        except Refused as refusal:
            hybrid[posting_id] = {"refused": str(refusal)}
        else:
            hybrid[posting_id] = {
                "projects_by": hybrid_page["projects_by"],
                "selected": hybrid_page["selected"],
                "page_order": [entry["group"] for entry in hybrid_page["page"]],
                "coverage": hybrid_page["coverage"],
                "engine_match": hybrid_page.get("engine_match"),
            }
        summary[posting_id] = {
            "engine_match": engine_match,
            "projects_by": hybrid[posting_id].get("projects_by"),
            "requirements": len(mapping),
        }
    baseline = Library.load(library_folder, as_of=BASELINE_AS_OF)
    placements = {
        "fixed_section_chars": len(baseline.always_shown_text),
        "library": {"validation_warnings": baseline.warnings},
    }
    return {"pages": pages, "documents": documents, "hybrid": hybrid, "placements": placements, "summary": summary}


def call2_request(library_folder: Path, data: Path, posting_id: str) -> dict[str, Any]:
    """Jev's call 2 request for one posting, as the engine builds it from the saved reading."""
    library = Library.load(library_folder)
    posting, reading, _mapping = saved_inputs(data, posting_id)
    requirements: list[Requirement] = [
        {"name": item["name"], "tokens": item["tokens"], "required": item["required"]}
        for item in reading["requirements"]
    ]
    bodies = jev_mapping.request_bodies(
        posting.get("title") or "", requirements, library.capability_vocabulary, library.capability_descriptions
    )
    about = (
        f"jev_mapping.request_bodies on example posting {posting_id}, its saved reading and the capability "
        "descriptions of examples/library; any change to call 2's Jev request fails tests/test_jev.py"
    )
    return {"about": about, "bodies": bodies}


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8", newline="\n")


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The tests' behaviour locks, recorded on the example library.")
    parser.add_argument("--library", type=Path, default=Path("examples/library"))
    parser.add_argument("--data", type=Path, default=Path("examples/data"))
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures"))
    parser.add_argument("--request-posting", default="paste-89f5dd84", help="the posting of call 2's request lock")
    options = parser.parse_args(arguments)
    data: Path = options.data
    posting_ids = sorted(path.stem for path in (data / "postings").glob("*.json"))
    recorded = record(options.library, data, posting_ids)
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)
    for kind, name in LOCKS.items():
        write(out / name, recorded[kind])
    write(out / "jev-call2-request.json", call2_request(options.library, data, options.request_posting))
    print(f"model_projects_below {DEFAULT_WEIGHTS.model_projects_below}")
    for posting_id, entry in recorded["summary"].items():
        print(posting_id, json.dumps(entry))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
