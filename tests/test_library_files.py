"""The library files, on a small generated library.

A valid one loads; each kind of mistake is refused with the file and record named; a held project and a fragile bullet
are kept off every page; a private note never reaches the judge; an empty page is refused; a requirement the mapping
does not name is listed on the page; each requirement's credit is recorded with the bullets that earn it; a skill or
course with no capability entry is scored by its name; a page's recorded terms add up to its score; a given set of
bullets is measured without choosing, and is refused beside the projects to choose from; a page records the skill rows
and coursework it prints, and the renderer refuses a page saved before it did and names the place of a text the
template cannot hold. The capability descriptions load in the vocabulary's order, each field in the order it is sent
whatever the file's, and a missing, extra or misplaced one is refused. The example library stands in as the default
until a library/ of the user's own exists.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tailor_engine import settings
from tailor_engine.library import Library, LibraryError
from tailor_engine.reading import pipeline
from tailor_engine.records import PageResult, Reading, Requirement
from tailor_engine.rendering.document import document_from_page
from tailor_engine.rendering.judge_input import library_text
from tailor_engine.selection.page import Refused, build_page
from tailor_engine.selection.weights import DEFAULT_WEIGHTS, Weights
from tailor_engine.selection.word_matching import topic_mix_for_word, topics_by_evidence_word

VOCABULARY = """
[[tag]]
name = "security.appsec"
[[tag]]
name = "systems.os"
[[tag]]
name = "research"
[[adjacency]]
a = "security.appsec"
b = "systems.os"
"""
CAPABILITIES = """
[[domain]]
name = "Application and platform security"
tag = "sec.appsec"
families = ["Security"]
capabilities = ["threat modelling"]
[[domain]]
name = "Systems"
tag = "sys.systems"
families = ["Systems"]
capabilities = ["formal verification"]
"""
DESCRIPTIONS = """
about = "Written for the tests."

["formal verification"]
domain = "Systems"
what = "proving code correct with a model checker or a proof assistant"
not_for = "threat modelling (finding how a design can be attacked)"
examples = ["Experience with TLA+ or Coq"]

["threat modelling"]
domain = "Application and platform security"
what = "finding how a design can be attacked before it is built"
not_for = "formal verification (proving code correct)"
examples = ["Threat modelling with STRIDE", "Design reviews for security"]

["none of these"]
what = "a condition only"
not_for = "an ability named with a tool"
examples = ["5+ years of experience"]
"""
PROJECTS = """
[[project]]
id = "P"
title = "Threat model of a payment service"
facts = "coursework, 2025."
complexity = 0.4
brand = 0.3
ends = "2025-12"

[[project.note]]
private = false
text = "Graded first in the class."

[[project.note]]
private = true
text = "Keep this one off every model call."

[[project.bullet]]
id = "P.1"
text = "Threat-modelled the payment service against STRIDE."
tags = { "security.appsec" = 1.0 }
evidence = ["threat modelling", "STRIDE"]
capabilities = [{ name = "threat modelling", why = "payment service, STRIDE" }]

[[project.bullet]]
id = "P.2"
pending = true
"""
PROJECT_HEADER = PROJECTS.split("[[project.note]]")[0]
# P.2 written, for a page that holds it: a bullet with no capability.
P2 = 'text = "Presented the model to the payments team."\ntags = { "security.appsec" = 1.0 }\nevidence = []'
CAPABILITY_LINE = 'capabilities = [{ name = "threat modelling", why = "payment service, STRIDE" }]'
BULLET_TAGS = 'tags = { "security.appsec" = 1.0 }'
SKILLS = """
[[row]]
id = "security"
titles = [{ text = "Security", tags = { "security.appsec" = 1.0 } }]
members = [{ text = "Threat Modeling", tags = { "security.appsec" = 1.0 } }]
"""
EDUCATION = """
[[degree]]
id = "uni"
institution = "A University"
award = "Bachelor of Science"
courses = [{ text = "Operating Systems", tags = { "systems.os" = 1.0 } }]
"""


class GeneratedLibrary(unittest.TestCase):
    def write(self, **changes: str | None) -> Path:
        """The generated library with these files, named without ".toml", in place of its own; None leaves one out."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        files = {
            "vocabulary": VOCABULARY,
            "capabilities": CAPABILITIES,
            "capability-descriptions": DESCRIPTIONS,
            "projects": PROJECTS,
            "skills": SKILLS,
            "education": EDUCATION,
            **changes,
        }
        for name, text in files.items():
            if text is not None:
                (Path(folder.name) / f"{name}.toml").write_text(text, encoding="utf-8")
        return Path(folder.name)

    def test_a_valid_library_loads(self) -> None:
        library = Library.load(self.write())
        self.assertEqual([bullet.id for bullet in library.bullets], ["P.1", "P.2"])
        self.assertTrue(library.bullets[1].pending)
        self.assertEqual(library.bullet_capabilities["P.1"], {"capabilities": ["threat modelling"], "conditions": []})
        self.assertEqual(library.domain_tags["Systems"], "sys.systems")
        # In the vocabulary's order, whatever the file's, each field in the order Jev is sent it; the note left out.
        self.assertEqual(
            list(library.capability_descriptions), ["threat modelling", "formal verification", "none of these"]
        )
        self.assertEqual(
            library.capability_descriptions["threat modelling"],
            {
                "domain": "Application and platform security",
                "what": "finding how a design can be attacked before it is built",
                "not_for": "formal verification (proving code correct)",
                "examples": ["Threat modelling with STRIDE", "Design reviews for security"],
            },
        )
        self.assertEqual(list(library.capability_descriptions["none of these"]), ["what", "not_for", "examples"])
        # The hash follows what is sent, and not the file's note.
        noted = Library.load(self.write(**{"capability-descriptions": DESCRIPTIONS.replace("for the tests", "again")}))
        self.assertEqual(noted.descriptions_hash, library.descriptions_hash)
        edited = Library.load(self.write(**{"capability-descriptions": DESCRIPTIONS.replace("TLA+ or Coq", "Coq")}))
        self.assertNotEqual(edited.descriptions_hash, library.descriptions_hash)

    def test_a_description_s_fields_load_in_the_order_they_are_sent_whatever_the_file_s_order(self) -> None:
        # Jev's call 2 sends each description as loaded, so the request is the tuned one only in this order.
        written = """
["threat modelling"]
examples = ["Threat modelling with STRIDE"]
not_for = "formal verification (proving code correct)"
what = "finding how a design can be attacked before it is built"
domain = "Application and platform security"

["formal verification"]
domain = "Systems"
what = "proving code correct with a model checker or a proof assistant"
not_for = "threat modelling (finding how a design can be attacked)"
examples = ["Experience with TLA+ or Coq"]

["none of these"]
examples = ["5+ years of experience"]
not_for = "an ability named with a tool"
what = "a condition only"
"""
        loaded = Library.load(self.write(**{"capability-descriptions": written})).capability_descriptions
        self.assertEqual(
            [list(entry) for entry in loaded.values()],
            [["domain", "what", "not_for", "examples"]] * 2 + [["what", "not_for", "examples"]],
        )

    def test_each_kind_of_mistake_is_refused_with_its_place_named(self) -> None:
        mistakes: dict[str, dict[str, str | None]] = {
            "unknown field 'colour'": {"projects": PROJECTS.replace("brand = 0.3", 'brand = 0.3\ncolour = "red"')},
            "missing 'complexity'": {"projects": PROJECTS.replace("complexity = 0.4\n", "")},
            "'brand' should be float": {"projects": PROJECTS.replace("brand = 0.3", 'brand = "high"')},
            "tag 'security.web' is not in the vocabulary": {
                "projects": PROJECTS.replace('"security.appsec" = 1.0 }\nevidence', '"security.web" = 1.0 }\nevidence')
            },
            "does not start with its project's id": {"projects": PROJECTS.replace('id = "P.1"', 'id = "Q.1"')},
            "not in the capability vocabulary": {
                "projects": PROJECTS.replace('name = "threat modelling"', 'name = "cooking"')
            },
            "is not a tag": {"vocabulary": VOCABULARY.replace('b = "systems.os"', 'b = "systems.gpu"')},
            "used twice": {"capabilities": CAPABILITIES.replace('tag = "sys.systems"', 'tag = "sec.appsec"')},
            "ends '2025-7' should be YYYY-MM": {"projects": PROJECTS.replace('ends = "2025-12"', 'ends = "2025-7"')},
            "complexity 1.4 should be from 0 to 1": {
                "projects": PROJECTS.replace("complexity = 0.4", "complexity = 1.4")
            },
            "project P: summary should be one line": {
                "projects": PROJECTS.replace(
                    'ends = "2025-12"', 'ends = "2025-12"\nsummary = "A model.\\nOf a service."'
                )
            },
            "project P: facts should be one line": {
                "projects": PROJECTS.replace('facts = "coursework, 2025."', 'facts = """coursework,\n2025."""')
            },
            "note: missing 'private'": {"projects": PROJECTS.replace("private = false\n", "")},
            # A block copied to start a new record, its id left unchanged.
            "project P: the id is used by another project": {"projects": PROJECTS + PROJECT_HEADER},
            "bullet P.1: the id is used by another bullet": {"projects": PROJECTS.replace('id = "P.2"', 'id = "P.1"')},
            "unknown field 'requires'": {"projects": PROJECTS.replace('id = "P.1"', 'id = "P.1"\nrequires = ["P.2"]')},
            "capability: should be a table, found str 'threat modelling'": {
                "projects": PROJECTS.replace(CAPABILITY_LINE, 'capabilities = ["threat modelling"]')
            },
            "'evidence' should be a list of strings": {
                "projects": PROJECTS.replace('evidence = ["threat', 'evidence = [1, "threat')
            },
            "tag weights sum to 0.500, must be 1": {
                "projects": PROJECTS.replace(BULLET_TAGS, BULLET_TAGS.replace("1.0", "0.5"))
            },
            "tag 'security.appsec' needs a number": {
                "projects": PROJECTS.replace(BULLET_TAGS, BULLET_TAGS.replace("1.0", '"all"'))
            },
            "a bullet that is not pending needs text and tags": {
                "projects": PROJECTS.replace('text = "Threat-modelled', 'label = "Threat-modelled')
            },
            "'threat modelling' is already in Application and platform security": {
                "capabilities": CAPABILITIES.replace('["formal verification"]', '["threat modelling"]')
            },
            "domain Systems: the name is used by another domain": {
                "capabilities": CAPABILITIES + '[[domain]]\nname = "Systems"\ntag = "sys.other"\nfamilies = []\n'
                "capabilities = []\n"
            },
            "tag research: the tag is listed twice": {"vocabulary": VOCABULARY + '[[tag]]\nname = "research"\n'},
            "vocabulary.toml has no 'research' tag, which degrees carry": {
                "vocabulary": VOCABULARY.replace('[[tag]]\nname = "research"\n', "")
            },
            "row security: the id is used by another row": {"skills": SKILLS + SKILLS},
            "member: should be a table, found str 'Python'": {
                "skills": SKILLS.replace('members = [{ text = "Threat Modeling"', 'members = ["Python", { text = "x"')
            },
            "capability-descriptions.toml is missing": {"capability-descriptions": None},
            "no description of 'formal verification' (Systems)": {
                "capability-descriptions": DESCRIPTIONS.replace('["formal verification"]', '["formal proofs"]')
            },
            "'formal proofs' is not a capability in capabilities.toml": {
                "capability-descriptions": DESCRIPTIONS.replace('["formal verification"]', '["formal proofs"]')
            },
            "'formal verification' is in 'Application and platform security' here and in 'Systems'": {
                "capability-descriptions": DESCRIPTIONS.replace(
                    'domain = "Systems"', 'domain = "Application and platform security"'
                )
            },
            "no 'none of these' table": {
                "capability-descriptions": DESCRIPTIONS.replace('["none of these"]', '["nothing fits"]')
            },
            "'none of these': unknown field 'domain'": {
                "capability-descriptions": DESCRIPTIONS.replace(
                    '["none of these"]\n', '["none of these"]\ndomain = "Systems"\n'
                )
            },
            "'threat modelling': missing 'not_for'": {
                "capability-descriptions": DESCRIPTIONS.replace(
                    'not_for = "formal verification (proving code correct)"\n', ""
                )
            },
            "'formal verification': 'examples' should be a list of strings": {
                "capability-descriptions": DESCRIPTIONS.replace('["Experience with TLA+ or Coq"]', '"TLA+"')
            },
            "degree uni: the id is used by another degree": {"education": EDUCATION + EDUCATION},
            "degree uni: missing 'award'": {"education": EDUCATION.replace('award = "Bachelor of Science"\n', "")},
            "'names' should be a list of strings": {
                "education": EDUCATION.replace(
                    'award = "Bachelor of Science"\n', 'award = "Bachelor of Science"\nnames = [1]\n'
                )
            },
        }
        for message, changes in mistakes.items():
            with self.subTest(mistake=message), self.assertRaisesRegex(LibraryError, re.escape(message)):
                Library.load(self.write(**changes))

    def test_a_held_project_and_a_fragile_bullet_are_kept_off_every_page(self) -> None:
        written = PROJECTS.replace('id = "P.2"\npending = true', 'id = "P.2"\n' + P2)
        self.assertEqual(
            [bullet.id for bullet in Library.load(self.write(projects=written)).usable_bullets], ["P.1", "P.2"]
        )
        fragile = written.replace('id = "P.2"\n', 'id = "P.2"\nfragile = true\n')
        self.assertEqual([bullet.id for bullet in Library.load(self.write(projects=fragile)).usable_bullets], ["P.1"])
        held = Library.load(self.write(projects=written.replace("brand = 0.3", "brand = 0.3\nheld = true")))
        self.assertEqual((held.usable_bullets, [bullet.id for bullet in held.bullets]), ([], ["P.1", "P.2"]))

    def test_a_degree_is_met_by_its_other_names_which_point_at_no_topic(self) -> None:
        education = EDUCATION.replace(
            'award = "Bachelor of Science"\n', 'award = "Bachelor of Science"\nnames = ["bachelor\'s degree"]\n'
        )
        library = Library.load(self.write(education=education))
        self.assertIn("bachelor's degree", library.always_shown_text)
        # Unlike `evidence`, a name does not steer the courses and skill rows through the word map.
        self.assertEqual(topic_mix_for_word("bachelor's degree", topics_by_evidence_word(library.section_entries)), {})

    def test_a_syntax_error_names_the_file(self) -> None:
        with self.assertRaisesRegex(LibraryError, "projects.toml"):
            Library.load(self.write(projects=PROJECTS.replace('title = "Threat', "title = Threat")))

    def test_a_private_note_never_reaches_the_judge(self) -> None:
        text = library_text(Library.load(self.write()))
        self.assertIn("Graded first in the class.", text)
        self.assertNotIn("Keep this one off", text)

    def test_an_empty_page_is_refused(self) -> None:
        # The only project is weak and shows no required capability, so nothing may open: refused. The two preferred
        # items it meets keep the posting above the reach floor.
        library = Library.load(self.write())
        reading: Reading = {
            "requirements": [
                {"name": "Kernel work", "tokens": ["Operating Systems"], "required": True},
                {"name": "Threat modelling", "tokens": ["threat modelling"], "required": False},
                {"name": "STRIDE", "tokens": ["STRIDE"], "required": False},
            ],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Kernel work", "required": True, "capability": "formal verification", "conditions": []},
            {"name": "Threat modelling", "required": False, "capability": "threat modelling", "conditions": []},
            {"name": "STRIDE", "required": False, "capability": None, "conditions": ["STRIDE"]},
        ]
        with self.assertRaisesRegex(Refused, "no project clears the bar"):
            build_page(
                pipeline.posting_from_text("", "Kernel engineer"),
                reading,
                library,
                mapping,
                weights=Weights(open_floor=0.9),
            )

    def test_a_requirement_the_mapping_does_not_name_is_listed(self) -> None:
        library = Library.load(self.write())
        reading: Reading = {
            "requirements": [
                {"name": "Threat modelling", "tokens": ["threat modelling"], "required": True},
                {"name": "Kernel work", "tokens": ["Operating Systems"], "required": False},
            ],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []}
        ]
        result = build_page(pipeline.posting_from_text("", "Security engineer"), reading, library, mapping)
        self.assertEqual(result["unmapped"], ["Kernel work"])

    def test_each_requirement_records_its_credit_and_the_bullets_that_earn_it(self) -> None:
        design_review = (
            '["design review"]\ndomain = "Application and platform security"\nwhat = "reviewing a design"\n'
            'not_for = "threat modelling"\nexamples = []\n'
        )
        library = Library.load(
            self.write(
                capabilities=CAPABILITIES.replace('["threat modelling"]', '["threat modelling", "design review"]'),
                **{"capability-descriptions": DESCRIPTIONS + design_review},
            )
        )
        reading: Reading = {
            "requirements": [
                {"name": "Threat modelling", "tokens": ["STRIDE"], "required": True},
                {"name": "Threat modelling in Rust", "tokens": ["Rust"], "required": False},
                {"name": "Design review", "tokens": ["design review"], "required": False},
                {"name": "STRIDE", "tokens": ["STRIDE"], "required": False},
                {"name": "Degree", "tokens": ["Bachelor"], "required": False},
                {"name": "Verification", "tokens": ["Coq"], "required": False},
            ],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []},
            {
                "name": "Threat modelling in Rust",
                "required": False,
                "capability": "threat modelling",
                "conditions": ["Rust"],
            },
            {"name": "Design review", "required": False, "capability": "design review", "conditions": []},
            {"name": "STRIDE", "required": False, "capability": None, "conditions": ["STRIDE"]},
            {"name": "Degree", "required": False, "capability": None, "conditions": ["Bachelor"]},
            {"name": "Verification", "required": False, "capability": "formal verification", "conditions": []},
        ]
        # Credit from a bullet names it; credit from the degree line names none; the same domain earns half, and so does
        # the capability without its named condition.
        expected = [
            ("Threat modelling", 1.0, ["P.1"]),
            ("Threat modelling in Rust", 0.5, ["P.1"]),
            ("Design review", 0.5, ["P.1"]),
            ("STRIDE", 1.0, ["P.1"]),
            ("Degree", 1.0, []),
            ("Verification", 0.0, []),
        ]
        result = build_page(pipeline.posting_from_text("", "Security engineer"), reading, library, mapping)
        self.assertEqual(
            [(entry["name"], entry["credit"], entry["bullets"]) for entry in result["requirement_credit"]], expected
        )
        self.assertEqual([entry["required"] for entry in result["requirement_credit"]], [True] + [False] * 5)

    def test_a_skill_or_course_with_no_capability_entry_is_scored_by_its_name(self) -> None:
        # This library has no skill-capabilities.toml, so no skill or course has an entry.
        library = Library.load(
            self.write(
                education=EDUCATION.replace(
                    "courses = [", 'courses = [{ text = "Algorithms", tags = { "systems.os" = 1.0 } }, '
                )
            )
        )
        reading: Reading = {
            "requirements": [
                {"name": "Threat modelling", "tokens": ["threat modelling"], "required": True},
                {"name": "Threat Modeling", "tokens": ["Threat Modeling"], "required": False},
                {"name": "Kernel", "tokens": ["kernel"], "required": False},
            ],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []},
            {"name": "Threat Modeling", "required": False, "capability": None, "conditions": ["Threat Modeling"]},
            {"name": "Kernel", "required": False, "capability": None, "conditions": ["Operating Systems"]},
        ]
        result = build_page(pipeline.posting_from_text("", "Security engineer"), reading, library, mapping)
        self.assertEqual(list(result["skill_scores"]), ["Threat Modeling"])
        # By name the course the posting names leads; by topic the two tie and the name order puts Algorithms first.
        self.assertEqual(result["coursework"], [{"degree": "uni", "courses": ["Operating Systems", "Algorithms"]}])

    def test_the_recorded_terms_add_up_to_the_score(self) -> None:
        library = Library.load(self.write())
        reading: Reading = {
            "requirements": [{"name": "Threat modelling", "tokens": ["threat modelling"], "required": True}],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []}
        ]
        result = build_page(pipeline.posting_from_text("", "Security engineer"), reading, library, mapping)
        weights = DEFAULT_WEIGHTS
        self.assertGreater(result["depth"], 0)
        self.assertAlmostEqual(
            result["score"],
            weights.coverage * result["coverage"]
            + weights.emphasis * result["emphasis"]
            + weights.standing * result["standing"]
            - weights.incoherence * result["incoherence"]
            - weights.cost * result["cost"]
            + weights.depth * result["depth"],
            places=12,
        )

    def test_a_given_set_of_bullets_is_measured_without_choosing(self) -> None:
        library = Library.load(self.write(projects=PROJECTS.replace('id = "P.2"\npending = true', 'id = "P.2"\n' + P2)))
        reading: Reading = {
            "requirements": [{"name": "Threat modelling", "tokens": ["threat modelling"], "required": True}],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []}
        ]
        posting = pipeline.posting_from_text("", "Security engineer")
        chosen = build_page(posting, reading, library, mapping, chosen=["P.2"])
        self.assertEqual((chosen["selected"], chosen["trace"]), (["P.2"], []))
        # P.2 shows no capability, so the requirement P.1 meets is missing on a page of P.2 alone.
        self.assertEqual([entry["credit"] for entry in chosen["requirement_credit"]], [0.0])
        with self.assertRaisesRegex(ValueError, "not usable bullets"):
            build_page(posting, reading, library, mapping, chosen=["P.9"])
        with self.assertRaisesRegex(ValueError, "not both"):
            build_page(posting, reading, library, mapping, chosen=["P.1"], projects={"P"})

    def test_a_page_records_what_it_prints_and_an_older_page_is_refused(self) -> None:
        library = Library.load(self.write())
        reading: Reading = {
            "requirements": [{"name": "Threat modelling", "tokens": ["threat modelling"], "required": True}],
            "keywords": [],
        }
        mapping: list[Requirement] = [
            {"name": "Threat modelling", "required": True, "capability": "threat modelling", "conditions": []}
        ]
        result = build_page(pipeline.posting_from_text("", "Security engineer"), reading, library, mapping)
        self.assertEqual(
            (result["skill_rows"], result["coursework"]),
            (
                [{"row": "security", "title": "Security", "members": ["Threat Modeling"]}],
                [{"degree": "uni", "courses": ["Operating Systems"]}],
            ),
        )
        saved_earlier = {key: value for key, value in result.items() if key not in ("skill_rows", "coursework")}
        with self.assertRaisesRegex(ValueError, "build it again with `page`"):
            document_from_page(saved_earlier)  # type: ignore[arg-type]

    def test_a_page_text_the_template_cannot_hold_is_named_by_its_place(self) -> None:
        # A page file edited by hand, with as many skill rows and courses as the template holds.
        page: PageResult = {
            "page": [{"group": "P", "title": "Threat model", "bullets": ["Threat-modelled a service.", "  "]}],
            "skill_rows": [{"row": f"row{index}", "title": "Security", "members": ["STRIDE"]} for index in range(5)],
            "coursework": [{"degree": "uni", "courses": [f"Course {index}" for index in range(8)]}],
        }
        with self.assertRaisesRegex(ValueError, "project 1 bullet 2 cannot be empty"):
            document_from_page(page)


if __name__ == "__main__":
    unittest.main()


class DefaultLibrary(unittest.TestCase):
    def test_the_example_library_stands_in_until_the_user_has_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(settings.default_library(root), root / "examples" / "library")
            (root / "library").mkdir()
            self.assertEqual(settings.default_library(root), root / "library")
