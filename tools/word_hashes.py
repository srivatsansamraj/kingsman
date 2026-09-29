"""The Word file for each example posting that gets a page, written to a folder, and each file's SHA-256.

  python tools/word_hashes.py local/checks/word-now > local/checks/word-now.txt

Two runs of the same code give byte-identical files (the writer pins every date and identifier), so a change meant to
leave behaviour alone must leave these hashes alone. `tools/check.sh` runs it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from tailor_engine import settings
from tailor_engine.library import Library
from tailor_engine.reading import store
from tailor_engine.records import Posting
from tailor_engine.rendering.document import document_from_page
from tailor_engine.rendering.word import write_tailored_docx
from tailor_engine.selection.page import build_page

EXAMPLES = settings.REPOSITORY_ROOT / "examples"
LIBRARY = EXAMPLES / "library"
DATA = EXAMPLES / "data"
# Every example posting: each gets a page, the non-technical control too (its match is low, but over the refusal floor).
POSTINGS = (
    "paste-89f5dd84",
    "paste-87b68035",
    "paste-3d814d16",
    "paste-9725cefb",
    "paste-afdfe55e",
    "paste-12648881",
    "paste-f945f360",
    "paste-f1f45dad",
)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The Word files for the example postings, and their hashes.")
    parser.add_argument("folder", type=Path)
    output: Path = parser.parse_args(arguments).folder
    output.mkdir(parents=True, exist_ok=True)
    library = Library.load(LIBRARY, as_of=dt.date(2026, 9, 28))
    for posting_id in POSTINGS:
        posting: Posting = json.loads((DATA / "postings" / f"{posting_id}.json").read_text(encoding="utf-8"))
        reading = store.load_reading(posting["text"], posting_id, DATA / "readings")
        mapping = store.load_mapping(posting["text"], DATA / "mappings")
        if reading is None or mapping is None:
            raise SystemExit(f"{posting_id}: the saved reading or mapping is missing")
        page = build_page(posting, reading, library, mapping["requirements"])
        target = output / f"{posting_id}.docx"
        write_tailored_docx(LIBRARY / "resume-template.docx", target, document_from_page(page), overwrite=True)
        print(posting_id, hashlib.sha256(target.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
