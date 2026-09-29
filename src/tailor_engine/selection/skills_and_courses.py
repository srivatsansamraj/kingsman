"""Choosing the skill rows and the courses for one posting.

Skill rows: each skill's fit with each of the posting's mapped requirements (`SkillFit`): full when the
requirement names the skill or asks for a capability it shows, the neighbour share for another capability in the same
domain. Five rows are taken one at a time, each time the row whose skills answer in full the most requirement weight
the rows already taken do not, so rows cover different requirements and synonyms ("Adversarial ML", "Evasion Attacks")
count once; a neighbour's partial fit orders and fills, and never picks a row (partial fits on many requirements put a
cryptography row above application security on a product security page). A row lists its skills that answer a
requirement in full, best first; a row with fewer than `MIN_ROW_SKILLS` of those is filled to that many with
neighbours, then by topic fit with the posting's word-based demand. A skill is printed once. The authored prior breaks
ties and picks rows when nothing is answered. Each row wears its best-fitting title by topic fit, a utility row such
as tools sorts last, and fixed rows are shown as written.

Courses: up to 11 per degree, ranked by their weighted fit with the requirements first, the same way as skills, then
by topic fit (the document check wants at least 8, so a degree needs 8 in the library). For the topic fit, a topic the
degree has no course for is moved to its nearest topics the degree does cover, shared by how much of the degree they
make up, so a posting about something the degree never taught still ranks the closest courses first rather than
falling back to file order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..layout import COURSES_MAX, PAGE_SKILL_ROWS, SKILLS_PER_ROW
from ..records import Degree, Requirement, SkillRow, TaggedItem
from ..text_search import contains_any
from .capability_matching import capabilities_of, name_key
from .requirement_weights import requirement_weight
from .tags import normalise, tag_distance
from .weights import DEFAULT_WEIGHTS, Weights

# ─────────────────────────────────────────────────────────────
# Skill rows
# ─────────────────────────────────────────────────────────────


UTILITY_ROWS = frozenset({"tools"})
# A row with fewer skills answering a requirement in full than this is filled to this many, so no row is one or two
# skills long; past it only such skills are listed. Set by hand.
MIN_ROW_SKILLS = 4


@dataclass(frozen=True)
class ChosenSkillRow:
    row_id: str
    title: str
    members: tuple[str, ...]


def _topic_fit(tags: dict[str, float], demand: dict[str, float]) -> float:
    return sum(demand.get(tag, 0.0) * weight for tag, weight in tags.items())


def requirement_fit(
    name: str,
    capabilities: list[str],
    requirement: Requirement,
    vocabulary: dict[str, str],
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    whole_name: bool,
) -> float:
    """How much one skill or course answers one requirement, from 0 to 1.

    1 when the requirement names it (a skill by its whole name among the requirement's conditions and words; a course
    when its name states one of the conditions: "Problem Solving and Python Programming" for Python) or asks for one of
    its capabilities; `skill_transferable` for another capability in the same domain; else 0.
    """
    if whole_name:
        words = (requirement.get("conditions") or []) + list(requirement.get("tokens") or [])
        named = name_key(name) in {name_key(word) for word in words}
    else:
        named = contains_any(requirement.get("conditions") or [], name)
    wanted = capabilities_of(requirement)
    if named or set(capabilities).intersection(wanted):
        return 1.0
    shown_domains = {vocabulary[item] for item in capabilities if item in vocabulary}
    if shown_domains.intersection(vocabulary[item] for item in wanted if item in vocabulary):
        return weights.skill_transferable
    return 0.0


@dataclass(frozen=True)
class SkillFit:
    """Each skill's (or course's) fit with each of the posting's requirements, and the requirements' weights."""

    fits: dict[str, tuple[float, ...]]  # name -> its `requirement_fit` with each requirement, in order
    weights: tuple[float, ...]  # each requirement's weight

    def score(self, name: str) -> float:
        """The share of the posting's requirement weight the item answers."""
        total = sum(self.weights) or 1.0
        return sum(weight * fit for weight, fit in zip(self.weights, self.fits.get(name, ()), strict=False)) / total

    def direct(self, name: str) -> bool:
        """Whether it answers some requirement in full."""
        return any(fit >= 1.0 for fit in self.fits.get(name, ()))


def skill_fit(
    names: Iterable[str],
    capabilities_by_name: dict[str, list[str]],
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    whole_name: bool,
) -> SkillFit:
    """`requirement_fit` for every skill or course in `names` or `capabilities_by_name`, against every requirement.

    One with no entry in `capabilities_by_name` is scored by its name alone. Those with an entry come first, in its
    order, so the skill scores a page saves keep the order of skill-capabilities.toml.
    """
    return SkillFit(
        {
            name: tuple(
                requirement_fit(
                    name, capabilities_by_name.get(name, []), requirement, vocabulary, weights, whole_name=whole_name
                )
                for requirement in requirements
            )
            for name in dict.fromkeys([*capabilities_by_name, *names])
        },
        tuple(requirement_weight(requirement, weights) for requirement in requirements),
    )


def _best_title(row: SkillRow, demand: dict[str, float]) -> str:
    titles = list(row.titles)
    return max(titles, key=lambda candidate: (_topic_fit(candidate[1], demand), -titles.index(candidate)))[0]


def _row_members(row: SkillRow, demand: dict[str, float], fit: SkillFit, shown: set[str]) -> list[TaggedItem]:
    """A row's skills not yet listed: those answering a requirement in full, then neighbours and topic fit.

    The full answers come best first; neighbours, then the best topic fits, fill the row to `MIN_ROW_SKILLS`.
    """
    fresh: list[TaggedItem] = []
    for member in row.members:
        if member[0].lower() not in shown and member[0].lower() not in {item[0].lower() for item in fresh}:
            fresh.append(member)
    direct = sorted((member for member in fresh if fit.direct(member[0])), key=lambda member: -fit.score(member[0]))[
        :SKILLS_PER_ROW
    ]
    near = sorted(
        (member for member in fresh if not fit.direct(member[0]) and fit.score(member[0]) > 0),
        key=lambda member: -fit.score(member[0]),
    )
    rest = sorted(
        (member for member in fresh if fit.score(member[0]) <= 0),
        key=lambda member: (-_topic_fit(member[1], demand), member[0]),
    )
    return direct + (near + rest)[: max(0, MIN_ROW_SKILLS - len(direct))]


def _answered(members: list[TaggedItem], fit: SkillFit) -> list[bool]:
    """Per requirement, whether one of `members` answers it in full."""
    return [
        any(fit.fits.get(text, (0.0,) * len(fit.weights))[index] >= 1.0 for text, _tags in members)
        for index in range(len(fit.weights))
    ]


def pick_skill_rows(
    skill_rows: dict[str, SkillRow], demand: dict[str, float], fit: SkillFit, rows_shown: int = PAGE_SKILL_ROWS
) -> list[ChosenSkillRow]:
    """The chosen rows (see the module docstring), then the fixed rows."""
    candidates = [row for row in skill_rows.values() if not row.always_shown]
    order = {row.id: index for index, row in enumerate(candidates)}
    total = sum(fit.weights) or 1.0
    covered = [False] * len(fit.weights)
    shown: set[str] = set()
    picked: list[tuple[float, SkillRow, list[TaggedItem]]] = []
    picked_ids: set[str] = set()
    while len(picked) < min(rows_shown, len(candidates)):
        options = []
        for row in candidates:
            if row.id in picked_ids:
                continue
            members = _row_members(row, demand, fit, shown)
            if members:
                answered = _answered(members, fit)
                new_weight = sum(
                    weight
                    for weight, answers, already in zip(fit.weights, answered, covered, strict=True)
                    if answers and not already
                )
                options.append(((new_weight / total, row.prior, -order[row.id]), row, members, answered))
        if not options:
            break
        key, row, members, answered = max(options, key=lambda option: option[0])
        picked.append((key[0], row, members))
        picked_ids.add(row.id)
        covered = [already or answers for already, answers in zip(covered, answered, strict=True)]
        shown |= {text.lower() for text, _tags in members}
    picked.sort(key=lambda item: (item[1].id in UTILITY_ROWS, -item[0], item[1].id))
    chosen = [
        ChosenSkillRow(row.id, _best_title(row, demand), tuple(text for text, _tags in members))
        for _gain, row, members in picked
    ]
    return chosen + fixed_rows(skill_rows)


def fixed_rows(skill_rows: dict[str, SkillRow]) -> list[ChosenSkillRow]:
    """The rows every page shows as written, under their first title."""
    return [
        ChosenSkillRow(row.id, row.titles[0][0], tuple(text for text, _tags in row.members))
        for row in skill_rows.values()
        if row.always_shown
    ]


def listed_skills(rows: list[ChosenSkillRow]) -> list[str]:
    """Every skill the page lists: the members of the chosen rows and of the fixed rows."""
    return [member for row in rows for member in row.members]


# ─────────────────────────────────────────────────────────────
# Courses
# ─────────────────────────────────────────────────────────────


def refocus(demand: dict[str, float], supply: dict[str, float], adjacency: set[frozenset[str]]) -> dict[str, float]:
    """The demand moved onto the topics `supply` covers.

    A topic it lacks goes to its nearest covered topics, shared by their weight in the supply. A decay with distance was
    tried and removed: every topic sharing the mass is equally near, so a decay cancels out.
    """
    available = {tag: value for tag, value in supply.items() if value > 0}
    moved_demand: dict[str, float] = {}
    for tag, mass in demand.items():
        if tag in available:
            moved_demand[tag] = moved_demand.get(tag, 0.0) + mass
            continue
        if not available:
            continue
        distances = {candidate: tag_distance(tag, candidate, adjacency) for candidate in available}
        nearest = min(distances.values())
        shares = {candidate: available[candidate] for candidate in available if distances[candidate] == nearest}
        total = sum(shares.values()) or 1.0
        for candidate, share in shares.items():
            moved_demand[candidate] = moved_demand.get(candidate, 0.0) + mass * share / total
    return normalise(moved_demand)


def pick_courses(
    degree: Degree,
    demand: dict[str, float],
    adjacency: set[frozenset[str]],
    scores: dict[str, float] | None = None,
) -> list[str]:
    """Up to `COURSES_MAX` of the degree's courses, by fit with the demand moved onto its topics, ties by name.

    With `scores`, a course's score against the requirements ranks first.
    """
    scores = scores or {}
    supply: dict[str, float] = {}
    for _text, tags in degree.courses:
        for tag, weight in tags.items():
            supply[tag] = supply.get(tag, 0.0) + weight
    refocused_demand = refocus(demand, normalise(supply), adjacency)
    ranked = sorted(
        degree.courses,
        key=lambda course: (-scores.get(course[0], 0.0), -_topic_fit(course[1], refocused_demand), course[0]),
    )
    return [text for text, _tags in ranked[:COURSES_MAX]]
