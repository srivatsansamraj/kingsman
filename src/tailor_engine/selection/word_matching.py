"""The posting's word-based demand: its mix of topics, found by looking its words up in the library.

Each evidence word carries the tags of the bullets that list it, so "Burp Suite" points at application security. The
demand titles the skill rows, orders the skills that fill a row, and ranks courses after their fit with the
requirements (`skills_and_courses`); requirements themselves are met by capabilities (`capability_matching`). Words are
matched whole (`token_pattern`), so "C" does not match "C#" and "MS" does not match "8.3 ms".

Word mode, which also met requirements by their words on the page, has been retired; its demand is what stays.
"""

from __future__ import annotations

import re

from ..records import Bullet, Requirement
from ..text_search import WORD_SEPARATORS, token_pattern
from .requirement_weights import requirement_weight
from .tags import normalise
from .weights import DEFAULT_WEIGHTS, Weights


def topics_by_evidence_word(bullets: list[Bullet]) -> dict[str, dict[str, float]]:
    """Evidence word (lower case) -> the topic mix of the bullets that list it."""
    topics: dict[str, dict[str, float]] = {}
    for bullet in bullets:
        for word in bullet.evidence:
            profile = topics.setdefault(word.lower(), {})
            for tag, weight in bullet.tags.items():
                profile[tag] = profile.get(tag, 0.0) + weight
    return {word: normalise(profile) for word, profile in topics.items()}


# A library phrase, not a single word, may stand for a longer posting phrase it appears in.
MIN_PHRASE_WORDS = 2


def topic_mix_for_word(token: str, topics: dict[str, dict[str, float]]) -> dict[str, float]:
    """The topic mix a posting word points at, or {}.

    An exact library word; else the first library word, in sorted order, that the posting word appears in; else the
    first library phrase of two or more words that appears in the posting word.
    """
    lowered = token.lower()
    if lowered in topics:
        return topics[lowered]
    pattern = token_pattern(lowered)  # lower case, so matched in any case
    for candidate in sorted(topics):
        if re.search(pattern, candidate):
            return topics[candidate]
    # A library word inside a longer posting phrase stands for it only when the library word is itself a
    # phrase: single words captured whole requirements ("cloud security" read as an IoT project through
    # "cloud").
    for candidate in sorted(topics):
        if len(re.split(WORD_SEPARATORS, candidate)) >= MIN_PHRASE_WORDS and re.search(
            token_pattern(candidate), lowered
        ):
            return topics[candidate]
    return {}


def word_demand(
    requirements: list[Requirement],
    keywords: list[str],
    topics: dict[str, dict[str, float]],
    weights: Weights = DEFAULT_WEIGHTS,
) -> tuple[dict[str, float], float]:
    """(demand, unmet share).

    Demand is the posting's topic mix, rounded to `demand_step` with anything under `demand_minimum` dropped so a stray
    word does not become a topic. The unmet share is the weight of words the library has never used.
    """
    unrounded_demand: dict[str, float] = {}
    unmet = 0.0
    demand_sources = [(requirement["tokens"], requirement_weight(requirement, weights)) for requirement in requirements]
    demand_sources += [([keyword], weights.keyword_weight) for keyword in keywords]
    for tokens, weight in demand_sources:
        hits = [profile for profile in (topic_mix_for_word(token, topics) for token in tokens) if profile]
        if not hits:
            unmet += weight
            continue
        for profile in hits:
            for tag, value in profile.items():
                unrounded_demand[tag] = unrounded_demand.get(tag, 0.0) + weight * value / len(hits)
    total = sum(unrounded_demand.values()) + unmet
    if total == 0:
        return {}, 1.0
    step = weights.demand_step
    rounded = {
        tag: round(value / total / step) * step
        for tag, value in unrounded_demand.items()
        if value / total >= weights.demand_minimum
    }
    return normalise(rounded), unmet / total
