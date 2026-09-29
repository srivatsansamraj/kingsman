"""Reading `skills.toml` and `education.toml`: the sections of the page outside the projects.

skills.toml     [[row]] a candidate row on the page: the titles it may appear under and the skills it may
                list, each with topic tags; `prior`, how much the row is worth on any page; `fixed`, shown
                on every page as written
education.toml  [[degree]] in page order: institution, award, dates, a fixed line, evidence words,
                other names for the degree, and courses with topic tags. Courses are chosen per
                posting; the rest is fixed.

Row and degree ids are unique within their file.
"""

from __future__ import annotations

from pathlib import Path

from ..records import Bullet, Degree, SkillRow, TaggedItem
from ..text_search import join_texts
from .records_file import (
    Schema,
    StringList,
    TOMLTable,
    field_problems,
    id_of,
    raise_if_problems,
    read_toml,
    records_of,
    tag_problems,
)

SKILLS_FILE = "skills.toml"
EDUCATION_FILE = "education.toml"
# The parents of section entries (skills, row titles, courses, degrees). They are not projects and have no
# entry in `Library.projects`; the posting's word-based demand reads only their tags and evidence, for the skill-row
# titles, the skills that fill a row and the course order.
SKILL_PARENT, TITLE_PARENT, COURSE_PARENT, DEGREE_PARENT = "S", "T", "C", "D"
# The topic a degree's evidence words point the word-based demand at. education.toml gives a degree no tags of its own,
# so every degree carries this one, and `Library.load` refuses a vocabulary without it.
DEGREE_TAG = "research"
SKILLS_FILE_FIELDS = Schema({"row": list}, {"about": str, "appendix": list})
EDUCATION_FILE_FIELDS = Schema({"degree": list}, {"about": str})
ROW_FIELDS = Schema(
    {"id": str},
    {
        "prior": float,
        "fixed": bool,
        "titles": list,
        "members": list,
        # For people reading the file; the engine does not use them.
        "notes": list,
    },
)
DEGREE_FIELDS = Schema(
    {"id": str, "institution": str, "award": str},
    {
        "dates": str,
        "evidence": StringList,
        "names": StringList,
        "fixed": str,
        "courses": list,
        # For people reading the file; the engine does not use them.
        "note": str,
        "notes": list,
    },
)
ITEM_FIELDS = Schema({"text": str, "tags": dict}, {})


def _tagged_items(where: str, items: list[TOMLTable], tags: set[str]) -> tuple[tuple[TaggedItem, ...], list[str]]:
    """(the items that are sound, the problems of the others)."""
    sound = []
    problems: list[str] = []
    for item in items:
        item_problems = field_problems(where, item, ITEM_FIELDS)
        if not item_problems:
            item_problems = tag_problems(f"{where} '{item['text']}'", item["tags"], tags)
        if item_problems:
            problems += item_problems
            continue
        sound.append(TaggedItem(str(item["text"]), {tag: float(weight) for tag, weight in item["tags"].items()}))
    return tuple(sound), problems


def load_skill_rows(library_directory: Path, tags: set[str]) -> dict[str, SkillRow]:
    """The skill rows in the file's order. Raises LibraryError naming every problem found."""
    document = read_toml(library_directory / SKILLS_FILE)
    problems = field_problems(SKILLS_FILE, document, SKILLS_FILE_FIELDS)
    rows = {}
    for row in records_of(document, "row"):
        where = f"{SKILLS_FILE} row {id_of(row)}"
        row_problems = field_problems(where, row, ROW_FIELDS)
        if row_problems:
            problems += row_problems
            continue
        if row["id"] in rows:
            problems.append(f"{where}: the id is used by another row")
            continue
        titles, title_problems = _tagged_items(f"{where} title", records_of(row, "titles"), tags)
        members, member_problems = _tagged_items(f"{where} member", records_of(row, "members"), tags)
        problems += title_problems + member_problems
        rows[row["id"]] = SkillRow(
            id=row["id"],
            titles=titles,
            members=members,
            prior=float(row.get("prior", 0.5)),
            always_shown=row.get("fixed", False),
        )
    raise_if_problems(SKILLS_FILE, problems)
    return rows


def load_degrees(library_directory: Path, tags: set[str]) -> dict[str, Degree]:
    """Degrees in the order the file lists them, which is the order they appear on the page."""
    document = read_toml(library_directory / EDUCATION_FILE)
    problems = field_problems(EDUCATION_FILE, document, EDUCATION_FILE_FIELDS)
    degrees = {}
    for degree in records_of(document, "degree"):
        where = f"{EDUCATION_FILE} degree {id_of(degree)}"
        degree_problems = field_problems(where, degree, DEGREE_FIELDS)
        if degree_problems:
            problems += degree_problems
            continue
        if degree["id"] in degrees:
            problems.append(f"{where}: the id is used by another degree")
            continue
        courses, course_problems = _tagged_items(f"{where} course", records_of(degree, "courses"), tags)
        problems += course_problems
        degrees[degree["id"]] = Degree(
            id=degree["id"],
            institution=degree["institution"],
            award=degree["award"],
            dates=degree.get("dates", ""),
            fixed_line=degree.get("fixed", ""),
            evidence=tuple(degree.get("evidence", [])),
            names=tuple(degree.get("names", [])),
            courses=courses,
        )
    raise_if_problems(EDUCATION_FILE, problems)
    return degrees


def section_entries(skill_rows: dict[str, SkillRow], degrees: dict[str, Degree]) -> list[Bullet]:
    """Skills, row titles, courses and degrees as `Bullet` records.

    They are never placed on a page; the posting's word-based demand reads their tags to learn which topics a posting's
    words point at, for the skill-row titles, the skills that fill a row and the course order.
    """
    bullets = []
    for row in skill_rows.values():
        for index, (text, tags) in enumerate(row.members):
            bullets.append(Bullet(f"{SKILL_PARENT}.{row.id}.{index}", SKILL_PARENT, text, tags, [text]))
        for index, (text, tags) in enumerate(row.titles):
            bullets.append(Bullet(f"{TITLE_PARENT}.{row.id}.{index}", TITLE_PARENT, text, tags, [text]))
    for degree in degrees.values():
        for index, (text, tags) in enumerate(degree.courses):
            bullets.append(Bullet(f"{COURSE_PARENT}.{degree.id}.{index}", COURSE_PARENT, text, tags, [text]))
        bullets.append(
            Bullet(
                f"{DEGREE_PARENT}.{degree.id}", DEGREE_PARENT, degree.award, {DEGREE_TAG: 1.0}, list(degree.evidence)
            )
        )
    return bullets


def always_shown_text(degrees: dict[str, Degree], skill_rows: dict[str, SkillRow]) -> str:
    """Everything on every page outside the projects: degrees, their fixed lines, and fixed skill rows.

    A requirement this text meets is met before any project is chosen.
    """
    parts: list[str] = []
    fixed_lines: list[str] = []
    for degree in degrees.values():
        parts += [degree.institution, degree.award]
        parts += list(degree.evidence) + list(degree.names)
        fixed_lines.append(degree.fixed_line)
    parts += fixed_lines
    for row in skill_rows.values():
        if row.always_shown:
            parts += [member for member, _tags in row.members]
    return join_texts(part for part in parts if part)
