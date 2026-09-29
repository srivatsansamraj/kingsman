"""Saved postings, readings, mappings and project choices, so each posting is read by the models once.

A model reads a posting differently every time (five runs gave five requirement lists), so selection runs
on a saved reading, never a fresh one. Readings, mappings and project choices are keyed on the first 16 hex characters
of the SHA-256 of the posting text: an edited posting is a new posting.

  postings   <posting id>.json
  readings   <posting id>-<text key>.json   {"id", "sha", "requirements", "keywords", "info"}
  mappings   <text key>.json                the record `capability_mapping.map_requirements` returns
  choices    <text key>.json                the record `project_choice.choose_projects` returns (call 3)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .. import settings
from ..records import CapabilityMapping, Posting, ProjectChoice, Reading, Requirement

T = TypeVar("T")

# On Windows a file open for reading cannot be renamed over, and a file being renamed over cannot be opened, for a
# moment: the dashboard reads pages, readings, mappings and choices on one thread while another replaces them, and
# either side failed. Replacing and reading a saved file try again this many times in all, waiting a little
# longer each time (0.5 s at most). Set by hand.
REPLACE_ATTEMPTS = 5
REPLACE_WAIT_SECONDS = 0.05


def retried(action: Callable[[], T]) -> T:
    """`action()`, tried again on PermissionError; the last attempt's error is raised."""
    for attempt in range(1, REPLACE_ATTEMPTS):
        try:
            return action()
        except PermissionError:
            time.sleep(REPLACE_WAIT_SECONDS * attempt)
    return action()


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path) -> Any:
    return json.loads(retried(lambda: path.read_text(encoding="utf-8"))) if path.exists() else None


def write_json(path: Path, data: object) -> Path:
    # Written beside the target and renamed over it: an interrupted write never leaves half a file, which
    # would stop every later read of it.
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    try:
        Path(temporary).write_text(json.dumps(data, indent=1), encoding="utf-8")
        retried(lambda: os.replace(temporary, path))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


# ─────────────────────────────────────────────────────────────
# Postings
# ─────────────────────────────────────────────────────────────


def save_posting(posting: Posting, directory: Path | None = None) -> Path:
    return write_json((directory or settings.POSTINGS) / f"{posting['id']}.json", posting)


def load_posting(posting_id: str, directory: Path | None = None) -> Posting | None:
    posting: Posting | None = _read_json((directory or settings.POSTINGS) / f"{posting_id}.json")
    return posting


# ─────────────────────────────────────────────────────────────
# Readings
# ─────────────────────────────────────────────────────────────


def reading_path(posting_id: str, text: str, directory: Path | None = None) -> Path:
    return (directory or settings.READINGS) / f"{posting_id}-{text_key(text)}.json"


def load_reading(text: str, posting_id: str | None = None, directory: Path | None = None) -> Reading | None:
    """The saved reading of this text, or None.

    The posting's own file if there is one, otherwise the first file for this text in name order (the same text saved
    under another id).
    """
    directory = directory or settings.READINGS
    if posting_id is not None:
        own: Reading | None = _read_json(reading_path(posting_id, text, directory))
        if own is not None:
            return own
    matches = sorted(directory.glob(f"*-{text_key(text)}.json"))
    first: Reading | None = _read_json(matches[0]) if matches else None
    return first


def save_reading(
    posting_id: str,
    text: str,
    requirements: list[Requirement],
    keywords: list[str],
    *,
    info: dict[str, Any] | None = None,
    directory: Path | None = None,
) -> Path:
    """`info` records which reader made it and what it cost."""
    record: Reading = {"id": posting_id, "sha": text_key(text), "requirements": requirements, "keywords": keywords}
    if info:
        record["info"] = info
    return write_json(reading_path(posting_id, text, directory), record)


# ─────────────────────────────────────────────────────────────
# Mappings
# ─────────────────────────────────────────────────────────────


# 2: each requirement carries a list of capabilities, any one of which meets it. 3: the mapping named the one or two
# domains the role is about; they are no longer asked for, and no code read them, so the format stayed 3 and
# mappings made with them stay current.
MAPPING_FORMAT = 3


def is_current(mapping: CapabilityMapping | None, vocabulary_hash: str, descriptions_hash: str) -> bool:
    """Whether a saved mapping was made under this capability vocabulary, in the current format.

    A renamed or moved capability makes it stale, and so does a mapping from before several capabilities per
    requirement. A mapping Jev made is stale too once the capability descriptions it was asked with have changed
    (`descriptions_hash`, the library's), or when it was made before they were sent; Opus is not sent them.
    Any model id starting "jev" is Jev's, the pinned version and the "jev-latest" of before it alike.
    """
    return (
        mapping is not None
        and mapping.get("vocabulary_sha256_16") == vocabulary_hash
        and mapping.get("format") == MAPPING_FORMAT
        and (
            not str(mapping.get("model") or "").startswith("jev")
            or mapping.get("descriptions_sha256_16") == descriptions_hash
        )
    )


def mapping_needs_call(mapping: CapabilityMapping | None, vocabulary_hash: str, descriptions_hash: str) -> bool:
    """Whether call 2 must be made again: no current mapping, or its answer failed the check.

    `is_current` alone does not ask for validity, since a page may still be built from a mapping that is not valid.
    """
    return not is_current(mapping, vocabulary_hash, descriptions_hash) or not (mapping or {}).get("valid")


def mapping_path(text: str, directory: Path | None = None) -> Path:
    return (directory or settings.MAPPINGS) / f"{text_key(text)}.json"


def load_mapping(text: str, directory: Path | None = None) -> CapabilityMapping | None:
    mapping: CapabilityMapping | None = _read_json(mapping_path(text, directory))
    return mapping


def save_mapping(text: str, record: CapabilityMapping, directory: Path | None = None) -> Path:
    return write_json(mapping_path(text, directory), record)


# ─────────────────────────────────────────────────────────────
# Project choices (call 3)
# ─────────────────────────────────────────────────────────────


CHOICE_FORMAT = 1


def choice_is_current(choice: ProjectChoice | None, projects_hash: str) -> bool:
    """Whether a saved choice was made from this project list, in the current format, and can be used.

    A project added, retitled or summarised again makes it stale.
    """
    return (
        choice is not None
        and choice.get("projects_sha256_16") == projects_hash
        and choice.get("format") == CHOICE_FORMAT
        and bool(choice.get("valid"))
    )


def choice_path(text: str, directory: Path | None = None) -> Path:
    return (directory or settings.CHOICES) / f"{text_key(text)}.json"


def load_choice(text: str, directory: Path | None = None) -> ProjectChoice | None:
    choice: ProjectChoice | None = _read_json(choice_path(text, directory))
    return choice


def save_choice(text: str, record: ProjectChoice, directory: Path | None = None) -> Path:
    return write_json(choice_path(text, directory), record)
