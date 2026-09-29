"""Capability mode: a requirement is met when a chosen bullet demonstrates the capability it asks for.

Word mode, now retired, let one generic word answer a whole requirement ("caching" in a media player met
"inference optimization"). Here each requirement has been mapped onto the capability vocabulary by a model, once per
posting, and each bullet's capabilities are authored in the library, so credit follows what the work was:

  the capability itself on the page            1.0
  another capability in the same domain        `transferable` (0.5)
  a condition it names (a language, tool,      x `missing_condition` (0.5) when nothing visible on the
    degree) missing from the page              page states it
  no capability, only conditions               1.0 if a condition or one of its words is visible, else 0

Conditions are checked against the text the page shows: the page outside the projects (including each
degree's evidence words, which stand as aliases for the degree), the bullets, and the conditions a bullet's
own map row establishes; and against the chosen skill rows, where a condition must be a whole skill's name
("Python" is met by the skill Python; "LLM" is not met by the skill LLM Red Teaming, which met a required
"Agentic AI Systems" in an earlier version). A bullet's evidence words never meet a requirement here. The posting's
topic mix comes from its own mapped requirements, so editing projects, bullets, skill rows or courses cannot change
it.

Two things still come from words: the word-lookup demand titles the skill rows, fills them and breaks ties among
courses (see `skills_and_courses`), and bullets' evidence lists measure redundancy in the score's cost term.

Bullets take topic tags from their capabilities' domains, so scoring and ordering run unchanged. A project
whose work sits in the families the posting asks for gets a small boost to its standing (the
`family_boost` weight): an edge in the ranking, not a fence around the family.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable

from ..library.projects import BulletCapabilities
from ..records import Bullet, Project, Requirement, RequirementCredit
from ..text_search import WORD_SEPARATORS, contains_any, join_texts
from .requirement_weights import requirement_weight, weighted_share
from .tags import normalise
from .weights import DEFAULT_WEIGHTS, Weights

# ─────────────────────────────────────────────────────────────
# Bullets as capabilities
# ─────────────────────────────────────────────────────────────


def name_key(name: str) -> str:
    """A skill or condition name compared whole: case, spaces, hyphens, underscores and slashes ignored."""
    return re.sub(WORD_SEPARATORS, " ", name).strip().casefold()


def capabilities_of(requirement: Requirement) -> list[str]:
    """The capabilities a requirement asks for, any one of which meets it; [] for a bare condition."""
    if "capabilities" in requirement:
        return list(requirement["capabilities"])
    capability = requirement.get("capability")
    return [capability] if capability else []


def topic_tags(
    capabilities: list[str], vocabulary: dict[str, str], domain_tags: dict[str, str], weights: Weights = DEFAULT_WEIGHTS
) -> dict[str, float]:
    """A bullet's topics, from its capabilities' domains.

    The first capability is the one it mainly shows and takes `main_capability_share` of the weight; the others share
    the rest.
    """
    if not capabilities:
        return {}
    count = len(capabilities)
    shares = (
        [1.0]
        if count == 1
        else [weights.main_capability_share] + [weights.other_capabilities_share / (count - 1)] * (count - 1)
    )
    tags: dict[str, float] = {}
    for capability, weight in zip(capabilities, shares, strict=True):
        tag = domain_tags[vocabulary[capability]]
        tags[tag] = tags.get(tag, 0.0) + weight
    return tags


def capability_bullets(
    bullets: list[Bullet],
    bullet_capabilities: dict[str, BulletCapabilities],
    vocabulary: dict[str, str],
    domain_tags: dict[str, str],
    weights: Weights = DEFAULT_WEIGHTS,
) -> list[Bullet]:
    """Copies of the bullets carrying their capabilities, with tags from those capabilities' domains.

    A bullet with no capability (a competition placing) takes its project's average tags, so it is relevant exactly when
    its project is.
    """
    entries = {
        bullet.id: bullet_capabilities.get(bullet.id, BulletCapabilities(capabilities=[], conditions=[]))
        for bullet in bullets
    }
    own_tags = {
        bullet.id: topic_tags(entries[bullet.id]["capabilities"], vocabulary, domain_tags, weights)
        for bullet in bullets
    }
    project_profiles: dict[str, list[dict[str, float]]] = {}
    for bullet in bullets:
        if own_tags[bullet.id]:
            project_profiles.setdefault(bullet.parent, []).append(own_tags[bullet.id])
    copies = []
    for bullet in bullets:
        entry = entries[bullet.id]
        tags = dict(own_tags[bullet.id])
        if not tags and project_profiles.get(bullet.parent):
            profiles = project_profiles[bullet.parent]
            for profile in profiles:
                for tag, weight in profile.items():
                    tags[tag] = tags.get(tag, 0.0) + weight / len(profiles)
        copies.append(
            dataclasses.replace(
                bullet, tags=tags, capabilities=list(entry["capabilities"]), conditions=list(entry["conditions"])
            )
        )
    return copies


# ─────────────────────────────────────────────────────────────
# Coverage
# ─────────────────────────────────────────────────────────────


def visible_text(bullets: list[Bullet], outside_text: str) -> str:
    """What conditions are checked against.

    The text outside the projects, the bullets, and the conditions their map rows establish. Evidence words are not in
    it (see the module docstring).
    """
    return join_texts(
        [
            outside_text,
            " ".join(bullet.text for bullet in bullets),
            " ".join(condition for bullet in bullets for condition in bullet.conditions),
        ]
    )


class CapabilityMatcher:
    """Capability mode's answers to the selector's questions.

    The questions are the `Matcher` protocol in `selection.scoring`.
    """

    def __init__(self, vocabulary: dict[str, str], weights: Weights = DEFAULT_WEIGHTS, skills: Iterable[str] = ()):
        self.vocabulary = vocabulary
        self.weights = weights
        self.skill_names = {name_key(skill) for skill in skills}

    def shows(self, words: list[str], text: str) -> bool:
        """Whether the page states one of `words`: in `text` as a whole word, or as the whole name of a listed skill."""
        return contains_any(words, text) or any(name_key(word) in self.skill_names for word in words)

    def requirement_credit(self, requirement: Requirement, bullets: list[Bullet], text: str) -> float:
        """How much of one requirement the page meets, from 0 to 1, as the table in the module docstring sets out."""
        capabilities = capabilities_of(requirement)
        conditions = requirement.get("conditions") or []
        if not capabilities:
            # A requirement that is only a condition (a degree, a language) is met by visible text alone,
            # its own alternative spellings ("Bachelor", "BS") included.
            return 1.0 if self.shows(conditions + list(requirement.get("tokens") or []), text) else 0.0
        shown = {name for bullet in bullets for name in bullet.capabilities}
        if shown.intersection(capabilities):
            value = 1.0
        elif {self.vocabulary.get(name) for name in capabilities} & {self.vocabulary[name] for name in shown}:
            value = self.weights.transferable
        else:
            return 0.0
        if conditions and not self.shows(conditions, text):
            value *= self.weights.missing_condition
        return value

    def coverage(self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str) -> float:
        text = visible_text(bullets, outside_text)
        return weighted_share(
            requirements, self.weights, lambda requirement: self.requirement_credit(requirement, bullets, text)
        )

    def met_requirement_names(
        self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str
    ) -> set[str]:
        """Requirements met in full; one met by a neighbouring capability or without its condition is not."""
        text = visible_text(bullets, outside_text)
        return {
            requirement["name"]
            for requirement in requirements
            if self.requirement_credit(requirement, bullets, text) >= 1.0
        }

    def meets_unmet_required_item(
        self,
        opening_bullets: list[Bullet],
        rest_of_page: list[Bullet],
        requirements: list[Requirement],
        outside_text: str,  # noqa: ARG002  text outside the projects shows no capability; the protocol passes it
    ) -> bool:
        """Whether these bullets directly show a required capability no other project on the page shows."""
        capabilities_elsewhere = {name for bullet in rest_of_page for name in bullet.capabilities}
        capabilities_opening = {name for bullet in opening_bullets for name in bullet.capabilities}
        return any(
            requirement["required"]
            and capabilities_opening.intersection(capabilities_of(requirement))
            and not capabilities_elsewhere.intersection(capabilities_of(requirement))
            for requirement in requirements
        )

    def requirement_credits(
        self, bullets: list[Bullet], requirements: list[Requirement], outside_text: str
    ) -> list[RequirementCredit]:
        """Each requirement's credit, and the bullets that earn it."""
        text = visible_text(bullets, outside_text)
        credits: list[RequirementCredit] = []
        for requirement in requirements:
            credit = self.requirement_credit(requirement, bullets, text)
            credits.append(
                {
                    "name": requirement["name"],
                    "required": requirement["required"],
                    "credit": credit,
                    "bullets": self.earning_bullets(requirement, bullets) if credit else [],
                }
            )
        return credits

    def earning_bullets(self, requirement: Requirement, bullets: list[Bullet]) -> list[str]:
        """The bullets showing the requirement's capability; failing those, another capability in its domain.

        For a requirement that is only conditions, the bullets whose own visible text states one.
        """
        capabilities = capabilities_of(requirement)
        if not capabilities:
            words = (requirement.get("conditions") or []) + list(requirement.get("tokens") or [])
            return [bullet.id for bullet in bullets if contains_any(words, visible_text([bullet], ""))]
        direct = [bullet.id for bullet in bullets if set(capabilities).intersection(bullet.capabilities)]
        if direct:
            return direct
        domains = {self.vocabulary.get(name) for name in capabilities}
        return [
            bullet.id for bullet in bullets if any(self.vocabulary[name] in domains for name in bullet.capabilities)
        ]


def capability_demand(
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    domain_tags: dict[str, str],
    weights: Weights = DEFAULT_WEIGHTS,
) -> dict[str, float]:
    """The posting's topic mix, from the domains of the capabilities its requirements were mapped to.

    A requirement naming alternatives in several domains shares its weight equally among them.
    """
    weight_by_tag: dict[str, float] = {}
    for requirement in requirements:
        domains = list(dict.fromkeys(vocabulary[name] for name in capabilities_of(requirement) if name in vocabulary))
        for domain in domains:
            tag = domain_tags[domain]
            weight_by_tag[tag] = weight_by_tag.get(tag, 0.0) + requirement_weight(requirement, weights) / len(domains)
    return normalise(weight_by_tag)


# ─────────────────────────────────────────────────────────────
# Family boost
# ─────────────────────────────────────────────────────────────


def posting_families(
    demand: dict[str, float], families: dict[str, list[str]], domain_tags: dict[str, str]
) -> dict[str, float]:
    """How much of the posting's demand falls in each family, each share capped at 1.

    A domain in two families counts in both, so the shares can add to more than 1.
    """
    domain_of_tag = {tag: domain for domain, tag in domain_tags.items()}
    shares: dict[str, float] = {}
    for tag, weight in demand.items():
        for family in families.get(domain_of_tag.get(tag, ""), []):
            shares[family] = min(1.0, shares.get(family, 0.0) + weight)
    return shares


def family_overlap(
    bullets: list[Bullet],
    demand: dict[str, float],
    families: dict[str, list[str]],
    vocabulary: dict[str, str],
    domain_tags: dict[str, str],
) -> dict[str, float]:
    """Per project, from 0 to 1: how much of its work lies in the families the posting asks for.

    It is the share of the project's capability mentions in each family, weighted by how much of the posting falls in
    that family.
    """
    wanted = posting_families(demand, families, domain_tags)
    mentions: dict[str, dict[str, float]] = {}
    totals: dict[str, int] = {}
    for bullet in bullets:
        for capability in bullet.capabilities:
            totals[bullet.parent] = totals.get(bullet.parent, 0) + 1
            for family in families.get(vocabulary.get(capability, ""), []):
                profile = mentions.setdefault(bullet.parent, {})
                profile[family] = profile.get(family, 0.0) + 1
    return {
        project_id: min(
            1.0, sum(wanted.get(family, 0.0) * count / totals[project_id] for family, count in profile.items())
        )
        for project_id, profile in mentions.items()
    }


def family_factors(projects: dict[str, Project], overlap: dict[str, float], boost: float) -> dict[str, float]:
    """Each project's standing multiplier, 1 + boost x overlap; {} (every project 1) when the boost is off."""
    if boost <= 0:
        return {}
    return {project_id: 1.0 + boost * overlap.get(project_id, 0.0) for project_id in projects}
