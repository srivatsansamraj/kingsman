"""Page geometry of the Word template, measured from rendered pages rather than assumed.

Calibrated on four generated resumes rendered to PDF. A paragraph's line count is
`ceil(characters / width)`, and with the widths below that predicted all 148 paragraphs of those
renders with no error. The page holds 56 lines; a 57th spills a trailing blank page.

Each section has a fixed allotment. Education and skills take theirs, and the projects section gets
what remains. There is no per-project minimum or maximum: the selector divides the projects allotment
however it scores best.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Protocol

from .records import Bullet, Project

PAGE_LINES = 56
HEADER_LINES = 3  # name, contact, a third header line
EDUCATION_LINES = 13  # heading, two degrees, two coursework lines, a fixed line
SKILLS_LINES = 11  # heading, skill rows, certifications
PROJECT_LINES = PAGE_LINES - HEADER_LINES - EDUCATION_LINES - SKILLS_LINES  # 29, heading included
PROJECT_BODY_LINES = PROJECT_LINES - 1  # 28 for titles and bullets
# The template's slots: at most six projects, and exactly five chosen skill rows above the fixed ones.
PAGE_PROJECTS = 6
PAGE_SKILL_ROWS = 5
# A chosen skill row holds up to eight skills.
SKILLS_PER_ROW = 8
# Each degree's coursework line holds 8 to 11 courses, a count chosen over a character budget.
COURSES_MIN = 8
COURSES_MAX = 11

# "course" was measured with the others; nothing reads it yet, since coursework lines are counted by
# course, not by width.
CHARS_PER_LINE = {"bullet": 126, "plain": 122, "course": 95}


def line_count(text: str, paragraph_style: str) -> int:
    """Rendered lines for one paragraph of the given kind."""
    return max(1, math.ceil(len(text.strip()) / CHARS_PER_LINE[paragraph_style]))


class TitledRow(Protocol):
    """A skills-section row as the page prints it: `Title: member, member`."""

    @property
    def title(self) -> str: ...

    @property
    def members(self) -> Sequence[str]: ...


def project_body_lines(bullets: Iterable[Bullet], projects: dict[str, Project]) -> int:
    """Lines the projects section needs for these bullets: one title per project plus every bullet."""
    page_bullets = list(bullets)
    project_ids = {bullet.parent for bullet in page_bullets}
    return sum(line_count(projects[project_id].title, "plain") for project_id in project_ids) + sum(
        line_count(bullet.text, "bullet") for bullet in page_bullets
    )


def skills_lines(rows: Iterable[TitledRow]) -> int:
    """Lines the skills section needs: its heading plus one or more lines per row."""
    return 1 + sum(line_count(f"{row.title}: {', '.join(row.members)}", "plain") for row in rows)
