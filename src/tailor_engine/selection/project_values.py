"""How strong each project is, for one page: its standing and its fill rank.

  merit      = merit_complexity x complexity + the rest x brand     (both authored in the library)
  recency    = max(recency_floor, 1 - recency_loss_per_year x years since the work ended)
  standing   = merit x recency                   how strongly the work argues for the candidate on any page
  fill rank  = complexity x recency              the order fill prefers projects in once relevance ties;
                                                 brand left out, so fill favours substance over fame

A page may scale both by a factor per project (capability mode's family boost); the scaled values are
used while the page is chosen, and the plain ones when it is measured and ordered.
"""

from __future__ import annotations

from ..records import Project
from .weights import Weights


def recency(project: Project, weights: Weights) -> float:
    return max(weights.recency_floor, 1.0 - weights.recency_loss_per_year * project.years_old)


def standing(project: Project, weights: Weights) -> float:
    merit = weights.merit_complexity * project.complexity + weights.merit_brand * project.brand
    return merit * recency(project, weights)


def fill_rank(project: Project, weights: Weights) -> float:
    return project.complexity * recency(project, weights)


def project_values(
    projects: dict[str, Project], weights: Weights, factors: dict[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    """(standing by project, fill rank by project), each scaled by the project's factor.

    A project without a factor is scaled by 1; multiplying by 1.0 leaves a float exactly as it was.
    """
    standings, fill_ranks = {}, {}
    for project_id, project in projects.items():
        factor = factors.get(project_id, 1.0)
        standings[project_id] = standing(project, weights) * factor
        fill_ranks[project_id] = fill_rank(project, weights) * factor
    return standings, fill_ranks
