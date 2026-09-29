"""Writing into the Word template.

End-to-end writes, the package-preservation contract, structure, and refusals. Pages are capability-mode pages
(the default) built from the example postings' saved readings and mappings, so no model is called.

These read the example library and its template (examples/library), never library/.
"""

from __future__ import annotations

import copy
import datetime as dt
import functools
import hashlib
import json
import tempfile
import unittest
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

from lxml import etree

from tailor_engine.library import Library
from tailor_engine.reading import store
from tailor_engine.records import Posting
from tailor_engine.rendering import word
from tailor_engine.rendering.document import ResumeDocument, document_from_page
from tailor_engine.selection.page import build_page

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_LIBRARY = EXAMPLES / "library"
EXAMPLE_DATA = EXAMPLES / "data"
WORD_PREFIX = f"{{{word.WORD_NAMESPACE}}}"  # element names as lxml spells them
TEMPLATE = EXAMPLE_LIBRARY / "resume-template.docx"
# Four example postings that get a page: application security (X1), offensive security (X2), AI security
# evaluation (X3) and cloud security (X5).
IN_LIBRARY = ("paste-89f5dd84", "paste-87b68035", "paste-3d814d16", "paste-afdfe55e")
# Template paragraphs outside every slot (section headings and fixed lines): a write leaves them as they are.
STRUCTURAL_PARAGRAPH_IDS = ("5E185C65", "7FC26840", "55F8519C", "6AC068EC", "67D4B4DA")
# The date the example locks use (EXAMPLES_AS_OF in test_selection_regression.py).
LIBRARY = (
    Library.load(EXAMPLE_LIBRARY, as_of=dt.date(2026, 9, 28)) if (EXAMPLE_LIBRARY / "projects.toml").exists() else None
)


# A document is frozen, so each posting's is built once and shared by every test that writes or reads it.
@functools.cache
def _document_for(posting_id: str) -> ResumeDocument:
    posting: Posting = json.loads((EXAMPLE_DATA / "postings" / f"{posting_id}.json").read_text(encoding="utf-8"))
    reading = store.load_reading(posting["text"], posting_id, EXAMPLE_DATA / "readings")
    mapping = store.load_mapping(posting["text"], EXAMPLE_DATA / "mappings")
    assert reading is not None and mapping is not None and LIBRARY is not None, posting_id
    return document_from_page(build_page(posting, reading, LIBRARY, mapping["requirements"]))


def _paragraphs(path: Path) -> list[etree._Element]:
    with zipfile.ZipFile(path) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    paragraphs: list[etree._Element] = root.find(f"{WORD_PREFIX}body").findall(f"{WORD_PREFIX}p")
    return paragraphs


def _text(paragraph: etree._Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{WORD_PREFIX}t"))


def _numbering_id(paragraph: etree._Element) -> str | None:
    node = paragraph.find(f"{WORD_PREFIX}pPr/{WORD_PREFIX}numPr/{WORD_PREFIX}numId")
    return node.get(f"{WORD_PREFIX}val") if node is not None else None


@unittest.skipUnless(
    TEMPLATE.exists() and LIBRARY is not None and EXAMPLE_DATA.exists(),
    "the example library, its template or its saved answers are not present",
)
class WordTemplateWriteTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    output_folder: Path
    written: dict[str, Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output_folder = Path(cls.temporary.name)
        cls.written = {}
        for posting_id in IN_LIBRARY:
            destination = cls.output_folder / f"{posting_id}.docx"
            word.write_tailored_docx(TEMPLATE, destination, _document_for(posting_id))
            cls.written[posting_id] = destination

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_template_hash_pin_matches_the_snapshot(self) -> None:
        digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
        self.assertEqual(digest, word.TEMPLATE_SHA256)

    def test_only_the_document_part_differs_from_the_template(self) -> None:
        for posting_id, path in self.written.items():
            with self.subTest(posting_id):
                with zipfile.ZipFile(TEMPLATE) as source, zipfile.ZipFile(path) as result:
                    self.assertEqual(source.namelist(), result.namelist())
                    differing = [name for name in source.namelist() if source.read(name) != result.read(name)]
                self.assertEqual(differing, ["word/document.xml"])

    def test_projects_render_as_a_title_plus_one_paragraph_per_bullet(self) -> None:
        for posting_id, path in self.written.items():
            with self.subTest(posting_id):
                document = _document_for(posting_id)
                expected_bullets = sum(len(project.bullets) for project in document.projects)
                paragraphs = _paragraphs(path)
                bullets = [p for p in paragraphs if _numbering_id(p) == "3"]
                self.assertEqual(len(bullets), expected_bullets)
                titles = [_text(p) for p in paragraphs if word._paragraph_id(p) in word.PROJECT_SLOTS]
                self.assertEqual(titles, [project.title for project in document.projects])
                for title in titles:
                    self.assertFalse(title.endswith(":"), "project title should not carry a colon")

    def test_selected_text_reaches_the_document(self) -> None:
        for posting_id, path in self.written.items():
            with self.subTest(posting_id):
                body = "\n".join(_text(p) for p in _paragraphs(path))
                for project in _document_for(posting_id).projects:
                    for bullet in project.bullets:
                        self.assertIn(bullet.strip(), body)

    def test_paragraph_ids_are_unique_and_in_the_range_word_allows(self) -> None:
        # [MS-DOCX] paraId: unique within the document part, above 0 and below 0x80000000.
        for posting_id, path in self.written.items():
            with self.subTest(posting_id):
                ids = [word._paragraph_id(p) or "" for p in _paragraphs(path)]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual([value for value in ids if not 0 < int(value, 16) < 0x80000000], [])

    def test_structural_paragraphs_are_untouched(self) -> None:
        template = {word._paragraph_id(p): _text(p) for p in _paragraphs(TEMPLATE)}
        for posting_id, path in self.written.items():
            with self.subTest(posting_id):
                produced = {word._paragraph_id(p): _text(p) for p in _paragraphs(path)}
                for paragraph_id in STRUCTURAL_PARAGRAPH_IDS:
                    self.assertEqual(produced[paragraph_id], template[paragraph_id])

    def test_writing_over_the_template_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overwrite the template"):
            word.write_tailored_docx(TEMPLATE, TEMPLATE, _document_for(IN_LIBRARY[0]))

    def assert_write_refused(self, replaced: str, damage: Callable[..., None], message: str) -> None:
        """A write whose `replaced` step also does `damage` is refused with `message`, and leaves no file."""
        original = getattr(word, replaced)

        def damaging(paragraph: etree._Element, *arguments: Any) -> Any:
            result = original(paragraph, *arguments)
            damage(paragraph)
            return result

        folder = self.output_folder / f"refused-{replaced}-{damage.__name__}"
        folder.mkdir()
        with (
            mock.patch.object(word, replaced, damaging),
            self.assertRaisesRegex(word.TemplateMismatchError, message),
        ):
            word.write_tailored_docx(TEMPLATE, folder / "out.docx", _document_for(IN_LIBRARY[0]))
        self.assertEqual(list(folder.iterdir()), [])

    def test_a_write_that_changes_anything_outside_the_slots_is_refused(self) -> None:
        def edit_the_first_paragraph(paragraph: etree._Element) -> None:
            first = paragraph.getparent()[0]
            first.append(word._new_run(" (edited)", None))

        self.assert_write_refused("_replace_skill", edit_the_first_paragraph, "outside the slots changed")

    def test_a_write_that_moves_a_slot_is_refused(self) -> None:
        def move_before_previous(paragraph: etree._Element) -> None:
            if word._paragraph_id(paragraph) == word.SKILL_SLOTS[-1]:
                paragraph.getprevious().addprevious(paragraph)

        self.assert_write_refused("_replace_skill", move_before_previous, "out of order")

    def test_a_write_that_repeats_a_bullet_is_refused(self) -> None:
        def repeat_the_first_bullet(paragraph: etree._Element) -> None:
            first_bullet = paragraph.getnext()
            first_bullet.addnext(copy.deepcopy(first_bullet))

        self.assert_write_refused("_replace_project", repeat_the_first_bullet, "repeated or out of order")

    def test_existing_output_is_refused_without_overwrite(self) -> None:
        with self.assertRaises(FileExistsError):
            word.write_tailored_docx(TEMPLATE, self.written[IN_LIBRARY[0]], _document_for(IN_LIBRARY[0]))

    def test_a_changed_template_is_refused(self) -> None:
        with self.assertRaises(word.TemplateMismatchError):
            word.write_tailored_docx(
                TEMPLATE,
                self.output_folder / "mismatch.docx",
                _document_for(IN_LIBRARY[0]),
                expected_template_sha256="0" * 64,
            )

    def test_a_pin_file_beside_a_template_of_ones_own_replaces_the_example_s_pin(self) -> None:
        # The example template's parts in a package of other bytes: the same content, another SHA-256.
        own = self.output_folder / "own" / "resume-template.docx"
        own.parent.mkdir()
        with zipfile.ZipFile(TEMPLATE) as source, zipfile.ZipFile(own, "w", zipfile.ZIP_STORED) as copied:
            for name in source.namelist():
                copied.writestr(name, source.read(name))
        document = _document_for(IN_LIBRARY[0])
        with self.assertRaisesRegex(word.TemplateMismatchError, "resume-template.sha256 beside it"):
            word.write_tailored_docx(own, own.parent / "refused.docx", document)
        pin = own.with_name(word.PIN_FILE)
        pin.write_text(f"{hashlib.sha256(own.read_bytes()).hexdigest()}  resume-template.docx\n", encoding="utf-8")
        written = word.write_tailored_docx(own, own.parent / "written.docx", document)
        self.assertEqual(word.pinned_sha256(TEMPLATE), word.TEMPLATE_SHA256)
        self.assertEqual(len(_paragraphs(written)), len(_paragraphs(self.written[IN_LIBRARY[0]])))

    def test_output_is_byte_identical_across_runs(self) -> None:
        first = self.written[IN_LIBRARY[0]].read_bytes()
        again = self.output_folder / "again.docx"
        word.write_tailored_docx(TEMPLATE, again, _document_for(IN_LIBRARY[0]))
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(again.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
