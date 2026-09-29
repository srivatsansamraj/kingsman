"""Edits to a built page: bullets taken off and put on, as the judge proposes and the user accepts. No model call.

An edit is {"remove": [bullet ids on the page], "add": [bullet ids from the library], "why": "..."}. It can only move
bullets that already exist, so nothing is written that the library does not hold. An edit is checked with the
engine's own rule for a page (`selector.feasible`: the projects section's line budget, the most projects, no bullet
beside one it excludes); the selector itself does not run. Accepted edits are kept in the tracker and applied, in
order, on top of every page the engine builds for the job, so a rebuild keeps them; one that no longer fits the
engine's new page is kept and marked, not applied.
"""

from __future__ import annotations

from typing import Any

from tailor_engine.layout import PROJECT_BODY_LINES, line_count, project_body_lines
from tailor_engine.library import Library
from tailor_engine.records import Bullet
from tailor_engine.selection.selector import MAX_PROJECTS, feasible


def edit_problem(library: Library, on_page: list[str], remove: list[str], add: list[str]) -> str | None:
    """What stops this edit on a page holding `on_page`; None when it can be applied."""
    usable = {bullet.id: bullet for bullet in library.usable_bullets}
    off_page = [bullet_id for bullet_id in remove if bullet_id not in on_page]
    already = [bullet_id for bullet_id in add if bullet_id in on_page and bullet_id not in remove]
    unknown = [bullet_id for bullet_id in add if bullet_id not in usable]
    page = [usable[bullet_id] for bullet_id in applied(on_page, remove, add) if bullet_id in usable]
    # The checks in order; the first that fails is the answer.
    checks = (
        (not remove and not add, "it changes nothing"),
        (bool(off_page), f"{', '.join(off_page)} is not on the page"),
        (bool(already), f"{', '.join(already)} is on the page already"),
        (bool(unknown), f"{', '.join(unknown)} is not a written bullet in the library"),
        (not page, "it leaves the page empty"),
    )
    failed = next((message for failing, message in checks if failing), None)
    if failed or feasible(page, library.projects):
        return failed
    return _unfit(page, library)


def _unfit(page: list[Bullet], library: Library) -> str:
    """Which part of the engine's rule for a page (`feasible`) these bullets break.

    The lines, the most projects, or a bullet beside one it excludes, checked in that order.
    """
    lines = project_body_lines(page, library.projects)
    if lines > PROJECT_BODY_LINES:
        return f"the page would not fit: {lines} project lines of {PROJECT_BODY_LINES}"
    projects = len({bullet.parent for bullet in page})
    if projects > MAX_PROJECTS:
        return f"the page would hold {projects} projects, at most {MAX_PROJECTS}"
    on_page = {bullet.id for bullet in page}
    # From the end, where `applied` puts the added bullets, so the bullet named first is the one being put on.
    clash = next(
        ((bullet.id, other) for bullet in reversed(page) for other in bullet.excludes if other in on_page), None
    )
    if clash is not None:
        return f"{clash[0]} cannot be on a page beside {clash[1]}"
    return "the page breaks the engine's rule for a page"


def applied(on_page: list[str], remove: list[str], add: list[str]) -> list[str]:
    """The page's bullet ids after the edit: the removed ones out, the added ones on, in the order given."""
    return [bullet_id for bullet_id in on_page if bullet_id not in remove] + [
        bullet_id for bullet_id in add if bullet_id not in on_page or bullet_id in remove
    ]


def line_costs(library: Library, on_page: list[str]) -> str:
    """The lines each bullet and project title takes, for the judge to keep an edit within the page."""
    used = project_body_lines((bullet for bullet in library.usable_bullets if bullet.id in on_page), library.projects)
    costs = ", ".join(f"[{bullet.id}] {line_count(bullet.text, 'bullet')}" for bullet in library.usable_bullets)
    titles = ", ".join(
        f"[{project_id}] {line_count(project.title, 'plain')}" for project_id, project in library.projects.items()
    )
    return (
        f"The projects section holds {PROJECT_BODY_LINES} lines; this page uses {used}. A project on the page takes "
        f"its title's lines plus its bullets'. Title lines: {titles}. Bullet lines: {costs}."
    )


def described(library: Library, edit: dict[str, Any]) -> dict[str, Any]:
    """An edit as the page shows it: each bullet's project and text in place of its id."""
    return {
        "remove": bullet_entries(library, edit.get("remove") or []),
        "add": bullet_entries(library, edit.get("add") or []),
        "why": edit.get("why"),
    }


def bullet_entries(library: Library, bullet_ids: list[str]) -> list[dict[str, str]]:
    """Each bullet as the page shows it: its project's title and its text; unknown ids left out."""
    bullets = {bullet.id: bullet for bullet in library.bullets}
    return [
        {"project": library.projects[bullets[bullet_id].parent].title, "text": bullets[bullet_id].text}
        for bullet_id in bullet_ids
        if bullet_id in bullets
    ]
