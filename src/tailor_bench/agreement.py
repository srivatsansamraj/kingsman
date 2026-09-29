"""How closely the engine's pages agree with the advisors' pages.

Per posting and advisor: the Jaccard overlap of the two pages' projects, and, for each project both chose,
the Jaccard overlap of their bullets. Refused postings have no page and are counted apart. The advisors'
agreement with each other is the ceiling to read these numbers against.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from typing import TypedDict

from tailor_engine.rendering.judge_input import PageIds

from .dataset import ADVISORS, advisor_page

# Projects split in the library after the advisors labelled their pages: their bullets are not comparable, their
# project choice still is. None in this library.
PROJECTS_WITHOUT_COMPARABLE_BULLETS: frozenset[str] = frozenset()


def jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left | right else 1.0


def compare(page: PageIds, labelled_page: PageIds) -> tuple[float, list[float]]:
    """(project overlap, bullet overlap for each project both pages chose)."""
    mine, theirs = dict(page), dict(labelled_page)
    shared = [project for project in mine if project in theirs and project not in PROJECTS_WITHOUT_COMPARABLE_BULLETS]
    return jaccard(set(mine), set(theirs)), [jaccard(set(mine[project]), set(theirs[project])) for project in shared]


class Agreement(TypedDict):
    projects: float | None  # mean project overlap with the advisors; None with no page built
    bullets: float | None  # mean bullet overlap, over projects both chose
    pages: int
    refused: int


def summarise(
    pages: dict[str, PageIds | None], advisor_page_of: Callable[[str, str], PageIds] = advisor_page
) -> Agreement:
    """Agreement over these postings; a refused posting's page is None.

    `advisor_page_of(posting id, advisor)` gives an advisor's page; by default it reads the label files.
    """
    projects: list[float] = []
    bullets: list[float] = []
    refused = 0
    for posting_id, page in pages.items():
        if page is None:
            refused += 1
            continue
        for advisor in ADVISORS:
            project_overlap, bullet_overlaps = compare(page, advisor_page_of(posting_id, advisor))
            projects.append(project_overlap)
            bullets += bullet_overlaps
    return {
        "projects": round(statistics.mean(projects), 3) if projects else None,
        "bullets": round(statistics.mean(bullets), 3) if bullets else None,
        "pages": len(pages) - refused,
        "refused": refused,
    }
