"""Reading one library TOML file and checking its records' fields.

Every library file is TOML, read by Python's own `tomllib`, so a syntax error is reported with its line.
Each record type then declares its fields; a record that is not a table, a missing required field, an
unknown field or a value of the wrong type is an error naming the file and the record, and `Library.load`
refuses the library.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, NamedTuple

TOMLTable = dict[str, Any]


class StringList:
    """In a schema: a list whose items are all strings."""


FieldTypes = dict[str, type[Any]]


class Schema(NamedTuple):
    required: FieldTypes
    optional: FieldTypes


class LibraryError(ValueError):
    """The library cannot be used as written; the message names every problem found."""


def read_toml(path: Path) -> TOMLTable:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise LibraryError(f"{path.name}: {error}") from error


def field_problems(where: str, record: object, schema: Schema) -> list[str]:
    """What is wrong with `record`'s fields; [] when it can be read.

    An int is accepted where a float is expected, since TOML writes 1.0 and 1 differently.
    """
    if not isinstance(record, dict):
        return [f"{where}: should be a table, found {type(record).__name__} {record!r}"]
    problems = [f"{where}: missing '{name}'" for name in schema.required if name not in record]
    for name, value in record.items():
        expected_type = schema.required.get(name, schema.optional.get(name))
        if expected_type is None:
            problems.append(f"{where}: unknown field '{name}'")
        elif expected_type is StringList:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                problems.append(f"{where}: '{name}' should be a list of strings")
        elif not (
            isinstance(value, expected_type)
            or (expected_type is float and isinstance(value, int) and not isinstance(value, bool))
        ):
            problems.append(f"{where}: '{name}' should be {expected_type.__name__}, found {type(value).__name__}")
    return problems


def tag_problems(where: str, tags: object, allowed: set[str]) -> list[str]:
    """What is wrong with a record's topic tags: each must be in the vocabulary and carry a number."""
    if not isinstance(tags, dict):
        return [f"{where}: tags should be a table of tag = weight, found {type(tags).__name__}"]
    problems = []
    for tag, weight in tags.items():
        if tag not in allowed:
            problems.append(f"{where}: tag '{tag}' is not in the vocabulary")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            problems.append(f"{where}: tag '{tag}' needs a number")
    return problems


def records_of(table: TOMLTable, name: str) -> list[Any]:
    """The array of tables `name`, or [] when it is missing or not a list.

    A value that is not a list is a problem the file's own field check reports.
    """
    records = table.get(name, [])
    return records if isinstance(records, list) else []


def id_of(record: object, key: str = "id") -> str:
    """A record's id for messages, or "?" when it has none."""
    return str(record.get(key, "?")) if isinstance(record, dict) else "?"


def raise_if_problems(file_name: str, problems: list[str]) -> None:
    if problems:
        raise LibraryError(f"{file_name} has problems:\n  " + "\n  ".join(problems))
