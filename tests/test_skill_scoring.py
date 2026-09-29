"""Capability mode's skill rows and course order: skills and courses scored against the posting's requirements.

Each test fails if its rule is not reached: the whole-name match, the course-name condition, a skill with no capability
entry scored by its name, a row picked by the requirements it answers in full (synonyms once, neighbours never), a
skill printed once, and the short-row fill.
"""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from tailor_engine.records import Degree, Requirement, SkillRow, TaggedItem
from tailor_engine.selection import skills_and_courses as sections
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

VOCABULARY = {
    "adversarial machine learning": "AI security",
    "agent containment and permissions": "AI security",
    "code review and security assessment": "Application and platform security",
    "applied cryptography": "Application and platform security",
    "threat modelling": "Application and platform security",
    "authentication and IAM": "Application and platform security",
}


def wanted(
    name: str, capabilities: Sequence[str] = (), conditions: Sequence[str] = (), required: bool = True
) -> Requirement:
    return {
        "name": name,
        "required": required,
        "tokens": [name],
        "capabilities": list(capabilities),
        "capability": capabilities[0] if capabilities else None,
        "conditions": list(conditions),
    }


def row(row_id: str, members: Sequence[str], prior: float = 0.5) -> SkillRow:
    return SkillRow(
        row_id, (TaggedItem(row_id.title(), {}),), tuple(TaggedItem(text, {}) for text in members), prior=prior
    )


class Fit(unittest.TestCase):
    def test_a_skill_is_named_whole_a_course_by_its_name_stating_a_condition(self) -> None:
        python = wanted("Python", conditions=["Python"])
        fit = sections.requirement_fit
        self.assertEqual(fit("Python", [], python, VOCABULARY, whole_name=True), 1.0)
        self.assertEqual(fit("Python for Data", [], python, VOCABULARY, whole_name=True), 0.0)
        self.assertEqual(fit("Problem Solving and Python Programming", [], python, VOCABULARY, whole_name=False), 1.0)

    def test_a_capability_answers_in_full_and_a_neighbour_in_part(self) -> None:
        review = wanted("Code review", ["code review and security assessment"])
        fit = sections.requirement_fit
        self.assertEqual(
            fit("Secure Code Review", ["code review and security assessment"], review, VOCABULARY, whole_name=True), 1.0
        )
        self.assertEqual(
            fit("Cryptography", ["applied cryptography"], review, VOCABULARY, whole_name=True),
            DEFAULT_WEIGHTS.skill_transferable,
        )

    def test_a_skill_with_no_capability_entry_is_scored_by_its_name(self) -> None:
        # Python has no entry, so only its name can answer; entries come first, in their own order.
        requirements = [
            wanted("Python", conditions=["Python"]),
            wanted("Code review", ["code review and security assessment"]),
        ]
        fit = sections.skill_fit(
            ["Python", "Secure Code Review"],
            {"Secure Code Review": ["code review and security assessment"]},
            requirements,
            VOCABULARY,
            whole_name=True,
        )
        self.assertEqual(list(fit.fits), ["Secure Code Review", "Python"])
        self.assertTrue(fit.direct("Python"))
        self.assertAlmostEqual(fit.score("Python"), 0.5)


class Rows(unittest.TestCase):
    def pick(
        self, rows: dict[str, SkillRow], requirements: list[Requirement], capabilities: dict[str, list[str]], shown: int
    ) -> list[sections.ChosenSkillRow]:
        fit = sections.skill_fit(capabilities, capabilities, requirements, VOCABULARY, whole_name=True)
        return sections.pick_skill_rows(rows, {}, fit, rows_shown=shown)

    def test_synonyms_answering_one_requirement_count_once(self) -> None:
        # Four synonyms answer one preferred requirement; one skill answers a required one. Summed skill scores would
        # rank the synonyms' row first (4 x 1/4 against 3/4); by requirements answered the required one wins.
        adversarial = ["Adversarial ML", "Evasion Attacks", "Model Robustness", "Adversarial Examples"]
        rows = {"ai": row("ai", adversarial, prior=0.9), "appsec": row("appsec", ["Secure Code Review"], prior=0.1)}
        capabilities = {name: ["adversarial machine learning"] for name in adversarial}
        capabilities["Secure Code Review"] = ["code review and security assessment"]
        requirements = [
            wanted("Adversarial AI", ["adversarial machine learning"], required=False),
            wanted("Code review", ["code review and security assessment"]),
        ]
        chosen = self.pick(rows, requirements, capabilities, shown=1)
        self.assertEqual([chosen_row.row_id for chosen_row in chosen], ["appsec"])

    def test_partial_fits_never_pick_a_row(self) -> None:
        # Cryptography is a neighbour of all three requirements; Threat Modeling answers one in full.
        rows = {
            "systems": row("systems", ["Cryptography"], prior=0.9),
            "detect": row("detect", ["Threat Modeling"], prior=0.1),
        }
        capabilities = {"Cryptography": ["applied cryptography"], "Threat Modeling": ["threat modelling"]}
        requirements = [
            wanted("Threat modeling", ["threat modelling"], required=False),
            wanted("Code review", ["code review and security assessment"]),
            wanted("Identity", ["authentication and IAM"]),
        ]
        chosen = self.pick(rows, requirements, capabilities, shown=1)
        self.assertEqual([chosen_row.row_id for chosen_row in chosen], ["detect"])

    def test_a_skill_in_two_rows_is_printed_once(self) -> None:
        rows = {
            "first": row("first", ["Threat Modeling", "OSINT"]),
            "second": row("second", ["Threat Modeling", "Code Review"]),
        }
        capabilities = {
            "Threat Modeling": ["threat modelling"],
            "OSINT": [],
            "Code Review": ["code review and security assessment"],
        }
        requirements = [
            wanted("Threat modeling", ["threat modelling"]),
            wanted("Code review", ["code review and security assessment"]),
        ]
        chosen = self.pick(rows, requirements, capabilities, shown=2)
        listed = [name for chosen_row in chosen for name in chosen_row.members]
        self.assertEqual(listed.count("Threat Modeling"), 1)

    def test_a_short_row_is_filled_to_the_minimum_and_no_further(self) -> None:
        others = [f"Other {index}" for index in range(6)]
        rows = {"detect": row("detect", ["Threat Modeling", *others])}
        capabilities = {"Threat Modeling": ["threat modelling"], **{name: [] for name in others}}
        chosen = self.pick(rows, [wanted("Threat modeling", ["threat modelling"])], capabilities, shown=1)
        self.assertEqual(len(chosen[0].members), sections.MIN_ROW_SKILLS)
        self.assertEqual(chosen[0].members[0], "Threat Modeling")


class Courses(unittest.TestCase):
    def test_a_course_answering_a_requirement_leads(self) -> None:
        courses = tuple(TaggedItem(name, {}) for name in ("Accounting", "Cryptography", "Botany"))
        degree = Degree("d", "U", "BSc", courses=courses)
        fit = sections.skill_fit(
            ["Accounting", "Cryptography", "Botany"],
            {"Cryptography": ["applied cryptography"]},
            [wanted("Encryption", ["applied cryptography"])],
            VOCABULARY,
            whole_name=False,
        )
        scores = {name: fit.score(name) for name in fit.fits}
        self.assertEqual(sections.pick_courses(degree, {}, set(), scores=scores)[0], "Cryptography")
        self.assertEqual(sections.pick_courses(degree, {}, set())[0], "Accounting")


if __name__ == "__main__":
    unittest.main()
