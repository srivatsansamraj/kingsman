"""Model call 3 answered by TypeSafe's Jev: for each listed project, how likely it belongs on the page.

One request a posting. The state holds the posting (its title and first 7,000 characters, as Opus reads it) and the
projects call 3 lists (`project_choice.listed_projects`, the same projects Opus sees: title, one-line summary, status
and end date); the questions are one yes-or-no a project, all in the same request. The six most probable projects are
kept, best first, always six: a floor on the probability left pages with three or four projects and lost the two
strongest security projects on the core security roles, which Jev ranks fifth and sixth there.

Measured on 31 postings: agreement with the advisors 0.688 against Opus 5's 0.696; 29 of the 31 pages the same
as Opus 5's, and of the two that differ, blind judges split one and gave the other to Opus 5. About 0.5 s and 5,400
input tokens a posting, off the Claude plan.

The projects pass the privacy guard before any request, exactly as they do before Opus; the posting is the employer's
and is not guarded, as before Opus. When Jev cannot answer (`jev`), `choose_projects` asks Opus 5
(`project_choice.choose_projects`), and the record names the fallback and why.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from ..library import Library
from ..privacy import check_outgoing
from ..records import ProjectChoice
from . import jev, project_choice
from .jev import JevUnavailableError, Post
from .store import CHOICE_FORMAT

# The question's text as measured.
PURPOSE = (
    "A one-page resume for this job posting shows 4 to 6 of the candidate's projects: the ones that make the "
    "strongest case for shortlisting the candidate for this specific role, judged by relevance to what the role "
    "is about and by the strength and credibility of the evidence."
)
CRITERIA = {
    "true": "Among the strongest evidence for this role; it belongs on the page.",
    "false": "Other projects make a stronger case for this role; leave it off.",
}
KEPT = project_choice.MOST_PROJECTS
WHY = "Jev: the six projects most likely to belong on the page"

Chooser = Callable[[str, str, Library], ProjectChoice]


def candidate_projects(library: Library) -> dict[str, dict[str, str]]:
    """The listed projects as the state holds them: id -> title, what it is, status, end."""
    return {
        project_id: {"title": title, "what it is": summary, "status": facts, "ended": ends}
        for project_id, title, summary, facts, ends in project_choice._project_fields(library)
    }


def question(project_id: str) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": (
            f"{PURPOSE} Is the project `candidate_projects.{project_id}` one of those 4 to 6 projects for "
            "`job_posting`?"
        ),
        "criteria": CRITERIA,
    }


def request_body(title: str, text: str, projects: dict[str, dict[str, str]]) -> dict[str, Any]:
    """The request for one posting: the posting and the projects as the state, one question a project."""
    return {
        "model": jev.MODEL,
        "state": {
            "job_posting": {"title": title, "text": text[: project_choice.POSTING_CHARACTERS]},
            "candidate_projects": projects,
        },
        "questions": {project_id: question(project_id) for project_id in projects},
    }


def chosen(answers: dict[str, Any], project_ids: list[str]) -> list[str]:
    """The `KEPT` projects Jev finds most likely to belong on the page, best first; ties keep the library's order.

    Raises JevUnavailableError when a project's answer is missing or carries no probability.
    """
    chances = {project_id: jev.number(answers.get(project_id), "noul") for project_id in project_ids}
    return sorted(project_ids, key=lambda project_id: -chances[project_id])[:KEPT]


def ask(
    title: str,
    text: str,
    library: Library,
    *,
    post: Post | None = None,
    key: Callable[[], str] | None = None,
    slot: AbstractContextManager[Any] | None = None,
) -> ProjectChoice:
    """The saved-choice record from one Jev request.

    Raises PrivacyError, before any request, when the projects hold an identity word, and JevUnavailableError when Jev
    cannot answer.
    """
    projects = candidate_projects(library)
    body = request_body(title, text, projects)
    # The guard reads the projects exactly as Opus's does: the same fields, as plain text (in JSON a tab or a line break
    # is written as an escape joined to the next word, which hides that word). The posting is the employer's.
    check_outgoing(project_choice.project_list(library))
    project_ids = list(projects)
    answered = jev.ask(body, post=post, key=key, slot=slot)
    projects_kept = chosen(answered.answers, project_ids)
    problems = project_choice.answer_problems({"projects": projects_kept}, project_ids)
    return {
        "projects": projects_kept,
        "why": WHY,
        "valid": not problems,
        "errors": problems,
        "first_errors": answered.first_errors,
        "attempts": answered.attempts,
        "input_tokens": answered.input_tokens,
        "output_tokens": answered.output_tokens,
        "seconds": answered.seconds,
        "projects_sha256_16": project_choice.projects_hash(library),
        "format": CHOICE_FORMAT,
        "model": jev.MODEL,
        "answered_by": answered.answered_by,
        "fallback": None,
        "made": dt.datetime.now().isoformat(timespec="seconds"),
    }


def choose_projects(
    title: str,
    text: str,
    library: Library,
    *,
    post: Post | None = None,
    key: Callable[[], str] | None = None,
    slot: AbstractContextManager[Any] | None = None,
    fallback: Chooser | None = None,
) -> ProjectChoice:
    """Call 3 on Jev; on Opus 5 (`project_choice.choose_projects`, looked up when it is made) when Jev cannot answer."""
    started = time.perf_counter()
    try:
        return ask(title, text, library, post=post, key=key, slot=slot)
    except JevUnavailableError as error:
        waited = time.perf_counter() - started
        record = (fallback or project_choice.choose_projects)(title, text, library)
        jev.fell_back(record, error, seconds=waited)
        return record
