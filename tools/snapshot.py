"""Everything the engine makes from saved inputs, in one JSON file, to check a change meant to leave behaviour alone.

  python tools/snapshot.py write local/checks/snapshot.json      before the change
  python tools/snapshot.py compare local/checks/snapshot.json    after it: each entry that differs

Covers the pages for the example postings (examples/data) on the example library, in capability mode and in hybrid
mode from call 3's saved answers, every field with floats exact; the Word document content of each page; the facts of
every example posting, and of each again with its board record dropped (the text-only path); and the loaded library.
The date is pinned, so project ages do not move between runs.
`tools/check.sh` runs it; the file it writes is kept out of git (`local/`), since it is retaken whenever a change is
meant to alter pages.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from tailor_engine import settings
from tailor_engine.library import Library
from tailor_engine.reading import store
from tailor_engine.reading.facts import posting_facts
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.records import PageResult, Posting, Reading, Requirement
from tailor_engine.rendering.document import document_from_page
from tailor_engine.selection.page import Refused, build_hybrid_page, build_page

ROOT = settings.REPOSITORY_ROOT
LIBRARY = ROOT / "examples" / "library"
DATA = ROOT / "examples" / "data"
AS_OF = dt.date(2026, 9, 28)
# Pages are keyed "<posting> capabilities" and "<posting> hybrid".
PAGE_KEY_SUFFIX = "capabilities"


def plain(value: object) -> Any:
    """`value` as JSON would carry it: records as dictionaries, sets as sorted lists, anything else as its text."""

    def convert(item: object) -> object:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return dataclasses.asdict(item)
        if isinstance(item, set | frozenset):
            return sorted((convert(member) if isinstance(member, frozenset) else member for member in item), key=str)
        return str(item)

    return json.loads(json.dumps(value, default=convert))


def saved_inputs(posting: Posting, text_id: str, directory: Path | None) -> tuple[Reading, list[Requirement]]:
    """A posting's saved reading and mapped requirements."""
    reading = store.load_reading(posting["text"], text_id, directory / "readings" if directory else None)
    mapping_record = store.load_mapping(posting["text"], directory / "mappings" if directory else None)
    if reading is None or mapping_record is None:
        raise SystemExit(f"{text_id}: the saved reading or mapping is missing")
    return reading, mapping_record["requirements"]


def page_and_document(build: Callable[[], PageResult]) -> tuple[Any, Any]:
    """The page `build` makes, and its Word document's content (None when the page is refused)."""
    try:
        result = build()
    except Refused as refusal:
        return {"refused": str(refusal)}, None
    return plain(result), plain(document_from_page(result))


def facts_or_error(posting: Posting) -> Any:
    # Some saved postings predate the `board` record; what the code does with them is checked too.
    try:
        return plain(posting_facts(posting))
    except Exception as error:  # noqa: BLE001
        return {"error": type(error).__name__}


def build() -> dict[str, Any]:
    library = Library.load(LIBRARY, as_of=AS_OF)
    listing_hash = projects_hash(library)
    pages: dict[str, Any] = {}
    documents: dict[str, Any] = {}
    for path in sorted((DATA / "postings").glob("*.json")):
        posting_id = path.stem
        posting: Posting = json.loads(path.read_text(encoding="utf-8"))
        reading, mapping = saved_inputs(posting, posting_id, DATA)
        key = f"{posting_id} {PAGE_KEY_SUFFIX}"
        pages[key], documents[key] = page_and_document(partial(build_page, posting, reading, library, mapping))
        choice = store.load_choice(posting["text"], DATA / "choices")
        if not store.choice_is_current(choice, listing_hash):
            raise SystemExit(f"{posting_id}: the saved project choice is missing or stale")
        key = f"{posting_id} hybrid"
        pages[key], documents[key] = page_and_document(
            partial(build_hybrid_page, posting, reading, library, mapping, choice)
        )
    postings: list[tuple[str, Posting]] = []
    for folder in (DATA / "postings",):
        for path in sorted(folder.glob("*.json")):
            postings.append((f"{folder.name}/{path.stem}", json.loads(path.read_text(encoding="utf-8"))))
    facts = {name: facts_or_error(posting) for name, posting in postings if "text" in posting}
    no_board: dict[str, Posting] = {name: {**posting, "board": {}} for name, posting in postings if "text" in posting}
    facts |= {f"{name} text only": facts_or_error(posting) for name, posting in no_board.items()}
    return {"pages": pages, "documents": documents, "facts": facts, "library": plain(vars(library))}


def compare(before: dict[str, Any], now: dict[str, Any]) -> int:
    """Print each entry of `now` that differs from `before`; the number of differences.

    A key a page gained since the baseline is counted apart (with how many pages have it not empty) and left out of
    the comparison; so are the facts of postings saved since. An entry the baseline has and now lacks is a difference.
    """
    added: dict[str, list[int]] = {}
    for key, page in now["pages"].items():
        old = before["pages"].get(key)
        if isinstance(old, dict) and isinstance(page, dict):
            for name in set(page) - set(old):
                counts = added.setdefault(name, [0, 0])
                counts[0] += 1
                counts[1] += bool(page[name])
            now["pages"][key] = {name: value for name, value in page.items() if name in old}
    for name, (pages_with, not_empty) in sorted(added.items()):
        print(f"pages: key '{name}' added on {pages_with} pages, not empty on {not_empty}")
    differences = 0
    for section in before:
        if section not in now:
            print(f"{section}: missing now")
            differences += 1
            continue
        new = sorted(set(now[section]) - set(before[section])) if section == "facts" else []
        if new:
            print(f"facts: {len(new)} entries for postings saved since the baseline ({', '.join(new[:6])})")
        for key in sorted(set(before[section]) | (set(now[section]) - set(new))):
            if before[section].get(key) != now[section].get(key):
                differences += 1
                print(f"{section} / {key}: differs")
    return differences


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The engine's output from saved inputs, written or compared.")
    parser.add_argument("action", choices=("write", "compare"))
    parser.add_argument("file", type=Path)
    options = parser.parse_args(arguments)
    target: Path = options.file
    now = build()
    if options.action == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(now, indent=1, sort_keys=True), encoding="utf-8")
        print({section: len(value) for section, value in now.items()})
        return 0
    differences = compare(json.loads(target.read_text(encoding="utf-8")), now)
    print("identical" if not differences else f"{differences} differences")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
