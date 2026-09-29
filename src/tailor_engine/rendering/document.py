"""The page as a renderer-neutral document: projects, skill rows and coursework, all authored text.

Built from a page result (`selection.page.build_page`) alone: the page records what it prints, so nothing is
chosen, written or rephrased here. Coursework follows the degrees in the order the library lists them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..layout import COURSES_MAX, COURSES_MIN, PAGE_PROJECTS, PAGE_SKILL_ROWS, SKILLS_PER_ROW
from ..records import PageResult


@dataclass(frozen=True)
class ProjectEntry:
    title: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class PrintedSkillRow:
    title: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class Coursework:
    degree_id: str
    courses: tuple[str, ...]


@dataclass(frozen=True)
class ResumeDocument:
    coursework: tuple[Coursework, ...]
    projects: tuple[ProjectEntry, ...]
    skill_rows: tuple[PrintedSkillRow, ...]


def _require_single_line(value: str, what: str) -> str:
    """The text stripped. Raises ValueError when it is empty or spans lines, which the template cannot hold."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{what} cannot be empty")
    if any(character in cleaned for character in "\r\n\t"):
        raise ValueError(f"{what} must be a single line")
    return cleaned


def validate_document(document: ResumeDocument) -> None:
    """Raise ValueError on anything the page cannot hold.

    The page holds up to six projects of at least one bullet (the line budget, not a per-project limit, decides how
    many), exactly five chosen skill rows of one to eight skills, and 8 to 11 courses per degree.
    """
    if not 1 <= len(document.projects) <= PAGE_PROJECTS:
        raise ValueError(f"the page holds 1 to {PAGE_PROJECTS} projects; received {len(document.projects)}")
    if len(document.skill_rows) != PAGE_SKILL_ROWS:
        raise ValueError(
            f"the page holds exactly {PAGE_SKILL_ROWS} chosen skill rows; received {len(document.skill_rows)}"
        )
    for entry in document.coursework:
        if not COURSES_MIN <= len(entry.courses) <= COURSES_MAX:
            raise ValueError(f"{entry.degree_id} coursework must contain {COURSES_MIN} to {COURSES_MAX} items")
        for index, course in enumerate(entry.courses):
            _require_single_line(course, f"{entry.degree_id} course {index + 1}")
    for index, project in enumerate(document.projects):
        _require_single_line(project.title.removesuffix(":"), f"project {index + 1} title")
        if not project.bullets:
            raise ValueError(f"project {index + 1} must contain at least one bullet")
        for bullet_index, bullet in enumerate(project.bullets):
            _require_single_line(bullet, f"project {index + 1} bullet {bullet_index + 1}")
    for index, row in enumerate(document.skill_rows):
        _require_single_line(row.title.removesuffix(":"), f"skill row {index + 1} title")
        if not 1 <= len(row.members) <= SKILLS_PER_ROW:
            raise ValueError(f"skill row {index + 1} must contain 1 to {SKILLS_PER_ROW} skills")
        for member_index, member in enumerate(row.members):
            _require_single_line(member, f"skill row {index + 1} skill {member_index + 1}")


def document_from_page(result: PageResult) -> ResumeDocument:
    """The document for a built page, as the page recorded it.

    Raises ValueError on a result without a page. A page saved before pages recorded their skill rows and coursework
    is refused too, to be built again with `page`, so the rows printed are always the rows it was scored with.
    """
    page = result.get("page")
    if not isinstance(page, list):
        raise ValueError("page result must contain a page list")
    if "skill_rows" not in result or "coursework" not in result:
        raise ValueError(
            "this page was saved before pages recorded their skill rows and coursework; build it again with `page`"
        )
    # Stripped here and checked once below, where a problem is named by its place ("project 2 bullet 3").
    document = ResumeDocument(
        coursework=tuple(
            Coursework(entry["degree"], tuple(str(course).strip() for course in entry["courses"]))
            for entry in result["coursework"]
        ),
        projects=tuple(
            ProjectEntry(
                title=str(project.get("title", "")).strip(),
                bullets=tuple(str(bullet).strip() for bullet in project.get("bullets", [])),
            )
            for project in page
        ),
        skill_rows=tuple(
            PrintedSkillRow(
                title=str(row["title"]).strip(),
                members=tuple(str(member).strip() for member in row["members"]),
            )
            for row in result["skill_rows"]
        ),
    )
    validate_document(document)
    return document
