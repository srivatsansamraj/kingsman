"""How much each requirement counts, and the share of that weight a page meets.

The matcher, both kinds of demand, and skill and course fit use it.
"""

from __future__ import annotations

from collections.abc import Callable

from ..records import Requirement
from .weights import DEFAULT_WEIGHTS, Weights


def requirement_weight(requirement: Requirement, weights: Weights = DEFAULT_WEIGHTS) -> float:
    """A required item counts `required_weight` times a preferred one."""
    return weights.required_weight if requirement["required"] else 1.0


def weighted_share(requirements: list[Requirement], weights: Weights, credit: Callable[[Requirement], float]) -> float:
    """The share of the requirements' weight met; 1 when there are no requirements.

    Each requirement earns `credit(requirement)`, from 0 to 1, of its weight.
    """
    if not requirements:
        return 1.0
    met = total = 0.0
    for requirement in requirements:
        weight = requirement_weight(requirement, weights)
        met += weight * credit(requirement)
        total += weight
    return met / total
