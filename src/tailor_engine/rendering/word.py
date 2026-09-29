"""Writing the page into the Word template, failing closed on anything unexpected.

The template is a finished one-page resume. Only its marked slots change, located by Word's paragraph ids
and checked against their expected positions:

  coursework slots   one per degree, in the order the library lists degrees ("Coursework: a, b, c")
  project slots      six; each used slot becomes a title line followed by one bullet paragraph per
                     bullet, and unused slots are removed
  skill slots        five rows, "Title: a, b, c"

Every other part of the package is copied byte for byte, and the result is re-read and compared with the
template before it replaces the output. The document with the slots and generated bullets taken out must
equal the template with the slots taken out, as a whole (order, section properties, attributes), and the
slots and bullets must stand in the template's slot order, each once. Anything else, or the template
differing from its pinned SHA-256, stops the write. The pin is `TEMPLATE_SHA256`, the example template's, unless
a file `resume-template.sha256` beside the template holds another: a library's own template is pinned there.
"""

from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from lxml import etree

from .document import ResumeDocument, validate_document

# ─────────────────────────────────────────────────────────────
# Template
# ─────────────────────────────────────────────────────────────


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_2010_NAMESPACE = "http://schemas.microsoft.com/office/word/2010/wordml"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
NAMESPACES = {"w": WORD_NAMESPACE, "w14": WORD_2010_NAMESPACE}
DOCUMENT_PART = "word/document.xml"
# The example template's SHA-256 (examples/library/resume-template.docx).
TEMPLATE_SHA256 = "2781359a494a85dc9d5111564751e136efe87d16abca4a41a172e3784fcde07f"
# Beside a template of the user's own: its SHA-256, the first word of the file, in place of TEMPLATE_SHA256.
PIN_FILE = "resume-template.sha256"

COURSEWORK_SLOTS = ("49B2B4F3", "3D2B92AF")
PROJECT_SLOTS = ("15D10DA1", "0FF90F43", "70A67275", "72860424", "032DF9B4", "36EEEBC9")
SKILL_SLOTS = ("25A22645", "50293AE9", "3E24A5EB", "10DC825C", "74DC496D")
MUTABLE_SLOTS = frozenset((*COURSEWORK_SLOTS, *PROJECT_SLOTS, *SKILL_SLOTS))
# The template's list numbering for coursework lines and for project bullets.
COURSEWORK_NUMBERING_ID = "2"
PROJECT_NUMBERING_ID = "3"
# The text id Word gave most of the template's own paragraphs (11 of 28), used for generated bullets too.
GENERATED_TEXT_ID = "77777777"
# A project or skill slot holds a title run and a body run; the written text copies their formatting.
PROTOTYPE_RUNS = 2
# Each slot's position among the body's paragraphs in the pinned template.
EXPECTED_INDICES = {
    "49B2B4F3": 6,
    "3D2B92AF": 10,
    "15D10DA1": 13,
    "0FF90F43": 14,
    "70A67275": 15,
    "72860424": 16,
    "032DF9B4": 17,
    "36EEEBC9": 18,
    "25A22645": 21,
    "50293AE9": 22,
    "3E24A5EB": 23,
    "10DC825C": 24,
    "74DC496D": 25,
}


class TemplateMismatchError(ValueError):
    """Raised when the input no longer matches the pinned template."""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qualified(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _paragraph_id(paragraph: etree._Element) -> str | None:
    paragraph_id: str | None = paragraph.get(_qualified(WORD_2010_NAMESPACE, "paraId"))
    return paragraph_id


def _canonical_bytes(element: etree._Element) -> bytes:
    canonical: bytes = etree.tostring(element, method="c14n", with_comments=True)
    return canonical


def _xml_parser() -> etree.XMLParser:
    # The package is read as data: entities are never resolved.
    return etree.XMLParser(remove_blank_text=False, resolve_entities=False)


def _body_paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find("w:body", NAMESPACES)
    if body is None:
        raise TemplateMismatchError("word/document.xml has no body")
    return list(body.findall("w:p", NAMESPACES))


def _locate_slots(root: etree._Element) -> dict[str, etree._Element]:
    """Each slot paragraph by id, checked against the pinned template.

    A slot missing, repeated or moved from its position raises TemplateMismatchError, so text never lands in the wrong
    place.
    """
    paragraphs = _body_paragraphs(root)
    located: dict[str, etree._Element] = {}
    for index, paragraph in enumerate(paragraphs):
        paragraph_id = _paragraph_id(paragraph)
        if paragraph_id not in MUTABLE_SLOTS:
            continue
        if paragraph_id in located:
            raise TemplateMismatchError(f"duplicate paragraph ID {paragraph_id}")
        expected = EXPECTED_INDICES[paragraph_id]
        if index != expected:
            raise TemplateMismatchError(f"paragraph {paragraph_id} moved from index {expected} to {index}")
        located[paragraph_id] = paragraph
    missing = MUTABLE_SLOTS - set(located)
    if missing:
        raise TemplateMismatchError("template is missing mutable paragraph IDs: " + ", ".join(sorted(missing)))
    return located


def _require_numbering(paragraph: etree._Element, expected_num_id: str) -> None:
    values = paragraph.xpath("w:pPr/w:numPr/w:numId/@w:val", namespaces=NAMESPACES)
    if values != [expected_num_id]:
        raise TemplateMismatchError(f"paragraph {_paragraph_id(paragraph)} must use numbering ID {expected_num_id}")


# ─────────────────────────────────────────────────────────────
# Runs
# ─────────────────────────────────────────────────────────────


def _run_properties(run: etree._Element) -> etree._Element | None:
    properties = run.find("w:rPr", NAMESPACES)
    return copy.deepcopy(properties) if properties is not None else None


def _new_run(text: str, properties: etree._Element | None) -> etree._Element:
    """A text run with these properties. Leading or trailing spaces are marked to be kept; Word drops them otherwise."""
    run = etree.Element(_qualified(WORD_NAMESPACE, "r"))
    if properties is not None:
        run.append(copy.deepcopy(properties))
    text_node = etree.SubElement(run, _qualified(WORD_NAMESPACE, "t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set(_qualified(XML_NAMESPACE, "space"), "preserve")
    text_node.text = text
    return run


def _replace_runs(
    paragraph: etree._Element,
    pieces: Iterable[tuple[str, etree._Element | None]],
) -> None:
    """Replace a paragraph's text runs, keeping its paragraph properties.

    A paragraph holding anything but runs raises, since rewriting it would drop that content.
    """
    children = list(paragraph)
    if any(etree.QName(child).localname not in {"pPr", "r"} for child in children):
        raise TemplateMismatchError(f"paragraph {_paragraph_id(paragraph)} contains unsupported non-run content")
    for child in children:
        if etree.QName(child).localname == "r":
            paragraph.remove(child)
    for text, properties in pieces:
        paragraph.append(_new_run(text, properties))


# ─────────────────────────────────────────────────────────────
# Slots
# ─────────────────────────────────────────────────────────────


def _replace_coursework(paragraph: etree._Element, courses: Sequence[str]) -> None:
    """Write one degree's courses into its coursework slot, in the slot's own formatting."""
    _require_numbering(paragraph, COURSEWORK_NUMBERING_ID)
    runs = paragraph.findall("w:r", NAMESPACES)
    if not runs:
        raise TemplateMismatchError("coursework paragraph has no formatting prototype")
    _replace_runs(
        paragraph,
        [("Coursework: " + ", ".join(courses), _run_properties(runs[0]))],
    )


def _derived_paragraph_id(slot_id: str, index: int) -> str:
    """Deterministic 8-hex paragraph id for a generated bullet, so two runs produce the same bytes.

    Word's format requires it above 0 and below 0x80000000 ([MS-DOCX] paraId), so the hash's top bit is cleared.
    """
    value = int(hashlib.sha256(f"{slot_id}:{index}".encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
    return f"{value or 1:08X}"


def _replace_project(paragraph: etree._Element, slot_id: str, title: str, bullets: Sequence[str]) -> tuple[str, ...]:
    """Turn one project slot into a title paragraph plus one paragraph per bullet.

    The slot keeps its own paragraph id and becomes the title; the bullets are inserted after it. Returns the bullets'
    paragraph ids.
    """
    _require_numbering(paragraph, PROJECT_NUMBERING_ID)
    runs = paragraph.findall("w:r", NAMESPACES)
    if len(runs) < PROTOTYPE_RUNS:
        raise TemplateMismatchError("project paragraph has no title/body prototypes")
    if not bullets:
        raise TemplateMismatchError("project slot has no bullets")
    title_properties = _run_properties(runs[0])
    body_properties = _run_properties(runs[-1])
    bullet_prototype = copy.deepcopy(paragraph)

    # the slot becomes the title: bold, and no longer a list item
    properties = paragraph.find("w:pPr", NAMESPACES)
    numbering = properties.find("w:numPr", NAMESPACES) if properties is not None else None
    if numbering is not None:
        properties.remove(numbering)
    indent = properties.find("w:ind", NAMESPACES) if properties is not None else None
    if indent is not None:
        properties.remove(indent)
    _replace_runs(paragraph, [(title.strip().removesuffix(":"), title_properties)])

    parent = paragraph.getparent()
    position = list(parent).index(paragraph)
    bullet_ids = tuple(_derived_paragraph_id(slot_id, offset) for offset in range(len(bullets)))
    for offset, (bullet, bullet_id) in enumerate(zip(bullets, bullet_ids, strict=True)):
        item = copy.deepcopy(bullet_prototype)
        item.set(_qualified(WORD_2010_NAMESPACE, "paraId"), bullet_id)
        item.set(_qualified(WORD_2010_NAMESPACE, "textId"), GENERATED_TEXT_ID)
        _replace_runs(item, [(bullet.strip(), body_properties)])
        parent.insert(position + 1 + offset, item)
    return bullet_ids


def _replace_skill(paragraph: etree._Element, title: str, members: Sequence[str]) -> None:
    """Write one skill row into its slot: the title in the title run's formatting, the skills in the body's."""
    runs = paragraph.findall("w:r", NAMESPACES)
    if len(runs) < PROTOTYPE_RUNS:
        raise TemplateMismatchError("skill paragraph has no title/body prototypes")
    clean_title = title.strip().removesuffix(":") + ":"
    _replace_runs(
        paragraph,
        [
            (clean_title, _run_properties(runs[0])),
            (" " + ", ".join(members), _run_properties(runs[-1])),
        ],
    )


# ─────────────────────────────────────────────────────────────
# Checking the written file
# ─────────────────────────────────────────────────────────────


def _read_output(template_parts: dict[str, bytes], output: Path) -> etree._Element:
    """The written document part, once every other part of the package is found unchanged.

    Every other part must be the template's, byte for byte and in the same order.
    """
    with zipfile.ZipFile(output) as package:
        if package.namelist() != list(template_parts):
            raise TemplateMismatchError("output package part order or membership changed")
        for name, source_data in template_parts.items():
            if name != DOCUMENT_PART and package.read(name) != source_data:
                raise TemplateMismatchError(f"preserved package part changed: {name}")
        root: etree._Element = etree.fromstring(package.read(DOCUMENT_PART), _xml_parser())
    return root


def _without_paragraphs(root: etree._Element, paragraph_ids: Iterable[str | None]) -> etree._Element:
    """A copy of the document with these body paragraphs taken out."""
    removed = set(paragraph_ids)
    trimmed: etree._Element = copy.deepcopy(root)
    body = trimmed.find("w:body", NAMESPACES)
    for paragraph in _body_paragraphs(trimmed):
        if _paragraph_id(paragraph) in removed:
            body.remove(paragraph)
    return trimmed


def _check_body(
    before_root: etree._Element,
    after_root: etree._Element,
    generated: dict[str, tuple[str, ...]],
    removed_slots: frozenset[str],
) -> None:
    """Refuses a written document that is anything but the template with its slots changed.

    `generated` is each used project slot's bullet ids; `removed_slots` the unused project slots.
    """
    new_ids = {bullet_id for bullet_ids in generated.values() for bullet_id in bullet_ids}
    outside_before = _without_paragraphs(before_root, MUTABLE_SLOTS)
    outside_after = _without_paragraphs(after_root, MUTABLE_SLOTS | new_ids)
    if _canonical_bytes(outside_before) != _canonical_bytes(outside_after):
        raise TemplateMismatchError("the document outside the slots changed")
    slot_order = [_paragraph_id(paragraph) for paragraph in _body_paragraphs(before_root)]
    expected = [
        paragraph_id
        for slot_id in slot_order
        if slot_id in MUTABLE_SLOTS and slot_id not in removed_slots
        for paragraph_id in (slot_id, *generated.get(slot_id, ()))
    ]
    found = [
        paragraph_id
        for paragraph_id in (_paragraph_id(paragraph) for paragraph in _body_paragraphs(after_root))
        if paragraph_id in MUTABLE_SLOTS or paragraph_id in new_ids
    ]
    if found != expected:
        raise TemplateMismatchError("slots or generated bullets are missing, repeated or out of order")


# ─────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────


def _fill_slots(root: etree._Element, document: ResumeDocument) -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    """Write the document into the slots of `root`, in place.

    Returns each used project slot's generated bullet ids, and the unused project slots, which are removed rather than
    left as empty bullets.
    """
    slots = _locate_slots(root)
    if len(document.coursework) != len(COURSEWORK_SLOTS):
        raise TemplateMismatchError(
            f"the template has {len(COURSEWORK_SLOTS)} coursework lines; "
            f"the library has {len(document.coursework)} degrees"
        )
    for paragraph_id, entry in zip(COURSEWORK_SLOTS, document.coursework, strict=True):
        _replace_coursework(slots[paragraph_id], entry.courses)
    # A page may use fewer project slots than the template has.
    generated = {
        paragraph_id: _replace_project(slots[paragraph_id], paragraph_id, project.title, project.bullets)
        for paragraph_id, project in zip(PROJECT_SLOTS, document.projects, strict=False)
    }
    unused_slots = PROJECT_SLOTS[len(document.projects) :]
    for paragraph_id in unused_slots:
        unused = slots[paragraph_id]
        unused.getparent().remove(unused)
    for paragraph_id, row in zip(SKILL_SLOTS, document.skill_rows, strict=True):
        _replace_skill(slots[paragraph_id], row.title, row.members)
    return generated, frozenset(unused_slots)


def pinned_sha256(template: Path) -> str:
    """The SHA-256 the template must have: the one in the pin file beside it, else the example template's."""
    pin = template.with_name(PIN_FILE)
    if not pin.exists():
        return TEMPLATE_SHA256
    words = pin.read_text(encoding="utf-8").split()
    return words[0].lower() if words else ""


def write_tailored_docx(
    template: Path,
    output: Path,
    document: ResumeDocument,
    *,
    overwrite: bool = False,
    expected_template_sha256: str | None = None,
) -> Path:
    """Write a new resume while preserving the pinned template's package contract. Returns the path written.

    The template must have `expected_template_sha256`, by default its pin (`pinned_sha256`).
    """
    validate_document(document)
    template = template.resolve()
    expected_template_sha256 = expected_template_sha256 or pinned_sha256(template)
    output = output.resolve()
    if template == output:
        raise ValueError("output must not overwrite the template")
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")

    template_hash = _file_sha256(template)
    if template_hash != expected_template_sha256:
        raise TemplateMismatchError(
            "template hash differs from the pinned template; "
            f"expected {expected_template_sha256}, found {template_hash}. A template of your own is pinned by its "
            f"SHA-256 in {PIN_FILE} beside it"
        )
    with zipfile.ZipFile(template) as source:
        template_entries = source.infolist()
        template_parts = {info.filename: source.read(info.filename) for info in template_entries}
    if DOCUMENT_PART not in template_parts:
        raise TemplateMismatchError(f"template is missing {DOCUMENT_PART}")

    before_root = etree.fromstring(template_parts[DOCUMENT_PART], _xml_parser())
    edited_root = copy.deepcopy(before_root)
    generated, removed_slots = _fill_slots(edited_root, document)
    document_part = etree.tostring(edited_root, encoding="UTF-8", xml_declaration=True, standalone=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".docx.tmp", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as destination:
            for info in template_entries:
                destination.writestr(
                    info, document_part if info.filename == DOCUMENT_PART else template_parts[info.filename]
                )
        if _file_sha256(template) != template_hash:
            raise TemplateMismatchError("template changed while output was being written")
        after_root = _read_output(template_parts, temporary)
        _check_body(before_root, after_root, generated, removed_slots)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output
