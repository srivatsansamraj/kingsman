"""A model judge for one built page: does each project belong, is each bullet relevant, what is missing.

The judge sees the posting, a one-line description of the candidate built from the library's degrees,
every project the candidate has (bullets with ids, the project's facts and notes), and the page. Its
answer is checked (one verdict per project and per bullet on the page, "missing" naming real
projects) and asked for once more with the problems listed. Nothing leaves without the privacy check.
"""

from __future__ import annotations

from typing import Any, TypedDict

from tailor_engine.layout import PAGE_PROJECTS, PROJECT_BODY_LINES
from tailor_engine.library import Library
from tailor_engine.privacy import check_outgoing
from tailor_engine.reading.model_call import ask_checked, call_json, problems_note
from tailor_engine.records import Posting
from tailor_engine.rendering.judge_input import (
    NUMBER_WORDS,
    POSTING_CHARACTERS,
    PageIds,
    candidate_line,
    library_text,
    render_page,
)

MODEL = "claude-opus-5-5"

# The page's limits come from the layout, so the prompt cannot drift from the page it describes.
SYSTEM_PROMPT = (
    "You judge the projects section of a one-page resume against one job posting, for one\n"
    "candidate. You are given the posting, every project the candidate has (bullets with IDs in square\n"
    f"brackets), and the page. The page holds at most {NUMBER_WORDS[PAGE_PROJECTS]} projects in "
    f"{PROJECT_BODY_LINES} lines; that limit is fixed, so the page\n"
    """is not worse for leaving something out for space alone, but it is worse when a project that fits the role
less well took a slot a better-fitting project should have had.

Give:
- every project on the page: "belongs": true if a hiring manager for this role would want it on this page;
  false if it spends a slot on work this role does not care about while better-fitting work was
  available. One short reason.
- every bullet on the page: "relevant": true or false for this role.
- "missing": projects from the candidate's list that should have been on the page instead of something
  that is there; at most three; [] if none.
- "why": one sentence on how well the page fits the role.

Judge only fit to this role. Ignore wording, order and formatting. Use only what the candidate did as
described.

Output ONLY JSON:
{"projects": [{"project": "<id>", "belongs": true, "reason": "..."}],
 "bullets": [{"id": "<bullet id>", "relevant": true}],
 "missing": ["<project id>"],
 "why": "..."}"""
)


def verdict_problems(verdict: Any, page: PageIds, known_project_ids: set[str]) -> list[str]:
    """What is wrong with the judge's answer for this page; [] when it can be used."""
    if not isinstance(verdict, dict):
        return ["the answer must be one JSON object"]
    problems: list[str] = []
    lists: dict[str, list[Any]] = {}
    for field in ("projects", "bullets", "missing"):
        value = verdict.get(field) or []
        if not isinstance(value, list):
            problems.append(f'"{field}" must be a list')
            value = []
        lists[field] = value
    for field, flag in (("projects", "belongs"), ("bullets", "relevant")):
        for item in lists[field]:
            if not isinstance(item, dict):
                problems.append(f'every item of "{field}" must be an object; found {item!r}')
            elif not isinstance(item.get(flag), bool):
                problems.append(f'"{flag}" must be true or false in {item!r}')
    on_page = {project_id for project_id, _bullets in page}
    judged = [str(item.get("project")) for item in lists["projects"] if isinstance(item, dict)]
    if sorted(judged) != sorted(on_page):
        problems.append(f"give one project verdict for each of {sorted(on_page)}")
    page_bullet_ids = {bullet for _project, bullet_ids in page for bullet in bullet_ids}
    judged_bullets = [str(item.get("id")) for item in lists["bullets"] if isinstance(item, dict)]
    if sorted(judged_bullets) != sorted(page_bullet_ids):
        problems.append(f"give one bullet verdict for each of {sorted(page_bullet_ids)}")
    unknown = [project for project in lists["missing"] if str(project) not in known_project_ids]
    if unknown:
        problems.append(f"missing lists unknown projects {unknown}")
    return problems


class JudgeResult(TypedDict):
    verdict: Any  # the answer's JSON object as the judge wrote it
    valid: bool
    problems: list[str]  # what is still wrong with the last answer
    attempts: int
    input_tokens: int
    output_tokens: int
    seconds: float
    model: str


def judge_page(posting: Posting, page: PageIds, library: Library) -> JudgeResult:
    """The judge's verdict on one page, checked, with what it cost."""
    candidate, projects, rendered_page = candidate_line(library), library_text(library), render_page(page, library)
    # Checked as built and sent as checked: everything from the candidate's library passes the guard. The
    # posting is public text and is not checked (it may name a company's own GitHub, for example).
    check_outgoing(candidate + "\n" + projects + "\n" + rendered_page)
    user_prompt = (
        f"ROLE: {posting.get('title', '')} at {posting.get('company', '')}\n\n"
        f"POSTING:\n{posting['text'][:POSTING_CHARACTERS]}\n\n{candidate}\n\n"
        f"THE CANDIDATE'S PROJECTS:\n{projects}\n\nTHE PAGE:\n{rendered_page}"
    )
    known_projects = set(library.projects)
    checked = ask_checked(
        lambda system_prompt, text: call_json(MODEL, system_prompt, text),
        SYSTEM_PROMPT,
        user_prompt,
        lambda answer: verdict_problems(answer["json"], page, known_projects),
        problems_note,
    )
    return {
        "verdict": checked.answer["json"],
        "valid": not checked.problems,
        "problems": checked.problems,
        "attempts": checked.attempts,
        "input_tokens": checked.input_tokens,
        "output_tokens": checked.output_tokens,
        "seconds": checked.seconds,
        "model": MODEL,
    }
