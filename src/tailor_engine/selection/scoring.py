"""How good a set of bullets is for one posting: the number the selector climbs.

  score = coverage     share of the posting's requirement weight the page meets (via the matcher)
        + emphasis     how closely the page's topic mix, by page space, matches the posting's
        + standing     how strong the chosen projects are, by bullet count
        - incoherence  projects whose own bullets pull in different directions
        - cost         off-topic space on weak projects, flagged bullets, bullets repeating each other
        + depth        projects with two to five bullets rather than one

each term multiplied by its weight in `weights.py`, where every number's origin is recorded.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from ..records import Bullet, CostDetail, Project, Requirement, RequirementCredit
from .project_values import project_values
from .tags import MAX_TAG_DISTANCE, tag_distance
from .weights import Weights


class Matcher(Protocol):
    """What the selector needs from the matcher (`capability_matching.CapabilityMatcher`)."""

    def coverage(self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str) -> float: ...

    def met_requirement_names(
        self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str
    ) -> set[str]: ...

    def meets_unmet_required_item(
        self,
        opening_bullets: list[Bullet],
        rest_of_page: list[Bullet],
        requirements: list[Requirement],
        outside_text: str,
    ) -> bool: ...

    def requirement_credits(
        self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str
    ) -> list[RequirementCredit]: ...


@dataclass(frozen=True)
class PageContext:
    """Everything that stays fixed while one page is chosen.

    Built by `create`, which computes each project's `standing` and `fill_rank` from the weights and any per-project
    factor.
    """

    requirements: list[Requirement]
    demand: dict[str, float]  # the posting's topic mix
    projects: dict[str, Project]
    adjacency: set[frozenset[str]]  # related topic tags
    outside_text: str  # text on the page outside the projects
    matcher: Matcher
    weights: Weights
    standing: dict[str, float]
    fill_rank: dict[str, float]

    @classmethod
    def create(
        cls,
        requirements: list[Requirement],
        demand: dict[str, float],
        *,
        projects: dict[str, Project],
        adjacency: set[frozenset[str]],
        outside_text: str,
        matcher: Matcher,
        weights: Weights,
        factors: dict[str, float] | None = None,
    ) -> PageContext:
        standing, fill_rank = project_values(projects, weights, factors or {})
        return cls(
            requirements=requirements,
            demand=demand,
            projects=projects,
            adjacency=adjacency,
            outside_text=outside_text,
            matcher=matcher,
            weights=weights,
            standing=standing,
            fill_rank=fill_rank,
        )

    def is_weak(self, project_id: str) -> bool:
        """Whether the project's standing is under the `open_floor` weight.

        A weak project opens a slot only for a required item nothing else on the page meets.
        """
        return self.standing[project_id] < self.weights.open_floor


def bullets_by_project(bullets: list[Bullet]) -> dict[str, list[Bullet]]:
    by_project: dict[str, list[Bullet]] = {}
    for bullet in bullets:
        by_project.setdefault(bullet.parent, []).append(bullet)
    return by_project


# ─────────────────────────────────────────────────────────────
# Topics
# ─────────────────────────────────────────────────────────────


def topic_mix(bullets: list[Bullet]) -> dict[str, float]:
    """The page's topics weighted by the space each bullet takes."""
    total = sum(bullet.character_count for bullet in bullets)
    if not total:
        return {}
    profile: dict[str, float] = {}
    for bullet in bullets:
        for tag, weight in bullet.tags.items():
            profile[tag] = profile.get(tag, 0.0) + weight * bullet.character_count / total
    return profile


def emphasis(bullets: list[Bullet], demand: dict[str, float]) -> float:
    """1 minus the share of page space spent on a different topic mix than the posting's.

    The share is half the summed differences: two mixes that share nothing differ by 1 on each side, 2 in all.
    """
    profile = topic_mix(bullets)
    if not profile:
        return 0.0
    return 1.0 - 0.5 * sum(abs(profile.get(tag, 0.0) - demand.get(tag, 0.0)) for tag in set(profile) | set(demand))


def off_topic_distance(
    tags: dict[str, float], demand: dict[str, float], adjacency: set[frozenset[str]], core_share: float
) -> float:
    """How far these topics sit from what the posting is about: 0 on topic, 0.5 related, 1 unrelated.

    What the posting is about is its heaviest topics, carrying `core_share` of its demand.
    """
    if not demand or not tags:
        return 0.0
    core: list[str] = []
    carried, total = 0.0, sum(demand.values()) or 1.0
    for tag, weight in sorted(demand.items(), key=lambda item: (-item[1], item[0])):
        core.append(tag)
        carried += weight
        if carried >= core_share * total:
            break
    distance_sum = 0.0
    for tag, weight in tags.items():
        distance = min(tag_distance(tag, core_tag, adjacency) for core_tag in core)
        distance_sum += weight * (distance / MAX_TAG_DISTANCE)
    return distance_sum / (sum(tags.values()) or 1.0)


def move_relevance(
    bullet: Bullet, demand: dict[str, float], adjacency: set[frozenset[str]], core_share: float
) -> float:
    """How on topic a bullet is: 1 minus the off-topic distance of its topics."""
    # Weighted by the bullet's length, as when a move could be several bullets: the distance divides it out again, and
    # keeping it keeps every float as it was.
    tags = {tag: weight * bullet.character_count for tag, weight in bullet.tags.items()}
    return 1.0 - off_topic_distance(tags, demand, adjacency, core_share)


# ─────────────────────────────────────────────────────────────
# Projects
# ─────────────────────────────────────────────────────────────


def standing(bullets: list[Bullet], standings: dict[str, float]) -> float:
    """Mean standing of the page's projects, weighted by bullets.

    By bullets rather than characters: a project must not gain standing for being described at greater length.
    """
    total = len(bullets) or 1
    return sum(
        standings[project_id] * len(project_bullets) / total
        for project_id, project_bullets in bullets_by_project(bullets).items()
    )


def incoherence(bullets: list[Bullet]) -> float:
    """How far each project's bullets are from sharing one topic, from 0 to 1, averaged over projects.

    For one project it is 1 minus its main topic's share of its space.
    """
    by_project = bullets_by_project(bullets)
    if not by_project:
        return 0.0
    penalty = 0.0
    for project_bullets in by_project.values():
        profile = topic_mix(project_bullets)
        if profile:
            penalty += 1.0 - max(profile.values())
    return penalty / len(by_project)


def depth_value(bullet_count: int, curve: tuple[float, ...]) -> float:
    return curve[min(bullet_count, len(curve) - 1)]


def depth(bullets: list[Bullet], curve: tuple[float, ...]) -> float:
    """What the page's projects are worth by their bullet counts, on the depth curve, averaged."""
    counts = {project_id: len(project_bullets) for project_id, project_bullets in bullets_by_project(bullets).items()}
    return sum(depth_value(count, curve) for count in counts.values()) / len(counts) if counts else 0.0


# ─────────────────────────────────────────────────────────────
# Cost
# ─────────────────────────────────────────────────────────────


def cost(
    bullets: list[Bullet],
    demand: dict[str, float],
    *,
    adjacency: set[frozenset[str]],
    standings: dict[str, float],
    weights: Weights,
) -> tuple[float, CostDetail]:
    """(cost, detail).

    Off-topic space counts less on a strong project, which carries its own weight; a bullet flagged with a liability
    counts more. Bullets whose evidence overlaps repeat each other.
    """
    if not bullets:
        return 0.0, {}
    off_topic_liability = 0.0
    for bullet in bullets:
        # Floored at 0: a family boost can lift a standing past 1, and off-topic space must never earn credit.
        off_topic_tolerance = max(0.0, 1.0 - standings.get(bullet.parent, 0.0))
        off_topic_liability += (
            off_topic_distance(bullet.tags, demand, adjacency, weights.core_share)
            * off_topic_tolerance
            * (1.0 + bullet.liability)
            * bullet.character_count
        )
    off_topic_liability /= sum(bullet.character_count for bullet in bullets) or 1

    redundancy = 0.0
    pairs = 0
    evidence_sets = [{word.lower() for word in bullet.evidence} for bullet in bullets]
    for left_evidence, right_evidence in itertools.combinations(evidence_sets, 2):
        if left_evidence and right_evidence:
            redundancy += len(left_evidence & right_evidence) / len(left_evidence | right_evidence)
            pairs += 1
    redundancy = redundancy / pairs if pairs else 0.0
    return min(1.0, weights.cost_liability * off_topic_liability + weights.cost_redundancy * redundancy), {
        "liability": round(off_topic_liability, 3),
        "redundancy": round(redundancy, 3),
    }


class ScoreTerms(NamedTuple):
    """The terms of the score for one page, before weighting."""

    coverage: float
    emphasis: float
    standing: float
    incoherence: float
    cost: float
    cost_detail: CostDetail
    depth: float


def score_terms(bullets: list[Bullet], context: PageContext) -> ScoreTerms:
    weights = context.weights
    page_cost, cost_detail = cost(
        bullets,
        context.demand,
        adjacency=context.adjacency,
        standings=context.standing,
        weights=weights,
    )
    return ScoreTerms(
        coverage=context.matcher.coverage(bullets, context.requirements, context.outside_text),
        emphasis=emphasis(bullets, context.demand),
        standing=standing(bullets, context.standing),
        incoherence=incoherence(bullets),
        cost=page_cost,
        cost_detail=cost_detail,
        depth=depth(bullets, weights.depth_curve),
    )


def weighted_score(terms: ScoreTerms, weights: Weights) -> float:
    return (
        weights.coverage * terms.coverage
        + weights.emphasis * terms.emphasis
        + weights.standing * terms.standing
        - weights.incoherence * terms.incoherence
        - weights.cost * terms.cost
        + weights.depth * terms.depth
    )


def score(bullets: list[Bullet], context: PageContext) -> float:
    return weighted_score(score_terms(bullets, context), context.weights)
