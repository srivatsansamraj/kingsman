"""The Profile and Stats pages' data: the library as the engine reads it, and what each reading cost.

Profile counts, for each project, how many read jobs' pages print it; and, for each capability, how many read jobs'
mappings ask for it (each alternative a requirement names counts), so the capabilities asked for with no bullet behind
them can be ranked. Stats takes each call's time, tokens, attempts and the model that answered from the saved reading
(call 1), mapping (call 2) and project choice (call 3, hybrid mode), and the page's build time from the tracker. Its
token counts are the Claude plan's; Jev's requests, input tokens and their price are counted apart.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from tailor_engine.layout import COURSES_MAX
from tailor_engine.reading import store
from tailor_engine.selection.capability_matching import capabilities_of

from .display import answered
from .jobs import Jobs, calls_needed_for, measures
from .tracker import DECISIONS

# TypeSafe's price for Jev: $0.042 a million input tokens, output free (TypeSafe's documentation).
JEV_DOLLARS_PER_MILLION_INPUT = 0.042


def profile(jobs: Jobs) -> dict[str, Any]:
    library, _fingerprint = jobs.library_cache.get()
    read_pages = []
    asked: dict[str, int] = {}
    for posting_id, row in jobs.tracker.rows().items():
        posting = jobs.posting(posting_id)
        if row["removed"] or posting is None:
            continue
        reading, mapping = jobs.records(posting)
        if calls_needed_for(reading, mapping, library):
            continue
        page = jobs.page(posting_id)
        if page is not None and not row["page_refused"]:
            read_pages.append(page)
        requirements = (mapping or {}).get("requirements") or []
        for capability in {name for entry in requirements for name in capabilities_of(entry)}:
            asked[capability] = asked.get(capability, 0) + 1
    printed = [{entry["group"] for entry in page.get("page") or []} for page in read_pages]
    bullets_by_project: dict[str, list[dict[str, Any]]] = {}
    covered: set[str] = set()
    for bullet in library.bullets:
        entry = library.bullet_capabilities.get(bullet.id)
        capabilities = list(entry["capabilities"]) if entry else []
        if not bullet.pending:
            covered.update(capabilities)
        bullets_by_project.setdefault(bullet.parent, []).append(
            {"text": bullet.text or "(to be written)", "skills": capabilities, "pending": bullet.pending}
        )
    domains: dict[str, list[str]] = {}
    for capability, domain in library.capability_vocabulary.items():
        domains.setdefault(domain, []).append(capability)
    return {
        "projects": [
            {
                "id": project_id,
                "title": details.title,
                "facts": details.facts,
                "used": sum(project_id in groups for groups in printed),
                "bullets": bullets_by_project.get(project_id, []),
            }
            for project_id, details in library.profiles.items()
        ],
        "read_jobs": len(read_pages),
        "skill_rows": [
            {
                "title": row.titles[0].text if row.titles else row.id,
                "fixed": row.always_shown,
                "members": [m.text for m in row.members],
            }
            for row in library.skill_rows.values()
        ],
        "degrees": [
            {
                "institution": degree.institution,
                "award": degree.award,
                "dates": degree.dates,
                "courses": [course.text for course in degree.courses],
            }
            for degree in library.degrees.values()
        ],
        "courses_max": COURSES_MAX,
        "domains": [{"name": name, "skills": skills} for name, skills in domains.items()],
        "covered": sorted(covered),
        "asked": asked,
    }


def _call(record: dict[str, Any] | None) -> dict[str, Any]:
    """One call's cost to one posting, and the model that answered it, as Stats shows them.

    Zeros, marked unknown, for a record saved before costs were kept. A call 2 made for several postings at once saves
    the whole call's seconds and each posting's share of its tokens (`batch` is how many shared it); the seconds are
    divided the same way, so a sum over postings counts each call once. "by" and "fallback" are `display.answered`'s;
    "jev" says Jev answered, so its tokens are not the Claude plan's. "attempts" counts every request made and
    "requests" those the call was split into (Jev's call 2 sends several at once), so a call was asked again
    `attempts - requests` times.
    """
    model = {
        **(answered(record) or {"by": None, "fallback": None}),
        "jev": str((record or {}).get("model") or "").startswith("jev"),
    }
    if not record or not record.get("seconds"):
        return {"seconds": 0.0, "input": 0, "output": 0, "attempts": 1, "requests": 1, "unknown": True, **model}
    return {
        "seconds": float(record["seconds"]) / int(record.get("batch") or 1),
        "input": int(record.get("input_tokens") or 0),
        "output": int(record.get("output_tokens") or 0),
        "attempts": int(record.get("attempts") or 1),
        "requests": int(record.get("requests") or 1),
        "unknown": False,
        **model,
    }


def reading_stats(jobs: Jobs) -> dict[str, Any]:
    """Per read job: each call's cost, the page's build time, and the day it was read.

    "call3" is None for a job with no call 3 made (a legacy page, or a match that was not low): it cost nothing, which
    is not the same as a cost not kept. A saved call 3 counts whether or not the page uses its projects. "tokens" counts
    the calls a Claude model answered; "jev" the requests Jev answered (a retried call made two), their input tokens
    and their price.
    """
    library, _fingerprint = jobs.library_cache.get()
    found: dict[str, Any] = {}
    for posting_id, row in jobs.tracker.rows().items():
        posting = jobs.posting(posting_id)
        if row["removed"] or posting is None:
            continue
        reading, mapping = jobs.records(posting)
        if calls_needed_for(reading, mapping, library):
            continue
        call1 = _call((reading or {}).get("info"))
        call2 = _call(dict(mapping or {}))
        choice = store.load_choice(posting["text"], jobs.paths.choices)
        call3 = _call(dict(choice)) if choice is not None else None
        calls = [call1, call2, *([call3] if call3 else [])]
        by_jev = [call for call in calls if call["jev"]]
        jev_input = sum(call["input"] for call in by_jev)
        made = (mapping or {}).get("made") or ""
        read_on = (
            made[:10]
            if made
            else dt.date.fromtimestamp(jobs.paths.postings.joinpath(f"{posting_id}.json").stat().st_mtime).isoformat()
        )
        found[posting_id] = {
            "call1": call1,
            "call2": call2,
            "call3": call3,
            "build": row["page_seconds"] or 0.0,
            "read": read_on,
            "seconds": sum(call["seconds"] for call in calls),
            "tokens": sum(call["input"] + call["output"] for call in calls if not call["jev"]),
            "jev": {
                "requests": sum(call["attempts"] for call in by_jev),
                "input": jev_input,
                "dollars": jev_input * JEV_DOLLARS_PER_MILLION_INPUT / 1e6,
            },
            "retries": sum(call["attempts"] - call["requests"] for call in calls),
            "unknown": any(call["unknown"] for call in calls),
        }
    return {"readings": found, "today": dt.date.today().isoformat(), "edits": edit_stats(jobs)}


def edit_stats(jobs: Jobs) -> dict[str, Any]:
    """The selector's pages against the judge-assisted ones.

    Per job with an accepted edit: the engine's page and the edited page (match, required items met, score, how many
    bullets differ). Over every decision on a judge proposal: how many were accepted, dismissed and undone, and for how
    many accepted ones the engine's own score put the page the user chose below its own: the count that would argue for
    changing the selector's weights, if it grows.
    """
    pages = []
    for posting_id, row in jobs.tracker.rows().items():
        page = jobs.page(posting_id) if not row["removed"] and not row["page_refused"] else None
        # An edited page keeps the engine's measures beside its own (`Jobs._with_edits`).
        engine: dict[str, Any] | None = cast(dict[str, Any], page).get("engine") if page else None
        if page is None or not engine:
            continue
        edited = measures(page)
        changed = len(set(engine["selected"]) ^ set(edited["selected"]))
        pages.append({"id": posting_id, "engine": engine, "edited": edited, "changed": changed})
    decisions = jobs.tracker.decisions()
    counts = {decision: sum(entry["decision"] == decision for entry in decisions) for decision in DECISIONS}
    scored = [entry for entry in decisions if entry["decision"] == "accepted" and entry["before"] and entry["after"]]
    lower = sum(entry["after"]["score"] < entry["before"]["score"] for entry in scored)
    return {"pages": pages, "decisions": counts, "accepted_scored": len(scored), "engine_scored_lower": lower}
