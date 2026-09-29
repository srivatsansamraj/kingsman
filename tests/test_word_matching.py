"""The word-based demand and the selector's move rules, on generated bullets.

Whole-word matching, empty inputs, the weak-project rule, and stable tie order.
"""

from __future__ import annotations

import re
import unittest

from tailor_engine.records import Bullet, Project, Requirement
from tailor_engine.selection.capability_matching import CapabilityMatcher
from tailor_engine.selection.scoring import PageContext
from tailor_engine.selection.selector import moves
from tailor_engine.selection.weights import DEFAULT_WEIGHTS
from tailor_engine.selection.word_matching import word_demand
from tailor_engine.text_search import token_pattern

VOCABULARY = {
    "model training": "Machine learning",
    "formal verification": "Systems",
    "backend services and APIs": "Backend and data systems",
}


def context(projects: dict[str, Project], requirements: list[Requirement]) -> PageContext:
    return PageContext.create(
        requirements,
        {},
        projects=projects,
        adjacency=set(),
        outside_text="",
        matcher=CapabilityMatcher(VOCABULARY),
        weights=DEFAULT_WEIGHTS,
    )


def wanted(name: str, capability: str) -> Requirement:
    return {"name": name, "tokens": [name], "required": True, "capability": capability, "conditions": []}


def bullet(bullet_id: str, capability: str) -> Bullet:
    return Bullet(
        bullet_id, bullet_id.split(".")[0], f"Did {capability}.", {"sw.backend": 1.0}, [], capabilities=[capability]
    )


class WholeWords(unittest.TestCase):
    CASES = (
        ("MS", "8.3 ms at 120 Hz", False),  # short capitals match only in capitals
        ("MS", "MS in Information Security", True),
        ("TA", "data pipeline", False),
        ("TA", "Teaching Assistant, TA", True),
        ("Python", "python, c", True),
        ("C", "C#", False),  # a one-letter language is not found inside C# or C++
        ("C", "Python, C, Java", True),
        ("OS internals", "os internals", True),
        ("Semgrep", "semgrep", True),
        ("GDB", "gdb", False),
    )

    def test_cases(self) -> None:
        for token, text, expected in self.CASES:
            with self.subTest(token=token, text=text):
                self.assertEqual(bool(re.search(token_pattern(token), text)), expected)


class EmptyInputs(unittest.TestCase):
    def test_empty_requirements_have_defined_results(self) -> None:
        self.assertEqual(word_demand([], [], {}), ({}, 1.0))
        self.assertEqual(CapabilityMatcher(VOCABULARY).coverage([], [], ""), 1.0)


class Moves(unittest.TestCase):
    def test_a_weak_project_opens_only_with_a_bullet_that_meets_an_unmet_required_item(self) -> None:
        weak = {"L": Project("L", "Low standing", complexity=0.1, brand=0.1)}
        candidates = [bullet("L.1", "model training"), bullet("L.2", "formal verification")]
        blocked = moves([], candidates, context(weak, [wanted("Go", "backend services and APIs")]))
        allowed = moves([], candidates, context(weak, [wanted("ML", "model training")]))
        self.assertEqual(blocked, [])
        # The verification bullet may not ride in on the training bullet's capability.
        self.assertEqual([move.id for move in allowed], ["L.1"])

    def test_tied_projects_are_offered_in_id_order(self) -> None:
        projects = {"B": Project("B", "Second"), "A": Project("A", "First")}
        candidates = [bullet("B.1", "model training"), bullet("A.1", "model training")]
        offered = moves([], candidates, context(projects, [wanted("ML", "model training")]))
        self.assertEqual([move.id for move in offered], ["A.1", "B.1"])


if __name__ == "__main__":
    unittest.main()
