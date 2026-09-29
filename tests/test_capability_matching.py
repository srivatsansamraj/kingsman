"""Capability mode, on generated bullets.

The false matches word mode made (now retired) must earn nothing here, or half where the capability is shown but a
named condition is not. Each case has the shape of one found on a real page. Plus the family boost, the
weak-project rule in capability terms, and incoherence recorded from 0 to 1.
"""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from tailor_engine.library.projects import BulletCapabilities
from tailor_engine.records import Bullet, Project, Requirement
from tailor_engine.selection import capability_matching as capability
from tailor_engine.selection.project_values import project_values
from tailor_engine.selection.scoring import PageContext, incoherence
from tailor_engine.selection.selector import may_open
from tailor_engine.selection.weights import DEFAULT_WEIGHTS
from tailor_engine.text_search import contains_any, join_texts

VOCABULARY = {
    "model optimisation and deployment": "Machine learning",
    "model training": "Machine learning",
    "mobile app development": "App development",
    "backend services and APIs": "Backend and data systems",
    "debugging and defect diagnosis": "Engineering practice",
    "build and release engineering": "Engineering practice",
    "team leadership": "Leadership",
    "threat modelling": "Application and platform security",
    "secure system architecture": "Application and platform security",
    "formal verification": "Systems",
}
DOMAIN_TAGS = {
    "Machine learning": "ai.ml",
    "App development": "sw.app",
    "Backend and data systems": "sw.backend",
    "Engineering practice": "sw.practice",
    "Leadership": "pro.leadership",
    "Application and platform security": "sec.appsec",
    "Systems": "sys.systems",
}
MATCHER = capability.CapabilityMatcher(VOCABULARY)


def make_bullet(
    bullet_id: str, text: str, evidence: list[str], capabilities: list[str], conditions: Sequence[str] = ()
) -> Bullet:
    return Bullet(
        bullet_id,
        bullet_id.split(".")[0],
        text,
        capability.topic_tags(capabilities, VOCABULARY, DOMAIN_TAGS),
        evidence,
        capabilities=capabilities,
        conditions=list(conditions),
    )


def requirement(
    name: str, tokens: list[str], capability_name: str | None, conditions: Sequence[str] = ()
) -> Requirement:
    return {
        "name": name,
        "required": True,
        "tokens": tokens,
        "capability": capability_name,
        "conditions": list(conditions),
    }


class FalseMatches(unittest.TestCase):
    def assert_word_mode_credits_but_capability_mode_does_not(
        self, bullet: Bullet, wanted: Requirement, expected: float
    ) -> None:
        # One of the requirement's words is among the bullet's words, which met it in full in word mode.
        self.assertTrue(contains_any(wanted["tokens"], join_texts([bullet.text, " ".join(bullet.evidence)])))
        self.assertEqual(MATCHER.coverage([bullet], [wanted], ""), expected)

    def test_image_caching_is_not_inference_optimisation(self) -> None:
        bullet = make_bullet(
            "V.1", "Cached decoded images so scrolling stays smooth.", ["caching"], ["mobile app development"]
        )
        wanted = requirement(
            "Inference optimization", ["inference optimization", "caching"], "model optimisation and deployment"
        )
        self.assert_word_mode_credits_but_capability_mode_does_not(bullet, wanted, 0.0)

    def test_a_software_regression_is_not_supervised_learning(self) -> None:
        bullet = make_bullet(
            "X.1",
            "Found and fixed two defects in another team's pipeline and sent both upstream.",
            ["debugging", "regression"],
            ["debugging and defect diagnosis"],
        )
        wanted = requirement(
            "Supervised Learning", ["supervised learning", "classification", "regression"], "model training"
        )
        self.assert_word_mode_credits_but_capability_mode_does_not(bullet, wanted, 0.0)

    def test_backend_work_without_go_is_only_half(self) -> None:
        bullet = make_bullet(
            "B.1", "Built the service's API in Python.", ["backend", "Go"], ["backend services and APIs"]
        )
        wanted = requirement("Backend in Go", ["Go", "backend"], "backend services and APIs", ["Go"])
        self.assert_word_mode_credits_but_capability_mode_does_not(bullet, wanted, DEFAULT_WEIGHTS.missing_condition)

    def test_isolating_a_dependency_is_not_leading_a_team(self) -> None:
        bullet = make_bullet(
            "V.3",
            "Isolated a dependency behind one interface so its migration touched one file.",
            ["migration", "led project"],
            ["build and release engineering"],
        )
        wanted = requirement("Led cross-team migration", ["led project", "migration"], "team leadership")
        self.assert_word_mode_credits_but_capability_mode_does_not(bullet, wanted, 0.0)


class Credit(unittest.TestCase):
    def test_a_capability_in_the_same_domain_is_transferable(self) -> None:
        wanted = requirement("Security design reviews", ["design review"], "secure system architecture")
        exact = make_bullet("A.1", "Designed the service's trust boundaries.", [], ["secure system architecture"])
        neighbour = make_bullet("A.2", "Threat-modelled the upload path.", [], ["threat modelling"])
        self.assertEqual(MATCHER.coverage([exact], [wanted], ""), 1.0)
        self.assertEqual(MATCHER.coverage([neighbour], [wanted], ""), DEFAULT_WEIGHTS.transferable)

    def test_hidden_evidence_cannot_meet_a_condition(self) -> None:
        wanted = requirement("Python", ["Python"], None, ["Python"])
        stated = make_bullet("P.1", "Wrote the checker.", [], ["formal verification"], conditions=["Python"])
        hidden = make_bullet("P.2", "Wrote the checker.", ["Python"], ["formal verification"])
        self.assertEqual(MATCHER.coverage([stated], [wanted], ""), 1.0)
        self.assertEqual(MATCHER.coverage([hidden], [wanted], ""), 0.0)

    def test_a_requirement_naming_alternatives_is_met_by_any_one(self) -> None:
        # "agent frameworks, LLM orchestration or evaluation harnesses" had no single capability, so got none.
        # The second alternative sits in another domain, so the first alone would give nothing.
        wanted: Requirement = {
            **requirement("Deployment or verification", ["harness"], "model optimisation and deployment"),
            "capabilities": ["model optimisation and deployment", "formal verification"],
        }
        second = make_bullet("K.1", "Extended the harness generator.", [], ["formal verification"])
        self.assertEqual(MATCHER.coverage([second], [wanted], ""), 1.0)
        self.assertEqual(MATCHER.earning_bullets(wanted, [second]), ["K.1"])

    def test_a_listed_skill_meets_a_condition_only_by_its_whole_name(self) -> None:
        # "LLM" inside the skill LLM Red Teaming met a required "Agentic AI Systems".
        matcher = capability.CapabilityMatcher(VOCABULARY, skills=["Python", "LLM Red Teaming"])
        python = requirement("Python", ["Python"], None, ["Python"])
        agentic = requirement("Agentic AI Systems", ["LLM"], None, ["LLM"])
        self.assertEqual(matcher.coverage([], [python], ""), 1.0)
        self.assertEqual(matcher.coverage([], [agentic], ""), 0.0)
        # Text outside the skills still counts word by word.
        self.assertEqual(matcher.coverage([], [agentic], "Built LLM tooling."), 1.0)

    def test_alternatives_in_two_domains_share_the_requirements_weight(self) -> None:
        wanted: Requirement = {
            **requirement("Threat modelling or verification", [], None),
            "capabilities": ["threat modelling", "formal verification"],
        }
        demand = capability.capability_demand([wanted], VOCABULARY, DOMAIN_TAGS)
        self.assertEqual(demand, {"sec.appsec": 0.5, "sys.systems": 0.5})


class WeakProjects(unittest.TestCase):
    def test_a_weak_project_opens_only_on_its_own_capability(self) -> None:
        shows = make_bullet("W.1", "Did threat modelling.", [], ["threat modelling"])
        other = make_bullet("W.2", "Did something else.", [], ["mobile app development"])
        wanted = requirement("Threat modeling", ["threat modeling"], "threat modelling")
        context = PageContext.create(
            [wanted],
            {},
            projects={"W": Project("W", "Weak", complexity=0.1, brand=0.1)},
            adjacency=set(),
            outside_text="",
            matcher=MATCHER,
            weights=DEFAULT_WEIGHTS,
        )
        self.assertTrue(may_open("W", [], [shows], context))
        self.assertFalse(may_open("W", [], [other], context))


class FamilyBoost(unittest.TestCase):
    FAMILIES = {"Application and platform security": ["Security"], "Systems": ["Systems"]}

    def test_the_boost_raises_in_family_projects_only(self) -> None:
        in_family = make_bullet("S.1", "t", [], ["threat modelling"])
        outside = make_bullet("O.1", "t", [], ["formal verification"])
        overlap = capability.family_overlap(
            [in_family, outside], {"sec.appsec": 1.0}, self.FAMILIES, VOCABULARY, DOMAIN_TAGS
        )
        self.assertEqual((overlap["S"], overlap["O"]), (1.0, 0.0))
        projects = {
            "S": Project("S", "Security work", complexity=0.6, brand=0.5),
            "O": Project("O", "Systems work", complexity=0.6, brand=0.5),
        }
        plain, _ = project_values(projects, DEFAULT_WEIGHTS, {})
        boosted, _ = project_values(projects, DEFAULT_WEIGHTS, capability.family_factors(projects, overlap, 0.2))
        self.assertAlmostEqual(boosted["S"], plain["S"] * 1.2)
        self.assertAlmostEqual(boosted["O"], plain["O"])
        self.assertEqual(capability.family_factors(projects, overlap, 0.0), {})

    def test_a_bullet_without_a_capability_takes_its_projects_average_topics(self) -> None:
        bullets = [
            Bullet("C.1", "C", "Trained the detector.", {}, []),
            Bullet("C.2", "C", "Placed third.", {}, []),
            Bullet("C.3", "C", "Threat-modelled it.", {}, []),
        ]
        mapping: dict[str, BulletCapabilities] = {
            "C.1": {"capabilities": ["model training"], "conditions": []},
            "C.3": {"capabilities": ["threat modelling"], "conditions": []},
        }
        copies = capability.capability_bullets(bullets, mapping, VOCABULARY, DOMAIN_TAGS)
        # The average of two profiles, not the first one's, and not empty.
        self.assertEqual(copies[0].tags, {"ai.ml": 1.0})
        self.assertEqual(copies[1].tags, {"ai.ml": 0.5, "sec.appsec": 0.5})


class Incoherence(unittest.TestCase):
    def test_incoherence_is_recorded_from_0_to_1_and_weighted_only_in_the_score(self) -> None:
        # The separate scale (0.3) merged into the weight, so an even two-topic project reads 0.5, not 0.15.
        split = [
            make_bullet("M.1", "t", [], ["threat modelling"]),
            make_bullet("M.2", "t", [], ["formal verification"]),
        ]
        self.assertEqual(incoherence(split), 0.5)
        self.assertEqual(incoherence(split[:1]), 0.0)
        self.assertEqual(DEFAULT_WEIGHTS.incoherence, 0.3)


if __name__ == "__main__":
    unittest.main()
