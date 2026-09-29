"""Reading `skill-capabilities.toml`: the capabilities each skill and each course shows.

[skills]   a skill's name, as a row lists it -> the capabilities it shows
[courses]  a course's name, as a degree lists it -> the capabilities it shows

Every name must be a skill of some row, or a course of some degree, and every capability must be in the vocabulary. A
skill or course with no entry is scored by its name alone, and the library warns about it; the fixed rows are shown as
written and need no entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from ..records import Degree, SkillRow
from .records_file import Schema, field_problems, raise_if_problems, read_toml

SECTION_CAPABILITIES_FILE = "skill-capabilities.toml"
FILE_FIELDS = Schema({"skills": dict, "courses": dict}, {"about": str})


class SectionCapabilities(NamedTuple):
    skills: dict[str, list[str]]  # skill name -> capabilities
    courses: dict[str, list[str]]  # course name -> capabilities


def load_section_capabilities(
    directory: Path, vocabulary: dict[str, str], skill_rows: dict[str, SkillRow], degrees: dict[str, Degree]
) -> tuple[SectionCapabilities, list[str]]:
    """(each skill's and course's capabilities, warnings). Raises LibraryError naming every problem in the file."""
    path = directory / SECTION_CAPABILITIES_FILE
    if not path.exists():
        return SectionCapabilities({}, {}), [
            f"{SECTION_CAPABILITIES_FILE} is missing: skills and courses score by name"
        ]
    document = read_toml(path)
    problems = field_problems(SECTION_CAPABILITIES_FILE, document, FILE_FIELDS)
    raise_if_problems(SECTION_CAPABILITIES_FILE, problems)
    scored_skills = {text for row in skill_rows.values() if not row.always_shown for text, _tags in row.members}
    courses = {text for degree in degrees.values() for text, _tags in degree.courses}
    sections: dict[str, dict[str, list[str]]] = {"skills": {}, "courses": {}}
    for section, known in (("skills", scored_skills), ("courses", courses)):
        for name, capabilities in document[section].items():
            where = f"{SECTION_CAPABILITIES_FILE} [{section}] '{name}'"
            if name not in known:
                problems.append(f"{where}: not a {section[:-1]} in the library")
            if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
                problems.append(f"{where}: should be a list of capability names")
                continue
            problems += [
                f"{where}: '{item}' is not in the capability vocabulary"
                for item in capabilities
                if item not in vocabulary
            ]
            sections[section][name] = list(capabilities)
    raise_if_problems(SECTION_CAPABILITIES_FILE, problems)
    warnings = [
        f"{SECTION_CAPABILITIES_FILE}: {kind} '{name}' has no entry and scores by its name alone"
        for kind, names, entries in (
            ("skill", scored_skills, sections["skills"]),
            ("course", courses, sections["courses"]),
        )
        for name in sorted(names - set(entries))
    ]
    return SectionCapabilities(sections["skills"], sections["courses"]), warnings
