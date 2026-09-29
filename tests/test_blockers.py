"""The deal-breakers shown at the top of a job: each one found, and none from a posting without them."""

from __future__ import annotations

import unittest

from tailor_dashboard.display import blockers_of
from tailor_engine.records import Reading


def reading(*requirements: tuple[str, bool]) -> Reading:
    return {"requirements": [{"name": name, "tokens": [name], "required": required} for name, required in requirements]}


class Blockers(unittest.TestCase):
    def test_each_deal_breaker_is_named_in_order(self) -> None:
        facts = {"sponsorship": "no", "years_required": 5, "clearance_required": False}
        found = blockers_of(facts, reading(("U.S. citizenship", True), ("Top Secret Clearance", False)))
        self.assertEqual(
            found,
            ["U.S. citizenship required", "Security clearance preferred", "No visa sponsorship", "Asks 5+ years"],
        )

    def test_a_clearance_the_posting_text_requires_is_required(self) -> None:
        self.assertEqual(blockers_of({"clearance_required": True}, None), ["Security clearance required"])

    def test_an_ordinary_posting_has_none(self) -> None:
        facts = {"sponsorship": "yes", "years_required": 2, "clearance_required": False}
        self.assertEqual(blockers_of(facts, reading(("Python", True), ("Threat modeling", False))), [])


if __name__ == "__main__":
    unittest.main()
