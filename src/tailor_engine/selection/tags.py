"""Topic tags: how far apart two are (0 the same, 1 related, 2 unrelated), and scaling a weighting of them.

Tags are written `family.topic` (`sec.appsec`, `ai.ml`). Two tags in one family are related, and so are
two tags the vocabulary lists as adjacent across families.
"""

from __future__ import annotations

MAX_TAG_DISTANCE = 2


def tag_distance(left: str, right: str, adjacency: set[frozenset[str]]) -> int:
    if left == right:
        return 0
    if frozenset((left, right)) in adjacency:
        return 1
    return 1 if left.split(".")[0] == right.split(".")[0] else 2


def normalise(values: dict[str, float]) -> dict[str, float]:
    """The weighting scaled to sum to 1; {} when it sums to 0."""
    total = sum(values.values())
    return {key: value / total for key, value in values.items()} if total else {}
