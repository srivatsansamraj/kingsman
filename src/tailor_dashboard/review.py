"""Ask the judge: one model call's view of a job and its page, brief or detailed.

Advice only; nothing in page selection reads it. The judge reads the posting, the candidate's degrees, every project
with its bullets, the page built for the posting, and each requirement's credit on that page, and answers: how
relevant the candidate is to the job, how well the page is aligned with it, the gaps it sees, and roles that would
fit better. Brief and detailed differ in length only. The answer is checked and asked for once more with its problems
listed (`ask_checked`); everything taken from the candidate's library passes the privacy guard before it is sent, as
in the benchmark's judge.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tailor_engine.library import Library
from tailor_engine.privacy import check_outgoing
from tailor_engine.reading.model_call import JsonAnswer, ask_checked, call_json, problems_note
from tailor_engine.records import PageResult, Posting
from tailor_engine.rendering.judge_input import (
    NUMBER_WORDS,
    POSTING_CHARACTERS,
    candidate_line,
    library_text,
    page_of,
    render_page,
)

from .edits import edit_problem, line_costs

MODEL = "claude-opus-5-5"  # the benchmark judge's model; advice is read by a person, once per request
VERDICTS = ("strong", "moderate", "weak")
# The most gaps, better-fitting roles and page edits each length allows; the answer is checked against them, and
# LENGTH states them to the judge.
LIMITS = {"brief": (3, 2, 2), "detailed": (6, 3, 3)}
_GAPS = {level: NUMBER_WORDS[most[0]] for level, most in LIMITS.items()}
_FITS = {level: NUMBER_WORDS[most[1]] for level, most in LIMITS.items()}
_EDITS = {level: NUMBER_WORDS[most[2]] for level, most in LIMITS.items()}

SYSTEM_PROMPT = """You advise one candidate about one job. You are given the job posting, the candidate's
degrees, every project the candidate has (bullets with IDs in square brackets), the one-page resume built for this
posting, and how each of the posting's requirements is met on that page: met, partly met or missing, with the bullets
that meet it.

Judge as the hiring manager for this role would. Use only what the candidate did as described; do not credit
experience the projects do not show. The candidate reads your answer without the IDs, so name a project by a short
form of its title (for example "the fuzzing project"), never by its ID.

Give:
- "verdict": "strong", "moderate" or "weak": how relevant the candidate is to this job.
- "summary": one sentence the candidate can act on.
- "relevance": why the verdict is what it is.
- "alignment": how well the page is aligned with the posting, and which of the candidate's own projects or bullets
  would align it better, if any.
- "gaps": what the posting asks for that the candidate's work does not show, most important first; each
  {"gap": a short name, "note": why it matters for this role, and whether any project partly covers it}.
- "better_fits": role titles that would fit this candidate better than this one; each {"role": the title, "why":
  the reason}; [] when this role fits well.
- "edits": changes to the page you recommend, each one the candidate accepts or refuses whole; each {"remove":
  [IDs of bullets on the page], "add": [IDs of the candidate's bullets not on the page], "why": one sentence}. Use
  only bullets listed with IDs; nothing new is written. The page must still fit its projects section (the lines are
  given under PAGE LINES) and hold at most six projects; a project comes onto the page with its first bullet, its
  title taking its lines, and leaves with its last. [] when the page should stay as it is. IDs go only in "remove"
  and "add".

Output ONLY JSON:
{"verdict": "...", "summary": "...", "relevance": "...", "alignment": "...",
 "gaps": [{"gap": "...", "note": "..."}], "better_fits": [{"role": "...", "why": "..."}],
 "edits": [{"remove": ["<id>"], "add": ["<id>"], "why": "..."}]}"""

LENGTH = {
    "brief": (
        f'LENGTH: brief. "relevance" and "alignment" in one or two sentences each; at most {_GAPS["brief"]} gaps, each '
        f"note one sentence; at most {_FITS['brief']} better-fitting roles, each reason one sentence; at most "
        f"{_EDITS['brief']} page edits."
    ),
    "detailed": (
        'LENGTH: detailed. "relevance" and "alignment" in three to five sentences each, naming the projects and '
        f"bullets that carry the page and any that should replace what is there; at most {_GAPS['detailed']} gaps, "
        "each note two or three sentences saying what would close it; at most "
        f"{_FITS['detailed']} better-fitting roles, each reason two sentences; at most {_EDITS['detailed']} page edits."
    ),
}


def credit_lines(page: PageResult) -> str:
    """Each requirement's credit on the page, as the judge reads it."""
    lines = []
    for entry in page.get("requirement_credit") or []:
        state = "met" if entry["credit"] >= 1.0 else "partly met" if entry["credit"] > 0 else "missing"
        kind = "required" if entry["required"] else "preferred"
        by = f" by {', '.join(entry['bullets'])}" if entry["bullets"] else ""
        lines.append(f"- ({kind}) {entry['name']}: {state}{by}")
    return "\n".join(lines)


def answer_problems(
    answer: Any,
    level: str,
    ids: frozenset[str] = frozenset(),
    edit_check: Callable[[list[str], list[str]], str | None] | None = None,
) -> list[str]:
    """What is wrong with the judge's answer for this length; [] when it can be used.

    `ids` are the library's IDs that could only be meant as IDs (see `library_ids`); one in the answer's prose is a
    problem, since the candidate reads it without them. `edit_check(remove, add)` says what stops an edit on this
    page, None when it can be applied.
    """
    if not isinstance(answer, dict):
        return ["the answer must be one JSON object"]
    problems = _edit_problems(answer.get("edits"), level, edit_check)
    given = answer.get("edits")
    edits = given if isinstance(given, list) else []
    prose = {key: value for key, value in answer.items() if key != "edits"}
    written = " ".join(_texts(prose) + [str(edit.get("why", "")) for edit in edits if isinstance(edit, dict)])
    named = sorted({word for word in re.findall(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)?", written) if word in ids})
    if named:
        problems.append(f"name projects by their titles, not by IDs; the answer uses {', '.join(named)}")
    if answer.get("verdict") not in VERDICTS:
        problems.append(f'"verdict" must be one of {list(VERDICTS)}')
    for field in ("summary", "relevance", "alignment"):
        if not isinstance(answer.get(field), str) or not answer[field].strip():
            problems.append(f'"{field}" must be a sentence')
    most_gaps, most_fits, _most_edits = LIMITS[level]
    for field, key, most in (("gaps", "gap", most_gaps), ("better_fits", "role", most_fits)):
        items = answer.get(field)
        if not isinstance(items, list):
            problems.append(f'"{field}" must be a list')
            continue
        if len(items) > most:
            problems.append(f'"{field}" may hold at most {most} items at this length')
        if any(not isinstance(item, dict) or not isinstance(item.get(key), str) for item in items):
            problems.append(f'every item of "{field}" must be an object with "{key}"')
    return problems


def _edit_problems(
    edits: Any, level: str, edit_check: Callable[[list[str], list[str]], str | None] | None
) -> list[str]:
    """What is wrong with the answer's page edits: their shape, their number, and each one against the page."""
    if not isinstance(edits, list):
        return ['"edits" must be a list ([] when the page should stay as it is)']
    _most_gaps, _most_fits, most_edits = LIMITS[level]
    problems = []
    if len(edits) > most_edits:
        problems.append(f'"edits" may hold at most {most_edits} items at this length')
    for number, edit in enumerate(edits, 1):
        remove, add = (edit.get("remove"), edit.get("add")) if isinstance(edit, dict) else (None, None)
        if not (
            isinstance(remove, list) and isinstance(add, list) and all(isinstance(item, str) for item in remove + add)
        ):
            problems.append(f'edit {number} must be an object with lists of IDs "remove" and "add", and "why"')
            continue
        problem = edit_check(remove, add) if edit_check else None
        if problem:
            problems.append(f"edit {number} cannot be applied: {problem}")
    return problems


def _texts(value: Any) -> list[str]:
    """Every string in an answer, however deep."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


def library_ids(library: Library) -> frozenset[str]:
    """IDs that could only be meant as IDs: every bullet's (they hold a dot) and projects' that hold a digit."""
    projects = {project_id for project_id in library.projects if any(character.isdigit() for character in project_id)}
    return frozenset(projects | {bullet.id for bullet in library.bullets})


def review_job(
    posting: Posting,
    page: PageResult,
    library: Library,
    level: str,
    *,
    call: Callable[[str, str], JsonAnswer] | None = None,
    role: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """The judge's checked answer for one job, with what it cost.

    `call(system, user)` stands in for the model; `role` is (title, company) as the dashboard shows them, the posting's
    own fields by default.
    """
    if level not in LIMITS:
        raise ValueError(f"unknown length {level!r}: brief or detailed")
    title, company = role or (posting.get("title", ""), posting.get("company", ""))
    on_page = list(page.get("selected") or [])
    candidate, projects = candidate_line(library), library_text(library)
    rendered_page, credits = render_page(page_of(page), library), credit_lines(page)
    # Everything taken from the candidate's library passes the guard; the posting is public text and is not checked.
    check_outgoing("\n".join((candidate, projects, rendered_page, credits)))
    user_prompt = (
        f"ROLE: {title} at {company}\n\n"
        f"POSTING:\n{posting['text'][:POSTING_CHARACTERS]}\n\n{candidate}\n\n"
        f"THE CANDIDATE'S PROJECTS:\n{projects}\n\nTHE PAGE:\n{rendered_page}\n\n"
        f"EACH REQUIREMENT ON THE PAGE:\n{credits}\n\n"
        f"PAGE LINES: {line_costs(library, on_page)}\n\n{LENGTH[level]}"
    )
    ids = library_ids(library)
    checked = ask_checked(
        call or (lambda system_prompt, text: call_json(MODEL, system_prompt, text)),
        SYSTEM_PROMPT,
        user_prompt,
        lambda answer: answer_problems(
            answer.get("json"), level, ids, lambda remove, add: edit_problem(library, on_page, remove, add)
        ),
        problems_note,
    )
    return {
        "level": level,
        "answer": checked.answer.get("json"),
        "problems": checked.problems,
        "attempts": checked.attempts,
        "input_tokens": checked.input_tokens,
        "output_tokens": checked.output_tokens,
        "seconds": checked.seconds,
        "model": MODEL,
    }
