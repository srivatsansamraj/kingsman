"""Model call 3: the candidate's projects that make the strongest case for one posting.

Sent: the posting's title and first 7,000 characters, and the candidate's projects that have a usable bullet, each as
id, title, one-line summary, status (its facts) and end. The answer is 4 to 6 of those ids, best first, and one
sentence. It is checked (ids from the list, none twice, 4 to 6) and asked for again once with the problems listed.
Nothing the model writes reaches the page: in hybrid mode the engine builds the page from the chosen projects' bullets
when its own match is under `Weights.model_projects_below`, and from the whole library otherwise.

Measured on 31 postings: under a match of 0.6 the model's projects were judged better than the engine's
7 to 1 (Opus) and 4 to 0 (Sonnet 5); above it, even. The prompt is the judge's own criterion and no rule of thumb: a
"built beats designed" line put one project first everywhere, and "relevance first" put read-about work above measured
work. Given call 1's requirements instead of the posting text, the call cost 32% fewer tokens and lost 2 to 8 head to
head.

The project list sits in the system prompt, the same text for every posting while the library is unchanged, so the
command line's cache can serve it (as call 2's vocabulary). It passes the privacy guard before every call. Its
fields are separated by "; ": with " | " the list cost about 2,400 more tokens a call on Opus 5.5, which counts a pipe
at about a token a character. A saved choice is stale once a listed project's content changes
(`projects_hash`), not when the list's layout does.

Call 3 asks Opus 5. Opus 5.5's safety classifier stopped every call 3 (category "cyber") and the command line fell back
to Opus 4.8, so every call 3 answer before then was Opus 4.8's. Asked directly on the 31 postings, Opus 5 picked what
Opus 4.8 picked (2 pages differ, judged 1 to 1 by both), in 5.5 s against 10 s, with no fallback; Sonnet 5 left the
strongest security project out of security pages and lost 2 to 7.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

from ..library import Library
from ..privacy import check_outgoing
from ..records import ProjectChoice
from . import model_call
from .model_call import Effort, JsonAnswer, answering_model, ask_checked, call_outcome, problems_note
from .store import CHOICE_FORMAT, text_key

MODEL = "claude-opus-5"
EFFORT: Effort = "medium"
POSTING_CHARACTERS = 7000  # as measured, though 16 of those 31 postings ran longer
FEWEST_PROJECTS = 4
MOST_PROJECTS = 6

INSTRUCTION = """You choose which of a candidate's projects go on a one-page resume for one job posting. Choose the 4 to 6
projects, best first, that make the strongest case for shortlisting the candidate for this specific role: relevance to
what the role is about, and the strength and credibility of the evidence. Use only the ids listed.

Reply with ONLY JSON: {"projects": ["<id>", ...], "why": "<one sentence>"}"""  # noqa: E501  the text as measured


def listed_projects(library: Library) -> list[str]:
    """The ids of the projects call 3 chooses from: those with a usable bullet, in library order."""
    return list(dict.fromkeys(bullet.parent for bullet in library.usable_bullets))


def _project_fields(library: Library) -> list[tuple[str, str, str, str, str]]:
    """Each listed project's id, title, summary, facts and end, as call 3 is sent them."""
    fields = []
    for project_id in listed_projects(library):
        profile = library.profiles[project_id]
        fields.append((project_id, profile.title, profile.summary.strip(), profile.facts.strip(), profile.ends))
    return fields


def project_list(library: Library) -> str:
    """One line a listed project, in library order: id; title; summary; status; end."""
    return "\n".join(
        f"{project_id}; {title}; {summary}; status: {facts}; ended: {ends}"
        for project_id, title, summary, facts, ends in _project_fields(library)
    )


def projects_hash(library: Library) -> str:
    """The key of the listed projects' content, which a saved choice records it was made from.

    Taken over each project's id, title, summary, facts and end, not over `project_list`'s text, so the list's layout
    can change without making the saved choices stale.
    """
    return text_key(json.dumps(_project_fields(library)))


def system_prompt(listing: str) -> str:
    return f"{INSTRUCTION}\n\nPROJECTS (id; title; what it is; status; ended):\n{listing}"


def build_prompt(title: str, text: str) -> str:
    return f"POSTING ({title}):\n{text[:POSTING_CHARACTERS]}"


def answer_problems(answer: Any, project_ids: list[str]) -> list[str]:
    """What breaks the rules in an answer; [] when it can be used."""
    projects = answer.get("projects") if isinstance(answer, dict) else None
    if not isinstance(projects, list):
        return ['the answer must be one JSON object with a "projects" list']
    if not all(isinstance(project, str) for project in projects):
        # Checked first: the duplicate check below puts each id in a dictionary, which an object cannot be.
        return ['"projects" must be a list of project id strings']
    problems = [f"{project!r} is not a project id in the list" for project in projects if project not in project_ids]
    problems += [f"{project!r} is chosen twice" for project in dict.fromkeys(projects) if projects.count(project) > 1]
    if not FEWEST_PROJECTS <= len(projects) <= MOST_PROJECTS:
        problems.append(f"choose {FEWEST_PROJECTS} to {MOST_PROJECTS} projects; the answer has {len(projects)}")
    return problems


def choose_projects(
    title: str, text: str, library: Library, call: Callable[[str, str], JsonAnswer] | None = None
) -> ProjectChoice:
    """The saved-choice record for one posting.

    `call(system, user)` returns {"json", "input_tokens", "output_tokens"}; by default it is one Opus 5 medium-effort
    call through the signed-in CLI. Raises PrivacyError, before any call, when the project list holds an identity word.
    """
    listing = project_list(library)
    check_outgoing(listing)
    project_ids = listed_projects(library)
    checked = ask_checked(
        call or _default_call,
        system_prompt(listing),
        build_prompt(title, text),
        lambda answer: answer_problems(answer["json"], project_ids),
        problems_note,
    )
    answer = checked.answer["json"] if isinstance(checked.answer["json"], dict) else {}
    chosen = [project for project in answer.get("projects") or [] if project in project_ids]
    why = answer.get("why")
    return {
        "projects": list(dict.fromkeys(chosen))[:MOST_PROJECTS],
        "why": why if isinstance(why, str) else "",
        **call_outcome(checked),
        "projects_sha256_16": projects_hash(library),
        "format": CHOICE_FORMAT,
        "model": MODEL,
        **answering_model(checked.answer),
        "made": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _default_call(system: str, user: str) -> JsonAnswer:
    return model_call.call_json(MODEL, system, user, effort=EFFORT)
