"""Reading `vocabulary.toml`: the topic tags a bullet, skill or course may carry, and which count as related.

[[tag]]        name (`security.appsec`, the part before the dot naming its family, or a bare name such as
               `research`), what it covers, which projects anchor it
[[adjacency]]  a pair of tags in different families that still count as close when topics are compared
"""

from __future__ import annotations

from pathlib import Path

from .records_file import Schema, field_problems, id_of, raise_if_problems, read_toml, records_of

FILE_NAME = "vocabulary.toml"
FILE_FIELDS = Schema(
    {"tag": list},
    {"about": str, "adjacency": list, "adjacency_notes": str, "appendix": list},
)
TAG_FIELDS = Schema({"name": str}, {"covers": str, "anchored_by": str})
ADJACENCY_FIELDS = Schema({"a": str, "b": str}, {})


def load_tag_vocabulary(library_directory: Path) -> tuple[set[str], set[frozenset[str]]]:
    """(the allowed tags, the pairs that count as related). Raises LibraryError."""
    document = read_toml(library_directory / FILE_NAME)
    problems = field_problems(FILE_NAME, document, FILE_FIELDS)
    tags = set()
    for tag in records_of(document, "tag"):
        where = f"{FILE_NAME} tag {id_of(tag, 'name')}"
        tag_record_problems = field_problems(where, tag, TAG_FIELDS)
        if tag_record_problems:
            problems += tag_record_problems
        elif tag["name"] in tags:
            problems.append(f"{where}: the tag is listed twice")
        else:
            tags.add(tag["name"])
    adjacency = set()
    for pair in records_of(document, "adjacency"):
        where = f"{FILE_NAME} adjacency {id_of(pair, 'a')} <-> {id_of(pair, 'b')}"
        pair_problems = field_problems(where, pair, ADJACENCY_FIELDS)
        if pair_problems:
            problems += pair_problems
            continue
        for side in (pair["a"], pair["b"]):
            if side not in tags:
                problems.append(f"{where}: '{side}' is not a tag")
        adjacency.add(frozenset((pair["a"], pair["b"])))
    raise_if_problems(FILE_NAME, problems)
    return tags, adjacency
