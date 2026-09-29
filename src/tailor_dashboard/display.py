"""A job's names, facts, deal-breakers, printed page and the models that answered, as the dashboard shows them.

Functions of saved records (a posting, its reading, its Word file), with no state.
"""

from __future__ import annotations

import functools
import json
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lxml import etree

from tailor_engine.reading import store
from tailor_engine.reading.facts import posting_facts
from tailor_engine.records import Posting, Reading, Requirement
from tailor_engine.rendering.word import DOCUMENT_PART, NAMESPACES

CURRENCY_SYMBOLS = {"USD": "$", "CAD": "CA$", "GBP": "£", "EUR": "€", "AUD": "A$", "INR": "₹", "SGD": "S$"}
WORK_MODES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}
# A title from a pasted posting's first line is kept only when it is short enough to be one.
TITLE_CHARACTERS = 100
WEB_ADDRESS = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
HOST_PREFIXES = frozenset(("www", "careers", "jobs", "job", "boards", "apply"))
# " — Acme Careers", " | Jobs at Acme": a site's name after a page's title, recognised by "careers" or "jobs".
SITE_SUFFIX = re.compile(r"\s+[—–|]\s+[^—–|]*\b(careers|jobs)\b[^—–|]*$", re.IGNORECASE)


def pay_text(salary: dict[str, Any] | None) -> str | None:
    """Pay as the list shows it ("$300k–405k", "CA$250k–535k", "$62–78 / hr"); None when the posting states none."""
    if not salary or not salary.get("min"):
        return None
    currency = salary.get("currency") or "USD"
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    low, high = int(salary["min"]), int(salary.get("max") or salary["min"])
    if salary.get("period") == "hour":
        return f"{symbol}{low}–{high} / hr" if high != low else f"{symbol}{low} / hr"
    thousands = f"{symbol}{round(low / 1000)}k"
    return f"{thousands}–{round(high / 1000)}k" if high != low else thousands


def places(location: str | None) -> tuple[str, str]:
    """(the first place and how many more, every place), from a board's list or the posting's "Location:" line."""
    if not location:
        return "Not stated", "Not stated"
    parts = [part.strip() for part in re.split(r"\s*[|;•]\s*", location) if part.strip()]
    if not parts:
        return "Not stated", "Not stated"
    return parts[0] + (f" +{len(parts) - 1}" if len(parts) > 1 else ""), "; ".join(parts)


def company_name(company: str) -> str:
    """The company as the dashboard names it.

    A board's slug ("acme-corp") becomes "Acme Corp"; a web address, which the page reader records for a site it has no
    board reader for ("www.example.com"), becomes its name ("Example"); a name already written with capitals stays.
    """
    if not company:
        return "Pasted posting"
    if WEB_ADDRESS.fullmatch(company):
        labels = [label for label in company.lower().split(".")[:-1] if label not in HOST_PREFIXES]
        company = labels[0] if labels else company
    return company.replace("-", " ").title() if company == company.lower() else company


def posting_title(posting: Posting) -> str:
    """The posting's own title: the board's, a web page's without its site name, or a pasted posting's first line."""
    if posting.get("title"):
        # A page read without a board reader keeps its browser title, the site's name after it ("... — Acme Careers").
        if posting.get("source") == "html":
            return SITE_SUFFIX.sub("", posting["title"]).strip() or posting["title"]
        return posting["title"]
    first = next((line.strip() for line in posting["text"].splitlines() if line.strip()), "")
    return first if 0 < len(first) <= TITLE_CHARACTERS else "Untitled posting"


def shown_names(posting: Posting, row: dict[str, Any]) -> tuple[str, str, bool]:
    """(title, company, whether the company is known): the names the user gave the job, else the posting's own."""
    title = row.get("title") or posting_title(posting)
    company = row.get("company") or company_name(posting.get("company") or "")
    return title, company, bool(row.get("company") or posting.get("company"))


STATES = ("met", "partial", "missing")


def state_of(credit: float) -> str:
    return "met" if credit >= 1.0 else "partial" if credit > 0 else "missing"


# A model id as the command line gives it: "claude-opus-5-5", "claude-sonnet-4-6", "claude-opus-5".
CLAUDE_MODEL = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?")


def model_name(model: str | None) -> str | None:
    """A model's name as the page shows it ("Jev", "Opus 5.5", "Sonnet 4.6"); an id it does not know, as it is."""
    if not model:
        return None
    if model.startswith("jev"):
        return "Jev"
    found = CLAUDE_MODEL.fullmatch(model)
    if found is None:
        return model
    family, major, minor = found.groups()
    return f"{family.capitalize()} {major}" + (f".{minor}" if minor else "")


def answered(record: Mapping[str, Any] | None) -> dict[str, str | None] | None:
    """Which model answered a saved call 2 or 3 ("by"), and why another answered in place of the one asked ("fallback").

    None for no record. "fallback" is None when the model asked answered.
    """
    if not record:
        return None
    notice = record.get("fallback")
    note = None
    if isinstance(notice, dict):
        # Jev's reasons lead the first errors (`jev.fell_back`); the command line's own fallback gives a category.
        errors = [str(error) for error in record.get("first_errors") or []]
        reasons = [error.removeprefix("Jev: ") for error in errors if error.startswith("Jev: ")]
        category = notice.get("api_refusal_category")
        why = "; ".join(reasons) or (f"refused ({category})" if category else "")
        note = f"{model_name(notice.get('original_model')) or 'the model asked'} could not answer" + (
            f": {why}" if why else ""
        )
    return {"by": model_name(record.get("answered_by") or record.get("model")), "fallback": note}


# Requirements that screen candidates out (citizenship, a clearance, no visa sponsorship, years asked), shown at the
# top of each job, since they sit among ordinary requirement lines.
CITIZENSHIP_WORDS = re.compile(r"\bcitizen", re.I)
CLEARANCE_WORDS = re.compile(r"\bclearance\b|\bTS/SCI\b|\btop secret\b", re.I)
YEARS_FLAGGED = 3  # years asked from which a posting is flagged; set by hand


def blockers_of(facts: dict[str, Any], reading: Reading | None) -> list[str]:
    """The deal-breakers a posting states, in order: citizenship, clearance, sponsorship refused, years asked."""
    requirements = reading.get("requirements", []) if reading else []

    def asking(words: re.Pattern[str]) -> list[Requirement]:
        return [item for item in requirements if words.search(" ".join([item["name"], *item.get("tokens", [])]))]

    found = []
    citizenship = asking(CITIZENSHIP_WORDS)
    if citizenship:
        found.append(
            "U.S. citizenship " + ("required" if any(item["required"] for item in citizenship) else "preferred")
        )
    clearance = asking(CLEARANCE_WORDS)
    if facts.get("clearance_required") or any(item["required"] for item in clearance):
        found.append("Security clearance required")
    elif clearance:
        found.append("Security clearance preferred")
    if facts.get("sponsorship") == "no":
        found.append("No visa sponsorship")
    years = facts.get("years_required")
    if years is not None and years >= YEARS_FLAGGED:
        found.append(f"Asks {years}+ years")
    return found


def facts_of(posting: Posting) -> dict[str, Any]:
    """`posting_facts`, kept for each posting's content.

    A saved posting does not change, and reading its facts again (the text's patterns) was half the time of every
    list request.
    """
    return dict(_facts(json.dumps(posting, sort_keys=True)))


@functools.lru_cache(maxsize=1024)
def _facts(posting: str) -> dict[str, Any]:
    return posting_facts(json.loads(posting))


def printed_paragraphs(path: Path) -> list[dict[str, Any]]:
    """The Word file's paragraphs in order, empty ones left out.

    Each is {"runs": [[text, bold]], "bullet", "heading"}; a tab is a run holding one tab character (the template
    right-aligns dates after one); a heading is a paragraph whose every run is bold and set in capitals.
    """
    if not path.exists():
        return []

    def document_part() -> bytes:
        with zipfile.ZipFile(path) as package:
            return package.read(DOCUMENT_PART)

    root = etree.fromstring(store.retried(document_part), etree.XMLParser(resolve_entities=False))
    w = "{" + NAMESPACES["w"] + "}"
    found = []
    for paragraph in root.iter(w + "p"):
        runs: list[list[Any]] = []
        capitals = True
        for run in paragraph.iter(w + "r"):
            bold = run.find(f"{w}rPr/{w}b") is not None
            for node in run.iter(w + "t", w + "tab"):
                text = "\t" if node.tag == w + "tab" else node.text or ""
                if text:
                    runs.append([text, bold])
            if run.find(f"{w}rPr/{w}caps") is None and run.find(f"{w}rPr/{w}smallCaps") is None:
                capitals = capitals and not "".join(node.text or "" for node in run.iter(w + "t")).strip()
        text = "".join(text for text, _bold in runs)
        if not text.strip():
            continue
        all_bold = all(bold for piece, bold in runs if piece.strip())
        found.append(
            {
                "runs": runs,
                "bullet": paragraph.find(f"{w}pPr/{w}numPr") is not None,
                "heading": all_bold and (capitals or text.strip() == text.strip().upper()),
            }
        )
    return found
