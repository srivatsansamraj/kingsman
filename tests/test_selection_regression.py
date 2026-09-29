"""The behaviour lock on the example library: any refactoring must leave these pages unchanged.

  capability mode    the example postings (examples/data): page, reach, coverage, score and refusals, recorded in
                     example-pages.json, and each page's skill rows and courses in example-documents.json
  hybrid mode        the same postings with call 3's saved answers: whose projects, the page and its match,
                     recorded in example-hybrid-pages.json

They read examples/library and examples/data only, never library/ or data/, so a user's own library changes
nothing here. The unit tests elsewhere run on generated data.
"""

from __future__ import annotations

import functools
import json
import unittest
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from tailor_engine import layout
from tailor_engine.library import Library
from tailor_engine.reading import pipeline, store
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.records import PageResult, Posting, ProjectChoice, Reading, Requirement
from tailor_engine.rendering.document import document_from_page
from tailor_engine.selection.page import Refused, build_hybrid_page, build_page
from tailor_engine.selection.project_values import recency
from tailor_engine.selection.skills_and_courses import fixed_rows
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_LIBRARY = EXAMPLES / "library"
# The example postings and their saved model answers: readings, mappings and call 3's choices.
EXAMPLE_DATA = EXAMPLES / "data"
# The library's warnings and the length of the text outside the projects.
PLACEMENTS = FIXTURES / "placements.json"
EXAMPLE_PAGES = FIXTURES / "example-pages.json"
EXAMPLE_DOCUMENTS = FIXTURES / "example-documents.json"
# Hybrid mode's pages from call 3's saved answers (examples/data/choices).
EXAMPLE_HYBRID_PAGES = FIXTURES / "example-hybrid-pages.json"
# The date the locks were recorded on; a project's age, and so its standing, is measured from it.
EXAMPLES_AS_OF = date(2026, 9, 28)
# Runs measure a project's age from today. The regression tests pin this date instead, so they do not start
# failing as the calendar moves.
BASELINE_AS_OF = date(2026, 9, 21)
HAVE_LIBRARY = (EXAMPLE_LIBRARY / "projects.toml").exists()


# The locks share these, each made once: the engine's page for a posting is built once and only read after, so the
# capability locks and the hybrid lock do not each build it again.
@functools.cache
def example_library() -> Library:
    """The example library as of the day the locks were recorded; loaded when a lock first needs it."""
    return Library.load(EXAMPLE_LIBRARY, as_of=EXAMPLES_AS_OF)


@functools.cache
def example_inputs(posting_id: str) -> tuple[Posting, Reading, list[Requirement]]:
    """An example posting, its saved reading and its mapped requirements."""
    posting: Posting = json.loads((EXAMPLE_DATA / "postings" / f"{posting_id}.json").read_text(encoding="utf-8"))
    reading = store.load_reading(posting["text"], posting_id, EXAMPLE_DATA / "readings")
    mapping = store.load_mapping(posting["text"], EXAMPLE_DATA / "mappings")
    assert reading is not None and mapping is not None, posting_id
    return posting, reading, mapping["requirements"]


@functools.cache
def engine_page(posting_id: str) -> PageResult | str:
    """The engine's page for an example posting, or the message it was refused with."""
    posting, reading, mapping = example_inputs(posting_id)
    try:
        return build_page(posting, reading, example_library(), mapping)
    except Refused as refusal:
        return str(refusal)


def example_choice(posting_id: str) -> ProjectChoice:
    """Call 3's saved answer for an example posting (examples/data/choices)."""
    posting, _reading, _mapping = example_inputs(posting_id)
    choice = store.load_choice(posting["text"], EXAMPLE_DATA / "choices")
    assert choice is not None, posting_id
    return choice


@unittest.skipUnless(HAVE_LIBRARY, "the example library is not present")
class LibraryIntegrity(unittest.TestCase):
    library: Library
    expected: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.library = Library.load(EXAMPLE_LIBRARY, as_of=BASELINE_AS_OF)
        cls.expected = json.loads(PLACEMENTS.read_text(encoding="utf-8"))

    def test_library_loads_clean(self) -> None:
        self.assertEqual(self.library.warnings, self.expected["library"]["validation_warnings"])

    def test_every_written_bullet_has_a_capability_row(self) -> None:
        written = sorted(bullet.id for bullet in self.library.bullets if not bullet.pending)
        self.assertEqual(written, sorted(self.library.bullet_capabilities))

    def test_every_domain_has_a_family_and_ai_security_has_two(self) -> None:
        families = self.library.families
        self.assertEqual(set(families), set(self.library.domain_tags))
        self.assertEqual(sorted(families["AI security"]), ["AI and data", "Security"])
        # A domain's tag prefix names its (first) family, so tag distance agrees with the families table.
        prefix = {"Security": "sec", "AI and data": "ai", "Software": "sw", "Systems": "sys", "Professional": "pro"}
        for domain, tag in self.library.domain_tags.items():
            self.assertIn(tag.split(".")[0], {prefix[family] for family in families[domain]}, domain)

    def test_recency_is_measured_from_the_date_given(self) -> None:
        later = Library.load(EXAMPLE_LIBRARY, as_of=date(2027, 9, 21)).projects
        ended = next(project_id for project_id, project in self.library.projects.items() if not project.is_running)
        self.assertLess(recency(later[ended], DEFAULT_WEIGHTS), recency(self.library.projects[ended], DEFAULT_WEIGHTS))

    def test_text_outside_the_projects_is_unchanged(self) -> None:
        self.assertEqual(len(self.library.always_shown_text), self.expected["fixed_section_chars"])


@unittest.skipUnless(HAVE_LIBRARY, "the example library is not present")
class Refusal(unittest.TestCase):
    library: Library

    @classmethod
    def setUpClass(cls) -> None:
        cls.library = Library.load(EXAMPLE_LIBRARY, as_of=BASELINE_AS_OF)

    @staticmethod
    def reading(names_and_words: list[tuple[str, list[str]]], keywords: Sequence[str] = ()) -> Reading:
        return {
            "requirements": [{"name": name, "tokens": words, "required": True} for name, words in names_and_words],
            "keywords": list(keywords),
        }

    @staticmethod
    def mapped(name: str, capabilities: Sequence[str] = (), conditions: Sequence[str] = ()) -> Requirement:
        return {
            "name": name,
            "required": True,
            "capability": capabilities[0] if capabilities else None,
            "capabilities": list(capabilities),
            "conditions": list(conditions),
        }

    def test_a_posting_outside_the_library_is_refused_before_selection(self) -> None:
        with self.assertRaisesRegex(Refused, "outside the library"):
            build_page(
                pipeline.posting_from_text("", "Outside"),
                self.reading([("Quantum chronodynamics", ["quantum chronodynamics"])]),
                self.library,
                [self.mapped("Quantum chronodynamics", conditions=["quantum chronodynamics"])],
            )

    def test_refusal_reads_the_requirements_not_the_unseen_words(self) -> None:
        # A posting the library answers is built even when most of its words are ones the library never uses.
        reading = self.reading(
            [
                ("Python", ["Python"]),
                ("Rust", ["Rust"]),
                ("Zero Trust", ["zero trust architecture"]),
                ("Workload identity", ["mTLS workload identity"]),
            ],
            ["Teleport", "Distroless", "Envoy", "Istio", "Buildkite", "Salt", "Bazel", "Vault"],
        )
        mapping = [
            self.mapped("Python", conditions=["Python"]),
            self.mapped("Rust", ["formal verification"], ["Rust"]),
            self.mapped("Zero Trust", ["secure system architecture"]),
            self.mapped("Workload identity", ["secure system architecture"]),
        ]
        result = build_page(pipeline.posting_from_text("", "Reach"), reading, self.library, mapping)
        self.assertGreaterEqual(result["reach"], 0.25)
        self.assertGreater(result["unmet"], 0.6)


@unittest.skipUnless(HAVE_LIBRARY and EXAMPLE_DATA.exists(), "the example library or its saved answers are not present")
class CapabilityModeExamples(unittest.TestCase):
    def test_example_pages_are_unchanged(self) -> None:
        expected = json.loads(EXAMPLE_PAGES.read_text(encoding="utf-8"))
        for posting_id, recorded in expected.items():
            with self.subTest(posting=posting_id):
                result = engine_page(posting_id)
                if isinstance(result, str):
                    self.assertEqual({"refused": result}, recorded)
                    continue
                self.assertEqual(
                    {
                        "selected": result["selected"],
                        "page_order": [entry["group"] for entry in result["page"]],
                        "reach": result["reach"],
                        "coverage": result["coverage"],
                        "score": result["score"],
                    },
                    recorded,
                )

    def test_example_skill_rows_and_courses_are_unchanged(self) -> None:
        # The default path's document: which skill rows, under which titles, with which members, and which courses.
        # Each page also fits the template's line allotments, its skill rows with the fixed rows included.
        self.assertEqual(
            layout.HEADER_LINES + layout.EDUCATION_LINES + layout.SKILLS_LINES + layout.PROJECT_LINES, layout.PAGE_LINES
        )
        fixed = fixed_rows(example_library().skill_rows)
        expected = json.loads(EXAMPLE_DOCUMENTS.read_text(encoding="utf-8"))
        for posting_id, recorded in expected.items():
            with self.subTest(posting=posting_id):
                page = engine_page(posting_id)
                if isinstance(page, str):
                    self.fail(f"refused: {page}")
                document = document_from_page(page)
                self.assertEqual(
                    {
                        "skills": [[row.title, list(row.members)] for row in document.skill_rows],
                        "coursework": [[line.degree_id, list(line.courses)] for line in document.coursework],
                    },
                    recorded,
                )
                self.assertLessEqual(layout.skills_lines([*document.skill_rows, *fixed]), layout.SKILLS_LINES)
                self.assertLessEqual(page["project_lines"], layout.PROJECT_BODY_LINES)


@unittest.skipUnless(HAVE_LIBRARY and EXAMPLE_DATA.exists(), "the example library or its saved answers are not present")
class HybridMode(unittest.TestCase):
    """Hybrid mode on the examples with call 3's saved answers (examples/data/choices); no model call."""

    def test_hybrid_pages_are_unchanged_and_take_the_models_projects_only_under_the_threshold(self) -> None:
        expected = json.loads(EXAMPLE_HYBRID_PAGES.read_text(encoding="utf-8"))
        library = example_library()
        listing_hash = projects_hash(library)
        for posting_id, recorded in expected.items():
            with self.subTest(posting=posting_id):
                posting, reading, mapping = example_inputs(posting_id)
                choice = example_choice(posting_id)
                self.assertTrue(store.choice_is_current(choice, listing_hash))
                try:
                    page = build_hybrid_page(posting, reading, library, mapping, choice)
                except Refused as refusal:
                    self.assertEqual({"refused": str(refusal)}, recorded)
                    continue
                self.assertEqual(
                    {
                        "projects_by": page["projects_by"],
                        "selected": page["selected"],
                        "page_order": [entry["group"] for entry in page["page"]],
                        "coverage": page["coverage"],
                        "engine_match": page.get("engine_match"),
                    },
                    recorded,
                )
                engine = engine_page(posting_id)
                if isinstance(engine, str):
                    self.fail(f"the engine's page was refused where the hybrid page was not: {engine}")
                # The selector may leave a chosen project out; the choice records the ones the page holds.
                on_page = {entry["group"] for entry in page["page"]}
                placed = [project_id for project_id in choice["projects"] if project_id in on_page]
                self.assertEqual(
                    page["choice"], {"projects": choice["projects"], "why": choice["why"], "placed": placed}
                )
                if engine["coverage"] >= DEFAULT_WEIGHTS.model_projects_below:
                    self.assertEqual(page["projects_by"], "engine")
                    self.assertEqual(page["selected"], engine["selected"])
                else:
                    self.assertEqual(page["projects_by"], "model")
                    self.assertEqual(page["engine_match"], engine["coverage"])
                    self.assertLessEqual(on_page, set(choice["projects"]))
                    # Only the projects change: reach and the skill rows stay the whole library's.
                    self.assertEqual((page["reach"], page["skill_rows"]), (engine["reach"], engine["skill_rows"]))

    def test_without_a_usable_choice_the_engines_page_stands(self) -> None:
        posting_id = next(
            posting_id
            for posting_id, recorded in json.loads(EXAMPLE_HYBRID_PAGES.read_text(encoding="utf-8")).items()
            if recorded.get("projects_by") == "model"
        )
        library = example_library()
        posting, reading, mapping = example_inputs(posting_id)
        engine = engine_page(posting_id)
        if isinstance(engine, str):
            self.fail(f"refused: {engine}")
        no_choice = build_hybrid_page(posting, reading, library, mapping, None)
        # Projects with no usable bullet leave nothing to place: the engine's page stays, with the choice recorded.
        nothing_to_place = build_hybrid_page(
            posting, reading, library, mapping, {**example_choice(posting_id), "projects": ["no-such-project"]}
        )
        for page in (no_choice, nothing_to_place):
            self.assertEqual((page["projects_by"], page["selected"]), ("engine", engine["selected"]))
        self.assertNotIn("choice", no_choice)
        self.assertEqual(
            (nothing_to_place["choice"]["projects"], nothing_to_place["choice"]["placed"]), (["no-such-project"], [])
        )


if __name__ == "__main__":
    unittest.main()
