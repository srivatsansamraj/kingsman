"""A posting's facts, read by rule with no model.

Salary, years, degree, sponsorship, clearance, work mode, location and level. Years and degree count only
where the posting requires them, so the required lines are found first. Job boards publish postings under
their own headings, so whether a line is required, preferred or a duty can be read from the heading above it:
required (qualifications, "what we look for", "you may be a good fit if"), preferred ("bonus", "nice to have",
"strong candidates may also"), duties (responsibilities, "what you'll do"), and stop sections (benefits, pay,
legal, the company). A posting with no recognisable required or preferred headings falls back to every
sentence-length line.

`posting_facts` prefers what the job board publishes structured (pay, work mode, location; read from the board's answer
in `board_facts.py`) over the text.
The posting's requirements for selection are read by model call 1 (`requirements.py`), not here.
"""

from __future__ import annotations

import re
from typing import Any

from ..records import Pay, Posting

# ─────────────────────────────────────────────────────────────
# Requirement sections
# ─────────────────────────────────────────────────────────────


PREFERRED_HEADING_WORDS = (
    "preferred",
    "bonus",
    "nice to have",
    "nice-to-have",
    "extra credit",
    "we'd love",
    "we would love",
    "would be great",
    "strong candidates may also",
    "desired",
    "a plus",
    "added plus",
    "pluses",
    "good to have",
    "ideally",
    "even better",
)
# Not "desirable": one board heads its main qualifications "Desirable Skills, Knowledge, and Experience"
# and its extras "Bonus Points", so there it means required.
REQUIRED_HEADING_WORDS = (
    "requirement",
    "qualification",
    "what we look for",
    "what we're looking for",
    "what we are looking for",
    "you have",
    "you should have",
    "you'll need",
    "you will need",
    "you bring",
    "what you bring",
    "must have",
    "about you",
    "who you are",
    "your background",
    "you may be a good fit",
    "you might be a good fit",
    "you might thrive",
    "skills",
    "your experience",
    "what you need",
    "what you'll bring",
    "you are",
    "ideal candidate",
    "competencies",
    "experience",
    "must be",
)
# Words that also open ordinary requirement lines ("Basic technical experience with pentesting"), so they
# make a heading only in a line of four words or fewer, or one ending in a colon.
SHORT_LINE_HEADING_WORDS = ("experience", "skills", "you are", "must be", "you have")
DUTY_HEADING_WORDS = (
    "responsibilit",
    "what you'll do",
    "what you will do",
    "what you'll be doing",
    "in this role",
    "the role",
    "day to day",
    "day-to-day",
    "your impact",
    "what you'll work on",
    "the work",
    "you will",
    "your role",
    "example projects",
    "key duties",
    "job duties",
)
STOP_HEADING_WORDS = (
    "benefit",
    "compensation",
    "salary",
    "pay range",
    "pay transparency",
    "about us",
    "about the company",
    "who we are",
    "why ",
    "perks",
    "equal opportunity",
    "eeo",
    "our commitment",
    "logistics",
    "how we're different",
    "annual salary",
    "base salary",
    "life at",
    "our culture",
    "accommodation",
    "privacy",
    "hiring process",
    "interview",
    "mentors",
    "research areas",
    "past projects",
    "what to expect",
    "overview",
    "available locations",
    "representative projects",
    "what we offer",
    "we offer",
)


def _clean_lines(text: str) -> list[str]:
    """The text's non-empty lines, spacing collapsed and list bullets stripped."""
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip(" \t•·-–—*▪◦●")
        if line:
            lines.append(line)
    return lines


# Title case: at least this share of the words of four letters or more start with a capital.
TITLE_CASE_WORD_LETTERS = 4
TITLE_CASE_SHARE = 0.6


def _is_title_case(words: list[str]) -> bool:
    """Whether most of the longer words are capitalised, as in a heading.

    "Desirable Skills, Knowledge, and Experience" is a heading; "Basic technical experience with pentesting" is a
    requirement.
    """
    long_words = [word for word in words if len(word) >= TITLE_CASE_WORD_LETTERS]
    return bool(long_words) and sum(word[0].isupper() for word in long_words) / len(long_words) >= TITLE_CASE_SHARE


# A heading is short and does not read as a sentence. The limits were set by hand.
HEADING_MAX_CHARACTERS = 90
HEADING_MAX_WORDS = 12
# "About the team" opens a section; a longer line starting "About" is prose.
ABOUT_HEADING_MAX_WORDS = 6
# Without a colon, a line is a heading only when this short; the SHORT_LINE_HEADING_WORDS need it shorter
# still, or title case.
BARE_HEADING_MAX_WORDS = 8
SHORT_HEADING_MAX_WORDS = 4


def _heading_kind(line: str) -> str | None:
    """The kind of section a line opens, or None if it is not a heading."""
    words = line.split()
    is_heading_shaped = (
        len(line) <= HEADING_MAX_CHARACTERS
        and len(words) <= HEADING_MAX_WORDS
        and (line.endswith(":") or not line.endswith("."))
    )
    if not is_heading_shaped:
        return None
    lowered = line.lower().rstrip(":").strip()
    if lowered.startswith("about ") and not lowered.startswith("about you") and len(words) <= ABOUT_HEADING_MAX_WORDS:
        # "About the role", "About the team + role" describe the work; "About <company>" does not.
        return "duty" if re.search(r"\b(role|team|position|job|opportunity)\b", lowered) else "stop"
    for kind, keys in (
        ("stop", STOP_HEADING_WORDS),
        ("preferred", PREFERRED_HEADING_WORDS),
        ("required", REQUIRED_HEADING_WORDS),
        ("duty", DUTY_HEADING_WORDS),
    ):
        hits = [key for key in keys if key in lowered]
        # A short line ending in a colon is a heading; without the colon only a near-pure match is.
        if not hits or not (line.endswith(":") or len(words) <= BARE_HEADING_MAX_WORDS):
            continue
        if (
            not line.endswith(":")
            and len(words) > SHORT_HEADING_MAX_WORDS
            and all(hit in SHORT_LINE_HEADING_WORDS for hit in hits)
            and not _is_title_case(words)
        ):
            continue
        return kind
    return "unknown" if line.endswith(":") else None


# A line under a required heading counts at this length: shorter is a fragment, longer a paragraph about
# something else. With no sections found, sentence-length lines count. The limits were set by hand.
REQUIRED_LINE_MIN_CHARACTERS = 12
REQUIRED_LINE_MAX_CHARACTERS = 600
FALLBACK_LINE_MIN_CHARACTERS = 30
FALLBACK_LINE_MAX_CHARACTERS = 300


def _required_lines(lines: list[str]) -> list[str]:
    """The lines under required headings.

    A posting with no required or preferred heading has no sections this reader knows, and every sentence-length line
    counts.
    """
    section = "duty"
    required: list[str] = []
    has_qualification_heading = False
    for line in lines:
        kind = _heading_kind(line)
        if kind == "unknown":
            # A heading the lists do not know ("Examples of what you may work on:") keeps the section it is
            # in, and ends a stop section.
            section = "duty" if section == "stop" else section
            continue
        if kind:
            section = kind
            has_qualification_heading = has_qualification_heading or kind in ("required", "preferred")
            continue
        if section == "required" and REQUIRED_LINE_MIN_CHARACTERS <= len(line) <= REQUIRED_LINE_MAX_CHARACTERS:
            required.append(line)
    if not has_qualification_heading:
        return [line for line in lines if FALLBACK_LINE_MIN_CHARACTERS <= len(line) <= FALLBACK_LINE_MAX_CHARACTERS]
    return required


# ─────────────────────────────────────────────────────────────
# Facts in the text
# ─────────────────────────────────────────────────────────────


DEGREE_PATTERN = re.compile(
    r"\b(Ph\.?D|doctorate|Master'?s|M\.S\.|MS\b|M\.Sc|Bachelor'?s|B\.S\.|BS\b|B\.A\.|BA\b|"
    r"B\.?Tech|B\.E\.|undergraduate degree|degree)\b",
    re.I,
)
# A range ("2-12+ years") is read by its lower bound: the second number is matched and skipped.
YEARS_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:or more\s+|plus\s+)?years?\b", re.I)
# Only numbers up to 20 before "years" count as years of experience asked for.
MOST_YEARS_ASKED = 20


CLEARANCE_PATTERN = re.compile(
    r"\b(TS/SCI|top secret|secret (?:security )?clearance|security clearance|"
    r"active clearance|clearance (?:is )?required)\b",
    re.I,
)


SALARY_RANGE_PATTERN = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?\s*(?:-|–|—|to)\s*\$?\s?"
    r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
)
# A dollar range under this is an hourly rate; the words just after a range are searched for "per hour".
HOURLY_RATE_BELOW = 1000
PERIOD_WINDOW_CHARACTERS = 40
# The posting's own salary line is kept for people to read, cut at this length.
SALARY_LINE_CHARACTERS = 200


def _salary_in(text: str) -> Pay | None:
    """The first dollar range in the text, or None; a range under 1,000 or marked hourly is an hourly rate."""
    match = SALARY_RANGE_PATTERN.search(text)
    if match is None:
        return None
    low = float(match.group(1).replace(",", "")) * (1000 if match.group(2) else 1)
    high = float(match.group(3).replace(",", "")) * (1000 if match.group(4) else 1)
    after = text[match.end() : match.end() + PERIOD_WINDOW_CHARACTERS].lower()
    period = "hour" if re.search(r"/\s?h(ou)?r|per hour|hourly", after) or high < HOURLY_RATE_BELOW else "year"
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    return {
        "min": int(low),
        "max": int(high),
        "currency": "USD",
        "period": period,
        "text": text[start : end if end > 0 else None].strip()[:SALARY_LINE_CHARACTERS],
    }


def level_of(title: str) -> str:
    # "Member of Technical Staff" is a job title at several labs, not the staff level.
    """The seniority a title states, or "unstated"."""
    lowered = re.sub(r"\bmember of (the )?technical staff\b", " ", title.lower())
    for level, keys in (
        ("intern", ("intern", "internship")),
        ("principal", ("principal", "distinguished")),
        ("staff", ("staff",)),
        ("senior", ("senior", "sr", "lead")),
        ("entry", ("new grad", "entry", "junior", "early career", "associate", "graduate")),
    ):
        # Whole words only: "Internal Controls" is not an internship.
        if any(re.search(r"\b" + re.escape(key) + r"\b", lowered) for key in keys):
            return level
    return "unstated"


def _work_mode_in(lowered_text: str) -> str:
    """The first of remote, hybrid and onsite the text states, in that order.

    A remote role that mentions visiting an office stays remote; "not remote" does not count as remote.
    """
    if re.search(r"\bremote\b", lowered_text) and not re.search(r"\bnot remote|no remote", lowered_text):
        return "remote"
    if re.search(r"\bhybrid\b|in (?:the )?office \d|\d\s*days? (?:a|per) week", lowered_text):
        return "hybrid"
    if re.search(r"\bon-?site\b|in-office|in office", lowered_text):
        return "onsite"
    return "unstated"


NO_SPONSORSHIP_PATTERN = re.compile(
    r"\b(unable|not able|cannot|can't|will not|won't|do not|don't|does not|doesn't|"
    r"not currently|no)\s+(?:to\s+)?(?:be\s+)?(?:able\s+to\s+)?"
    r"(?:sponsor|(?:offer|provide|support)[^.]{0,40}\bsponsor)"
    r"|\bno\s+(?:visa\s+)?sponsorship|sponsorship is not available",
    re.I,
)
SPONSORSHIP_PATTERN = re.compile(
    r"\bwe (?:do |can |will )?sponsor|\bvisa sponsorship (?:is )?(?:available|provided|offered)|"
    r"\bsponsor visas|\bwill sponsor|\bsponsorship (?:is )?available",
    re.I,
)


def _sponsorship_in(text: str) -> str:
    """Whether the text offers visa sponsorship: "no", "yes" or "unstated".

    The refusal is checked first, since a posting that refuses sponsorship often names it too.
    """
    if NO_SPONSORSHIP_PATTERN.search(text):
        return "no"
    if SPONSORSHIP_PATTERN.search(text):
        return "yes"
    return "unstated"


# ─────────────────────────────────────────────────────────────
# Posting facts
# ─────────────────────────────────────────────────────────────


def posting_facts(posting: Posting) -> dict[str, Any]:
    """The facts a job search filters on: the board's structured fields where it gives them, the text's otherwise.

    `posting` is {"text", "title", "company", "board"}.
    """
    text = posting["text"]
    board = posting.get("board") or {}
    lines = _clean_lines(text)
    required_text = "\n".join(_required_lines(lines))
    years = [
        int(match.group(1))
        for match in YEARS_PATTERN.finditer(required_text)
        if int(match.group(1)) <= MOST_YEARS_ASKED
    ]
    degree = DEGREE_PATTERN.search(required_text)
    location = next((line.split(":", 1)[1].strip() for line in lines if re.match(r"(?i)^locations?:", line)), None)
    pay = board.get("pay")
    text_salary = _salary_in(text)
    title = posting.get("title", "")
    return {
        "title": title,
        "company": posting.get("company", ""),
        "location": board.get("location") or location,
        "work_mode": board.get("work_mode") or _work_mode_in(text.lower()),
        "level": level_of(title),
        "years_required": max(years) if years else None,
        "sponsorship": _sponsorship_in(text),
        "clearance_required": bool(CLEARANCE_PATTERN.search(text)),
        "degree_required": degree.group(0) if degree else None,
        "salary": (
            {**pay, "from": board.get("source")}
            if pay
            else ({**text_salary, "from": "posting text"} if text_salary and text_salary["min"] else None)
        ),
    }
