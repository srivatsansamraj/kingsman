"""The candidate, the projects and a page as a judge model reads them.

Both judges build their prompts from these: the benchmark's (`tailor_bench.judge`) and the dashboard's
(`tailor_dashboard.review`), so a change here changes what each sends. The text comes from the candidate's library, so
each caller passes it through the privacy guard (`privacy.check_outgoing`) before it is sent.
"""

from __future__ import annotations

import re

from ..library import Library
from ..privacy import OPERATIONAL_NOTES
from ..records import Bullet, PageResult

# A page as (project, bullet ids) in page order: what a judge reads, and what the benchmark compares with the advisors.
PageIds = list[tuple[str, list[str]]]

# A limit as a prompt states it ("at most six projects").
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")

# The posting as a judge reads it, cut at this length: the requirements come before the benefits and the
# legal text, and the page is judged on those. Set by hand.
POSTING_CHARACTERS = 9000

# Up to this many of a project's notes, each cut at this length: enough context for the judge without
# the notes outweighing the bullets it is judging. Set by hand.
NOTES_PER_PROJECT = 3
NOTE_CHARACTERS = 600


def page_of(result: PageResult) -> PageIds:
    """A built page as (project, bullet ids) in page order."""
    return [
        (item["group"], [bullet_id for bullet_id in result["selected"] if bullet_id.split(".")[0] == item["group"]])
        for item in result["page"]
    ]


def candidate_line(library: Library) -> str:
    degrees = "; ".join(f"{degree.award}, {degree.institution} ({degree.dates})" for degree in library.degrees.values())
    return f"CANDIDATE: {degrees}."


def library_text(library: Library) -> str:
    """Every project as an advisor would read it.

    Its facts, when it ended, up to three of the candidate's notes (private ones, and any about publishing or accounts,
    left out), and every bullet with its id.
    """
    bullets_by_project: dict[str, list[Bullet]] = {}
    for bullet in library.bullets:
        if not bullet.pending:
            bullets_by_project.setdefault(bullet.parent, []).append(bullet)
    lines = []
    for project_id, profile in library.profiles.items():
        if not bullets_by_project.get(project_id):
            continue
        lines.append(f"PROJECT {project_id}: {profile.title}")
        lines.append(f"  facts: {profile.facts}")
        lines.append(f"  ended: {profile.ends}" + (f" ({profile.ends_note})" if profile.ends_note else ""))
        shareable = [
            note.text for note in profile.notes if not note.private and not OPERATIONAL_NOTES.search(note.text)
        ]
        for note in shareable[:NOTES_PER_PROJECT]:
            lines.append("  note: " + re.sub(r"\s+", " ", note)[:NOTE_CHARACTERS])
        for bullet in bullets_by_project[project_id]:
            lines.append(f"  [{bullet.id}] {bullet.text}")
        lines.append("")
    return "\n".join(lines)


def render_page(page: PageIds, library: Library) -> str:
    """The page as the judge reads it: each project with its bullets, each with its id."""
    text_by_bullet_id = {bullet.id: bullet.text for bullet in library.bullets}
    lines = []
    for project_id, bullet_ids in page:
        lines.append(f"  [{project_id}] {library.projects[project_id].title}")
        lines += [f"    [{bullet_id}] {text_by_bullet_id[bullet_id]}" for bullet_id in bullet_ids]
    return "\n".join(lines)
