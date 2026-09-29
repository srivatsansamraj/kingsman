"""Choosing the page's bullets: four passes over the score in `scoring.py`.

  add     repeatedly take the move with the best score gain per line of page space, until none gains
  swap    exchange one chosen bullet for one left out while that raises the score (at most 200 swaps)
  fill    spend the lines left: on-topic moves first, then substantial projects, never lowering coverage
  prune   drop a weak project that no longer earns its slot, and fill again (at most one per slot)

A move is one bullet: another bullet for a project already on the page, or the bullet that opens a new
project. Every pass keeps the page feasible: its projects fit the line budget and slot count, and no bullet
sits beside one it excludes. Each accepted move is recorded in the trace with the score after it.

A weak project (standing under the `open_floor` weight) may open only with a bullet that meets a
required item nothing else on the page meets. Which requirements count as met is the matcher's question.

Each move is scored by rescoring the whole candidate page. A page takes about 1.4 s at this library's size,
against about 35 s of model calls to read a new posting; scoring moves incrementally would save little of
that and would change results through the order of floating-point sums.
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

from ..layout import PAGE_PROJECTS, PROJECT_BODY_LINES, project_body_lines
from ..records import Bullet, Project, Trace, TraceEntry
from .scoring import PageContext, bullets_by_project, depth_value, emphasis, move_relevance, score
from .weights import Weights

# The Word template's project slots; the budget that binds is lines.
MAX_PROJECTS = PAGE_PROJECTS
MAX_LINES = PROJECT_BODY_LINES
# A safety bound on the swap pass. Each swap must raise the score, so it cannot cycle; the cap only stops
# a pathological library from running long. Set by hand.
MAX_SWAPS = 200
# Two scores this close are equal; the smallest gain an add or swap must make.
SCORE_TOLERANCE = 1e-12
# Two coverages this close are equal: fill may not take a move that lowers coverage by more.
COVERAGE_TOLERANCE = 1e-9


def feasible(
    bullets: list[Bullet], projects: dict[str, Project], max_projects: int = MAX_PROJECTS, max_lines: int = MAX_LINES
) -> bool:
    """Whether the page fits the line budget and the slot count, with no bullet beside one it excludes."""
    if project_body_lines(bullets, projects) > max_lines:
        return False
    if len({bullet.parent for bullet in bullets}) > max_projects:
        return False
    chosen_ids = {bullet.id for bullet in bullets}
    return not any(excluded in chosen_ids for bullet in bullets for excluded in bullet.excludes)


def may_open(project_id: str, page: list[Bullet], opening_bullets: list[Bullet], context: PageContext) -> bool:
    """Whether `opening_bullets`, bullets of `project_id`, may be on a page with the rest of `page`.

    Always for a strong project; for a weak one only when those bullets themselves meet a required item nothing else
    does. Only their own bullets are judged, so one bullet cannot open a project on a sibling's evidence.
    """
    if not context.requirements or not context.is_weak(project_id):
        return True
    rest_of_page = [bullet for bullet in page if bullet.parent != project_id]
    return context.matcher.meets_unmet_required_item(
        opening_bullets, rest_of_page, context.requirements, context.outside_text
    )


def moves(selected: list[Bullet], candidates: list[Bullet], context: PageContext) -> list[Bullet]:
    """Every bullet that may be added to this page.

    A further bullet for an open project, or a bullet that may open a closed one. Closed projects are taken in id order
    and their bullets in id order, so ties between equal moves resolve the same way every run.
    """
    chosen_ids = {bullet.id for bullet in selected}
    open_projects = {bullet.parent for bullet in selected}
    available = [bullet for bullet in candidates if bullet.id not in chosen_ids and bullet.parent in open_projects]
    for project_id in sorted({bullet.parent for bullet in candidates} - open_projects):
        closed_project_bullets = sorted(
            (bullet for bullet in candidates if bullet.parent == project_id and bullet.id not in chosen_ids),
            key=lambda bullet: bullet.id,
        )
        available += [bullet for bullet in closed_project_bullets if may_open(project_id, selected, [bullet], context)]
    return available


# ─────────────────────────────────────────────────────────────
# Passes
# ─────────────────────────────────────────────────────────────


class _FillRank(NamedTuple):
    """Fill's order for a move, smallest first."""

    below_emphasis_floor: int  # 1 when the move leaves the page's emphasis under its floor
    not_relevant: int  # 1 when the move is under the relevance floor
    relevance_if_relevant: float  # more relevant first, among relevant moves only
    project_fill_rank: float  # then from a more substantial project
    relevance: float  # then more relevant, among the rest too
    bullet_id: str  # then by id, so equal moves resolve the same way every run


class _Search:
    """One page's search, with everything its passes share.

    The bullets to choose from, the fixed context, the page's budget, and the trace of every accepted move with the
    score after it.
    """

    def __init__(self, candidates: list[Bullet], context: PageContext, max_projects: int):
        self.candidates = candidates
        self.context = context
        self.max_projects = max_projects
        self.trace: Trace = []

    def _fits(self, page: list[Bullet]) -> bool:
        return feasible(page, self.context.projects, self.max_projects)

    def _record_move(self, event: str, ids: list[str], page: list[Bullet], page_score: float | None = None) -> None:
        page_score = score(page, self.context) if page_score is None else page_score
        self.trace.append(TraceEntry(event, ids, round(page_score, 3)))

    def add(self, selected: list[Bullet]) -> list[Bullet]:
        """Take the move with the best score gain per line of page space, until none gains."""
        context = self.context
        while True:
            best: Bullet | None = None
            best_gain, best_score = SCORE_TOLERANCE, 0.0
            current = score(selected, context)
            current_lines = project_body_lines(selected, context.projects)
            for move in moves(selected, self.candidates, context):
                trial_page = selected + [move]
                if not self._fits(trial_page):
                    continue
                # Gain per line the move takes, a project's title line included when it opens one.
                lines = max(1, project_body_lines(trial_page, context.projects) - current_lines)
                trial_score = score(trial_page, context)
                gain = (trial_score - current) / lines
                if gain > best_gain:
                    best, best_gain, best_score = move, gain, trial_score
            if best is None:
                return selected
            selected = selected + [best]
            self._record_move("add", [best.id], selected, best_score)

    def swap(self, selected: list[Bullet]) -> list[Bullet]:
        """Exchange one chosen bullet for one left out while that raises the score, at most `MAX_SWAPS` times.

        Each round takes the first improving exchange in id order.
        """
        for _attempt in range(MAX_SWAPS):
            exchange = self._first_improving_swap(selected)
            if exchange is None:
                break
            selected, outgoing, incoming, page_score = exchange
            self._record_move("swap", [outgoing.id, incoming.id], selected, page_score)
        return selected

    def _first_improving_swap(self, selected: list[Bullet]) -> tuple[list[Bullet], Bullet, Bullet, float] | None:
        """The first exchange, in id order, that raises the score: (page, outgoing, incoming, score), or None.

        An incoming bullet that would open a weak project may do so only as `may_open` allows.
        """
        context = self.context
        current = score(selected, context)
        chosen_ids = {bullet.id for bullet in selected}
        unchosen = sorted(
            (bullet for bullet in self.candidates if bullet.id not in chosen_ids), key=lambda bullet: bullet.id
        )
        for outgoing in sorted(selected, key=lambda bullet: bullet.id):
            remaining = [bullet for bullet in selected if bullet is not outgoing]
            remaining_projects = {bullet.parent for bullet in remaining}
            for incoming in unchosen:
                trial_page = remaining + [incoming]
                opens_project = incoming.parent not in remaining_projects
                if opens_project and not may_open(incoming.parent, trial_page, [incoming], context):
                    continue
                if not self._fits(trial_page):
                    continue
                trial_score = score(trial_page, context)
                if trial_score > current + SCORE_TOLERANCE:
                    return trial_page, outgoing, incoming, trial_score
        return None

    def fill(self, selected: list[Bullet], event: str) -> list[Bullet]:
        """Take moves in `_FillRank` order while any fits.

        A move may never lower coverage, and one that adds nothing to its project on the depth curve is never taken.
        """
        context = self.context
        while True:
            coverage_now = context.matcher.coverage(selected, context.requirements, context.outside_text)
            ranked: list[tuple[_FillRank, Bullet]] = []
            for move in moves(selected, self.candidates, context):
                trial_page = selected + [move]
                if not self._fits(trial_page):
                    continue
                trial_coverage = context.matcher.coverage(trial_page, context.requirements, context.outside_text)
                if trial_coverage < coverage_now - COVERAGE_TOLERANCE:
                    continue
                rank = self._fill_rank(selected, move, trial_page)
                if rank is not None:
                    ranked.append((rank, move))
            if not ranked:
                return selected
            best_move = min(ranked, key=lambda ranked_move: ranked_move[0])[1]
            selected = selected + [best_move]
            self._record_move(event, [best_move.id], selected)

    def _fill_rank(self, selected: list[Bullet], move: Bullet, trial_page: list[Bullet]) -> _FillRank | None:
        """The move's rank, or None when it would add nothing to its project on the depth curve."""
        context = self.context
        weights = context.weights
        topic_relevance = move_relevance(move, context.demand, context.adjacency, weights.core_share)
        # Weigh a move by what one more bullet adds to its project on the depth curve, so fill stops piling
        # short on-topic bullets onto one project past the point where they add anything. The relevance
        # floor then applies to this weighted relevance.
        depth_weighted_relevance = topic_relevance
        if weights.depth > 0:
            bullet_count = sum(1 for bullet in selected if bullet.parent == move.parent)
            marginal = depth_value(bullet_count + 1, weights.depth_curve) - depth_value(
                bullet_count, weights.depth_curve
            )
            if marginal <= 0:
                return None
            depth_weighted_relevance = topic_relevance * (marginal / weights.largest_depth_step)
        relevant = depth_weighted_relevance >= weights.relevance_floor
        return _FillRank(
            below_emphasis_floor=1 if emphasis(trial_page, context.demand) < weights.emphasis_floor else 0,
            not_relevant=0 if relevant else 1,
            relevance_if_relevant=-depth_weighted_relevance if relevant else 0.0,
            project_fill_rank=-context.fill_rank[move.parent],
            relevance=-depth_weighted_relevance,
            bullet_id=move.id,
        )

    def prune(self, selected: list[Bullet]) -> list[Bullet]:
        """Drop weak projects that no longer earn their slot, refilling after each.

        After fill, a weak project may stay only while its bullets still meet a required item no other project meets.
        The weakest that does not is dropped, the page refilled, and the check made again, at most once per slot.
        """
        context = self.context
        for _attempt in range(self.max_projects):
            weakest_first = sorted(
                {bullet.parent for bullet in selected},
                key=lambda project_id: (context.standing[project_id], project_id),
            )
            project_to_drop = next(
                (
                    project_id
                    for project_id in weakest_first
                    if context.is_weak(project_id)
                    and not may_open(
                        project_id,
                        [bullet for bullet in selected if bullet.parent != project_id],
                        [bullet for bullet in selected if bullet.parent == project_id],
                        context,
                    )
                ),
                None,
            )
            if project_to_drop is None:
                break
            selected = [bullet for bullet in selected if bullet.parent != project_to_drop]
            self._record_move("prune", [project_to_drop], selected)
            selected = self.fill(selected, "refill")
        return selected


def select(
    candidates: list[Bullet], context: PageContext, max_projects: int = MAX_PROJECTS
) -> tuple[list[Bullet], Trace]:
    """The chosen bullets in id order, and the trace of every accepted move."""
    search = _Search(candidates, context, max_projects)
    selected = search.add([])
    selected = search.swap(selected)
    selected = search.fill(selected, "fill")
    selected = search.prune(selected)
    return sorted(selected, key=lambda bullet: bullet.id), search.trace


# ─────────────────────────────────────────────────────────────
# Page order
# ─────────────────────────────────────────────────────────────


def page_order(
    bullets: list[Bullet],
    demand: dict[str, float],
    projects: dict[str, Project],
    standings: dict[str, float],
    weights: Weights,
) -> list[tuple[str, list[Bullet]]]:
    """Projects latest-ending first, as a resume reads; running projects first of all.

    The library has no start dates, so running projects tie on date and keep the order of their strength for this
    posting.
    """
    by_project = bullets_by_project(bullets)

    def order_key(project_id: str) -> float:
        # Negated, so the strongest sorts first.
        return -(
            weights.order_standing * standings[project_id]
            + weights.order_emphasis * emphasis(by_project[project_id], demand)
        )

    def end_sort_key(project_id: str) -> tuple[bool, dt.date]:
        project = projects[project_id]
        return project.is_running, project.ended or dt.date.min

    by_strength = sorted(by_project, key=lambda project_id: (order_key(project_id), project_id))
    return [
        (project_id, sorted(by_project[project_id], key=lambda bullet: bullet.id))
        for project_id in sorted(by_strength, key=end_sort_key, reverse=True)
    ]
