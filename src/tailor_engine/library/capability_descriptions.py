"""Reading `capability-descriptions.toml`: what Jev's call 2 is told about each capability.

  ["<capability>"]   domain (as `capabilities.toml` gives it), what (the work it names), not_for (its nearest
                     neighbours, and where their asks belong), examples (written as a posting would ask)
  ["none of these"]  what, not_for, examples: the option for a requirement that names only a condition or asks for no
                     capability in the list

Every capability of the vocabulary has one table, under its own domain, and nothing else has one. Jev's call 2 sends
them all with each requirement (`reading/jev_mapping.py`); Opus's call 2 does not read them. A saved Jev mapping keeps
their hash, so an edited description makes it stale.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .records_file import LibraryError, Schema, StringList, field_problems, raise_if_problems, read_toml

FILE_NAME = "capability-descriptions.toml"
NONE = "none of these"
DESCRIPTION_FIELDS = Schema({"domain": str, "what": str, "not_for": str, "examples": StringList}, {})
NONE_FIELDS = Schema({"what": str, "not_for": str, "examples": StringList}, {})

# Capability (then "none of these") -> its description, each field in the order it is sent.
CapabilityDescriptions = dict[str, dict[str, Any]]


def load_capability_descriptions(library_directory: Path, vocabulary: dict[str, str]) -> CapabilityDescriptions:
    """Each capability's description in the vocabulary's order, then "none of these". Raises LibraryError.

    `vocabulary` is capability -> domain (`capabilities.load_capability_vocabulary`).
    """
    path = library_directory / FILE_NAME
    if not path.exists():
        raise LibraryError(f"{FILE_NAME} is missing: Jev's call 2 describes every capability of capabilities.toml")
    document = read_toml(path)
    problems: list[str] = []
    for name, entry in document.items():
        if name == "about" and isinstance(entry, str):
            continue  # the file's note, sent nowhere
        entry_problems = field_problems(
            f"{FILE_NAME} {name!r}", entry, NONE_FIELDS if name == NONE else DESCRIPTION_FIELDS
        )
        if entry_problems:
            problems += entry_problems
        elif name != NONE and name not in vocabulary:
            problems.append(f"{FILE_NAME}: {name!r} is not a capability in capabilities.toml")
        elif name != NONE and entry["domain"] != vocabulary[name]:
            problems.append(
                f"{FILE_NAME}: {name!r} is in '{entry['domain']}' here and in '{vocabulary[name]}' in capabilities.toml"
            )
    problems += [
        f"{FILE_NAME}: no description of {name!r} ({domain})"
        for name, domain in vocabulary.items()
        if name not in document
    ]
    if NONE not in document:
        problems.append(f"{FILE_NAME}: no {NONE!r} table")
    raise_if_problems(FILE_NAME, problems)
    fields = list(DESCRIPTION_FIELDS.required)
    described = {name: {field: document[name][field] for field in fields} for name in vocabulary}
    described[NONE] = {field: document[NONE][field] for field in NONE_FIELDS.required}
    return described


def descriptions_hash(descriptions: CapabilityDescriptions) -> str:
    """A fingerprint of what Jev's call 2 is told: a saved Jev mapping asked with other descriptions is stale.

    The file's `about` note is not part of it.
    """
    return hashlib.sha256(json.dumps(descriptions, sort_keys=True).encode("utf-8")).hexdigest()[:16]
