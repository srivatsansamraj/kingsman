"""One page for one posting, from its saved reading and mapping: the orchestration of the selection package.

  reading   the requirements and keywords model call 1 read from the posting
  mapping   each requirement's capabilities and conditions (model call 2)
  library   the candidate's library

Skill rows and courses: each skill and course is scored against the mapped requirements, and the word-based
demand (`word_matching.word_demand`) only titles the rows, orders the skills that fill a row, and ranks courses after
that score. The rows chosen also count as visible text when a requirement's conditions are checked. A posting the
library can barely answer is refused before anything is chosen: the `reach_floor` weight is the least share of its
requirement weight the library must be able to meet.

The result is a plain dictionary (JSON-ready): the page, the measures behind it, and the trace of moves. It
records everything the page prints (projects with their bullets, skill rows, coursework), so the renderer
only formats and cannot print rows the page was not scored with.
"""

from __future__ import annotations

from collections.abc import Collection

from ..layout import project_body_lines
from ..library import Library
from ..records import Bullet, PageResult, Posting, ProjectChoice, Reading, Requirement, ShownChoice, Trace
from .capability_matching import (
    CapabilityMatcher,
    capabilities_of,
    capability_bullets,
    capability_demand,
    family_factors,
    family_overlap,
)
from .scoring import PageContext, score_terms, weighted_score
from .selector import MAX_PROJECTS, page_order, select
from .skills_and_courses import ChosenSkillRow, listed_skills, pick_courses, pick_skill_rows, skill_fit
from .weights import DEFAULT_WEIGHTS, Weights
from .word_matching import topics_by_evidence_word, word_demand


# Named without "Error": a refusal is the engine's answer for a posting it cannot serve, not a fault.
class Refused(ValueError):  # noqa: N818
    """The library cannot answer enough of the posting for a page to be worth building."""


# ─────────────────────────────────────────────────────────────
# Refusals and the page's record
# ─────────────────────────────────────────────────────────────


def _refuse_below_floor(reach: float, weights: Weights) -> None:
    if reach < weights.reach_floor:
        raise Refused(f"{1 - reach:.0%} of this posting's requirement weight is outside the library; page not built")


def _refuse_an_empty_page(matched_bullets: list[Bullet]) -> None:
    if not matched_bullets:
        # Every project fell under the open floor with no required item of its own to meet: an empty page
        # is no answer, so it is refused like a posting the library cannot reach.
        raise Refused("no project clears the bar for this posting; page not built")


def _result(bullets: list[Bullet], trace: Trace, reach: float, context: PageContext) -> PageResult:
    """The page's record."""
    projects = context.projects
    requirements, demand = context.requirements, context.demand
    met = context.matcher.met_requirement_names(bullets, requirements, context.outside_text)
    terms = score_terms(bullets, context)
    return {
        "demand": demand,
        "reach": reach,
        "selected": [bullet.id for bullet in bullets],
        "bullets": len(bullets),
        "project_lines": project_body_lines(bullets, projects),
        "coverage": terms.coverage,
        "emphasis": terms.emphasis,
        "standing": terms.standing,
        "incoherence": terms.incoherence,
        "cost": terms.cost,
        "cost_detail": terms.cost_detail,
        "depth": terms.depth,
        "score": weighted_score(terms, context.weights),
        "missing_required": [
            requirement["name"]
            for requirement in requirements
            if requirement["required"] and requirement["name"] not in met
        ],
        "requirement_credit": context.matcher.requirement_credits(bullets, requirements, context.outside_text),
        "trace": trace,
        "page": [
            {
                "group": project_id,
                "title": projects[project_id].title,
                "bullets": [bullet.text for bullet in project_bullets],
            }
            for project_id, project_bullets in page_order(bullets, demand, projects, context.standing, context.weights)
        ],
    }


def _skills_and_coursework(
    rows: list[ChosenSkillRow], library: Library, demand: dict[str, float], course_scores: dict[str, float]
) -> PageResult:
    """The skill rows the page prints and each degree's courses, degrees in library order.

    The fixed rows are left out: the template holds them as they are.
    """
    return {
        "skill_rows": [
            {"row": row.row_id, "title": row.title, "members": list(row.members)}
            for row in rows
            if not library.skill_rows[row.row_id].always_shown
        ],
        "coursework": [
            {
                "degree": degree_id,
                "courses": pick_courses(degree, demand, library.tag_adjacency, scores=course_scores),
            }
            for degree_id, degree in library.degrees.items()
        ],
    }


# ─────────────────────────────────────────────────────────────
# Capability mode
# ─────────────────────────────────────────────────────────────


def _capability_page(
    usable: list[Bullet],
    requirements: list[Requirement],
    mapping: list[Requirement],
    joined: list[Requirement],
    library: Library,
    *,
    skills: list[str],
    outside_text: str,
    max_projects: int,
    weights: Weights,
    chosen: Collection[str] | None = None,
    projects: Collection[str] | None = None,
) -> PageResult:
    """The page matched on capabilities.

    `joined` is each requirement joined with its mapping (`joined_with_mapping`); each bullet is joined with its
    capabilities here. The family boost helps choose the page; the page is then measured and ordered on plain standing.
    With `chosen`, nothing is chosen: those bullets are the page, measured and ordered the same way. With `projects`,
    bullets are chosen from those projects only; reach is still the whole library's, so the refusal does not depend on
    which projects were given.
    """
    # A requirement the mapping does not name gets no capability, so only its words can meet it; the page lists it.
    mapped = mapped_names(mapping)
    unmapped = [requirement["name"] for requirement in requirements if requirement["name"] not in mapped]
    vocabulary = library.capability_vocabulary
    # The skills the page lists can meet a condition such as a language, by whole name only.
    matcher = CapabilityMatcher(vocabulary, weights, skills)
    candidates = capability_bullets(usable, library.bullet_capabilities, vocabulary, library.domain_tags, weights)
    reach = matcher.coverage(candidates, joined, outside_text)
    _refuse_below_floor(reach, weights)
    if projects is not None:
        candidates = [bullet for bullet in candidates if bullet.parent in projects]
    demand = capability_demand(joined, vocabulary, library.domain_tags, weights)

    def context(factors: dict[str, float] | None = None) -> PageContext:
        return PageContext.create(
            joined,
            demand,
            projects=library.projects,
            adjacency=library.domain_adjacency,
            outside_text=outside_text,
            matcher=matcher,
            weights=weights,
            factors=factors,
        )

    if chosen is None:
        overlap = family_overlap(candidates, demand, library.families, vocabulary, library.domain_tags)
        boosted = context(family_factors(library.projects, overlap, weights.family_boost))
        matched_bullets, trace = select(candidates, boosted, max_projects)
    else:
        unknown = set(chosen) - {bullet.id for bullet in candidates}
        if unknown:
            raise ValueError(f"not usable bullets: {sorted(unknown)}")
        matched_bullets, trace = [bullet for bullet in candidates if bullet.id in chosen], []
    _refuse_an_empty_page(matched_bullets)
    result = _result(matched_bullets, trace, reach, context())
    result["requirements"] = [
        {
            "name": requirement["name"],
            "required": requirement["required"],
            "capabilities": requirement["capabilities"],
            "conditions": requirement["conditions"],
        }
        for requirement in joined
    ]
    result["unmapped"] = unmapped
    return result


# ─────────────────────────────────────────────────────────────
# Building a page
# ─────────────────────────────────────────────────────────────


def mapped_names(mapping: list[Requirement]) -> set[str]:
    return {entry["name"] for entry in mapping}


def joined_with_mapping(requirements: list[Requirement], mapping: list[Requirement]) -> list[Requirement]:
    """Each requirement with the capabilities and conditions its mapping gives; none for one the mapping leaves out."""
    mapped = {entry["name"]: entry for entry in mapping}
    return [
        {
            **requirement,
            "capabilities": capabilities_of(mapped.get(requirement["name"], {})),
            "conditions": mapped.get(requirement["name"], {}).get("conditions", []),
        }
        for requirement in requirements
    ]


def requirements_from(reading: Reading) -> list[Requirement]:
    """The reading's requirements with the fields selection uses."""
    return [
        {"name": item["name"], "tokens": item["tokens"], "required": item["required"]}
        for item in reading["requirements"]
    ]


def build_page(
    posting: Posting,
    reading: Reading,
    library: Library,
    mapping: list[Requirement],
    *,
    max_projects: int = MAX_PROJECTS,
    weights: Weights = DEFAULT_WEIGHTS,
    chosen: Collection[str] | None = None,
    projects: Collection[str] | None = None,
) -> PageResult:
    """The page for `posting` ({"title", ...}), from its reading and its capability mapping (call 2's requirements).

    `chosen` gives the page's bullets instead of choosing them: the page is measured, ordered and recorded as a chosen
    one would be, with an empty trace. The dashboard uses it for an edit the user accepted. `projects` limits the
    choice to those projects' bullets: `build_hybrid_page` gives call 3's.

    Raises `Refused` when the library cannot meet at least `weights.reach_floor` of the posting's requirement weight,
    and ValueError when `chosen` and `projects` are both given, or `chosen` names a bullet that is not usable.
    """
    if chosen is not None and projects is not None:
        raise ValueError("give the page's bullets or the projects to choose them from, not both")
    usable = library.usable_bullets
    outside_text = library.always_shown_text
    requirements = requirements_from(reading)
    keywords = list(reading.get("keywords") or [])
    word_based_demand, unmet = word_demand(
        requirements, keywords, topics_by_evidence_word(usable + library.section_entries), weights
    )
    result: PageResult = {
        "posting": posting.get("title", ""),
        "posting_id": posting.get("id"),
        # The only mode since word mode was retired; saved pages keep the field.
        "mode": "capabilities",
        "extraction": {
            "requirements": len(requirements),
            "required": sum(requirement["required"] for requirement in requirements),
            "keywords": len(keywords),
        },
        "unmet": unmet,
    }
    # Each skill and course is scored against the mapped requirements, once: the rows scored with are the rows
    # printed. Every skill a row may list and every course is scored, one with no entry in skill-capabilities.toml by
    # its name alone.
    joined = joined_with_mapping(requirements, mapping)
    vocabulary = library.capability_vocabulary
    skill_names = [text for row in library.skill_rows.values() if not row.always_shown for text, _tags in row.members]
    course_names = [text for degree in library.degrees.values() for text, _tags in degree.courses]
    skill_fits = skill_fit(skill_names, library.skill_capabilities, joined, vocabulary, weights, whole_name=True)
    course_fits = skill_fit(course_names, library.course_capabilities, joined, vocabulary, weights, whole_name=False)
    course_scores = {name: course_fits.score(name) for name in course_fits.fits}
    # The skills answering a requirement in full, with their share of the requirement weight: what an application
    # form's skills field gets.
    result["skill_scores"] = {
        name: round(skill_fits.score(name), 4) for name in skill_fits.fits if skill_fits.direct(name)
    }
    rows = pick_skill_rows(library.skill_rows, word_based_demand, skill_fits)
    result.update(
        _capability_page(
            usable,
            requirements,
            mapping,
            joined,
            library,
            skills=listed_skills(rows),
            outside_text=outside_text,
            max_projects=max_projects,
            weights=weights,
            chosen=chosen,
            projects=projects,
        )
    )
    result.update(_skills_and_coursework(rows, library, word_based_demand, course_scores))
    return result


def build_hybrid_page(
    posting: Posting,
    reading: Reading,
    library: Library,
    mapping: list[Requirement],
    choice: ProjectChoice | None,
    *,
    max_projects: int = MAX_PROJECTS,
    weights: Weights = DEFAULT_WEIGHTS,
) -> PageResult:
    """Hybrid mode: the engine's page, or the page from call 3's projects where the engine's match is low.

    When the engine's own match is under `weights.model_projects_below`, the selector chooses among the chosen projects'
    bullets under the same line budget and weak-project gate, so a chosen project may not be placed. `choice` is a
    current, valid call 3 answer, or None when there is none. The page records whose projects it uses ("projects_by"),
    the choice with the chosen projects the page holds ("placed"), and, when the model's projects are used, the
    engine's own match. When the chosen projects leave nothing to place, the engine's page stays. Raises `Refused` as
    `build_page` does.
    """
    page = build_page(posting, reading, library, mapping, max_projects=max_projects, weights=weights)
    page["projects_by"] = "engine"
    chosen_projects = (choice or {}).get("projects") or []
    if not chosen_projects:
        return page
    why = (choice or {}).get("why", "")
    page["choice"] = _shown_choice(chosen_projects, why, page)
    if page["coverage"] >= weights.model_projects_below:
        return page
    try:
        chosen_page = build_page(
            posting,
            reading,
            library,
            mapping,
            max_projects=max_projects,
            weights=weights,
            projects=set(chosen_projects),
        )
    except Refused:
        return page
    chosen_page.update(
        {
            "projects_by": "model",
            "choice": _shown_choice(chosen_projects, why, chosen_page),
            "engine_match": page["coverage"],
        }
    )
    return chosen_page


def _shown_choice(chosen_projects: list[str], why: str, page: PageResult) -> ShownChoice:
    """Call 3's choice as a page records it, with the chosen projects that page holds, best first."""
    on_page = {entry["group"] for entry in page["page"]}
    return {
        "projects": list(chosen_projects),
        "why": why,
        "placed": [project_id for project_id in chosen_projects if project_id in on_page],
    }
