"""Every number that shapes a page, in one place, each with where it came from.

Origins:

  fitted      chosen by a search against the advisors' pages, on half the benchmark, checked on the other half
  compared    chosen from a set of values run side by side on the benchmark
  judgement   the author's stated view, written as numbers
  set by hand chosen when the mechanism was built; the reason is given when one was recorded

An ablation found the two floors (`open_floor`, `relevance_floor`) behave identically anywhere
from 0 to their value: the mechanisms matter, the exact numbers do not. A new weight ships only with an
ablation showing it changes something (the rule that ablation set).

`build_page` takes a `Weights`; the benchmark varies one with `--weights name=value`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from typing import Any

# The depth curve needs a first and a later point: its largest step scales fill's relevance.
MIN_CURVE_POINTS = 2


@dataclass(frozen=True)
class Weights:
    """Every weight that shapes a page, grouped by where it acts.

    The score, project strength, gates, fill, page order, capability mode, and the posting's requirements and demand.
    """

    # ─────────────────────────────────────────────────────────────
    # The score the selector climbs (scoring.py)
    # ─────────────────────────────────────────────────────────────
    coverage: float = 0.60  # set by hand as the scale anchor the others are fitted against
    emphasis: float = 0.40  # fitted, 0.20 or 0.40
    standing: float = 0.70  # fitted, 0.35 to 1.40
    # Set by hand; reason not recorded; load-bearing in the ablation. It was 1.0 times a separate scale of 0.3,
    # merged since only their product acted, so the page records incoherence from 0 to 1.
    incoherence: float = 0.3
    cost: float = 0.35  # set by hand; weakly active in the ablation
    depth: float = 0.15  # fitted, 0 to 0.50
    # Value of a project by its bullet count on the page: one adds little, two to four carry most of it,
    # level at five. Judgement: the author's curve, written as numbers.
    depth_curve: tuple[float, ...] = (0.0, 0.15, 0.45, 0.75, 0.92, 1.00)
    # "What the posting is about": its heaviest topics carrying this share of its demand. Set by hand when
    # a share of the whole replaced a fraction of the top topic, which stopped separating near from far on
    # evenly spread postings.
    core_share: float = 0.60
    cost_liability: float = 0.4  # set by hand; reason not recorded
    cost_redundancy: float = 0.2  # set by hand; reason not recorded

    # ─────────────────────────────────────────────────────────────
    # How strong a project is (standing, fill rank)
    # ─────────────────────────────────────────────────────────────
    # Merit = this x complexity + the rest x brand. Complexity counts more because it is the durable
    # signal to a technical reader; brand stays because a resume passes a non-technical screen first
    # (set by hand with that reason).
    merit_complexity: float = 0.6
    # Recency = max(floor, 1 - loss x years since the work ended): old but hard work is discounted, never
    # erased. Judgement: the author's correction.
    recency_floor: float = 0.60
    recency_loss_per_year: float = 0.07

    # ─────────────────────────────────────────────────────────────
    # Gates
    # ─────────────────────────────────────────────────────────────
    # A project under this standing opens a slot only for a required item nothing else meets. Set by hand;
    # flat from 0 to 0.35 in the ablation.
    open_floor: float = 0.35
    # Least share of the posting's requirement weight the library must meet for a page to be built.
    # Compared: refuses the five non-engineering postings of thirty, builds every engineering one.
    reach_floor: float = 0.25
    # Hybrid mode: under this match the page is built from the projects model call 3 chose. Compared: below 0.6 the
    # model's projects were judged better 7 to 1, above it even, on 31 postings.
    model_projects_below: float = 0.60

    # ─────────────────────────────────────────────────────────────
    # Fill (spending the lines left after the score stops rising)
    # ─────────────────────────────────────────────────────────────
    # Moves at least this relevant are taken first. Set by hand; flat from 0 to 0.30 in the ablation.
    # The advisors order project pairs as this rule does 0.900 of the time, above their 0.878 with each
    # other.
    relevance_floor: float = 0.30
    # Moves that keep the page's emphasis at or above this rank first; a preference, never a veto, since
    # as a veto it left pages 11 lines short (set by hand, later made a preference).
    emphasis_floor: float = 0.60

    # ─────────────────────────────────────────────────────────────
    # Page order among projects that tie on end date
    # ─────────────────────────────────────────────────────────────
    # Order key = this x standing + the rest x emphasis: "standing weighs more than ordering, but the
    # presence matters" (judgement).
    order_standing: float = 0.6

    # ─────────────────────────────────────────────────────────────
    # Capability mode
    # ─────────────────────────────────────────────────────────────
    transferable: float = 0.5  # credit for another capability in the same domain; set by hand
    # The same for a skill or course scored against a requirement; set by hand. A partial fit orders and fills
    # a skill row and never picks one, so it moves order, not which rows show.
    skill_transferable: float = 0.5
    missing_condition: float = 0.5  # credit kept when a named condition is not visible; set by hand
    # Standing x (1 + boost x family overlap). Compared: 0.1 raised security-lane agreement 0.724 to 0.752;
    # 0.2, 0.3, 0.5 and flat additions did no better.
    family_boost: float = 0.1
    # A bullet's first capability is the one it mainly shows: this share of its topic weight, the rest
    # split among the others. Set by hand.
    main_capability_share: float = 0.6

    # ─────────────────────────────────────────────────────────────
    # The posting's requirements and its word-based demand
    # ─────────────────────────────────────────────────────────────
    # A required item against a preferred one (1), in coverage, reach and both demands, and in skill and course fit;
    # set by hand.
    required_weight: float = 3.0
    keyword_weight: float = 0.5  # a recruiter keyword's weight in the demand; set by hand
    demand_step: float = 0.05  # the demand is rounded to these steps; set by hand
    demand_minimum: float = 0.025  # topics under this share are dropped as strays; set by hand

    def __post_init__(self) -> None:
        # Refused here so a mistyped `--weights` value fails loudly instead of giving a negative complement.
        for field in fields(self):
            value = getattr(self, field.name)
            for number in value if isinstance(value, tuple) else (value,):
                if not math.isfinite(number) or number < 0:
                    raise ValueError(f"weight {field.name} must be a finite number of 0 or more; found {number}")
        shares_and_credits = (
            "merit_complexity",
            "order_standing",
            "main_capability_share",
            "core_share",
            "transferable",
            "missing_condition",
            "skill_transferable",
        )
        for share in shares_and_credits:
            if getattr(self, share) > 1:
                raise ValueError(
                    f"weight {share} is a share or credit and must be from 0 to 1; found {getattr(self, share)}"
                )
        # The demand is divided by its step, so a step of 0 would stop every page.
        if self.demand_step <= 0:
            raise ValueError("weight demand_step must be more than 0")
        if len(self.depth_curve) < MIN_CURVE_POINTS:
            raise ValueError("weight depth_curve needs at least two points")

    @property
    def merit_brand(self) -> float:
        return 1.0 - self.merit_complexity

    @property
    def order_emphasis(self) -> float:
        return 1.0 - self.order_standing

    @property
    def other_capabilities_share(self) -> float:
        return 1.0 - self.main_capability_share

    @property
    def largest_depth_step(self) -> float:
        curve = self.depth_curve
        return max(curve[index] - curve[index - 1] for index in range(1, len(curve)))


DEFAULT_WEIGHTS = Weights()


def with_changes(changes: list[str], base: Weights = DEFAULT_WEIGHTS) -> Weights:
    """Weights with `name=value` changes applied, for trying a value on the benchmark.

    A curve is written as comma-separated numbers.
    """
    known = {field.name for field in fields(Weights)}
    updates: dict[str, Any] = {}
    for change in changes:
        name, equals, value = change.partition("=")
        if name not in known:
            raise ValueError(f"no weight named '{name}'; known: {', '.join(sorted(known))}")
        try:
            if not equals:
                raise ValueError("no value")
            is_curve = isinstance(getattr(base, name), tuple)
            updates[name] = tuple(float(part) for part in value.split(",")) if is_curve else float(value)
        except ValueError as error:
            raise ValueError(f"weight {name}: '{change}' should be name=number ({error})") from error
    return replace(base, **updates)
