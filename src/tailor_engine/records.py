"""The records every part of the engine passes around.

From the library (in memory):
  Bullet        one bullet: its text, the topics it covers (tags, weights summing to 1), the evidence words
                that point a posting's words at those topics, and in capability mode what it demonstrates
  Project       one project: its title, how substantial and recognisable it is, and when it ended
  SkillRow      a row the skills section may show: its titles and skills, each with topic tags
  Degree        a degree: its fixed facts and the courses the coursework line chooses from

From a posting (saved as JSON, so these are TypedDicts and the files on disk load unchanged):
  Posting       the posting's text and where it came from, with the facts its board publishes (BoardFacts)
  Requirement   one thing a posting asks for, as model call 1 read it and, in capability mode, as model
                call 2 mapped it onto the capability vocabulary
  Reading       model call 1's saved answer;  CapabilityMapping  model call 2's saved answer
  ProjectChoice model call 3's saved answer (hybrid mode)
  PageResult    one built page, the measures behind it and the trace of the moves that chose it

How strong a project is (its standing and fill rank) depends on the weights in use, so it is computed per
page in `selection/project_values.py` from the complexity, brand and end date recorded here.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, TypedDict


class TaggedItem(NamedTuple):
    """A skill, skill-row title or course with its topic tags."""

    text: str
    tags: dict[str, float]


RUNNING_END_VALUES = {"present", "ongoing", ""}


@dataclass
class Bullet:
    id: str
    parent: str
    text: str
    tags: dict[str, float]
    evidence: list[str]
    liability: float = 0.0
    fragile: bool = False
    excludes: list[str] = field(default_factory=list)
    pending: bool = False
    # Capability mode only: what the bullet demonstrates and the conditions it establishes, set by
    # `selection.capability_matching.capability_bullets` from the library's capability map.
    capabilities: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)

    @property
    def character_count(self) -> int:
        return len(self.text)


@dataclass
class Project:
    id: str
    title: str
    complexity: float = 0.5
    brand: float = 0.5
    held: bool = False
    ends: str = "present"
    as_of: dt.date = field(default_factory=dt.date.today)

    @property
    def is_running(self) -> bool:
        return self.ends.strip().lower() in RUNNING_END_VALUES

    @property
    def ended(self) -> dt.date | None:
        """The first day of the month the work ended; None while it runs. Loading checks the YYYY-MM form."""
        if self.is_running:
            return None
        match = re.match(r"(\d{4})-(\d{2})", self.ends.strip())
        return dt.date(int(match.group(1)), int(match.group(2)), 1) if match else None

    @property
    def years_old(self) -> float:
        """Years since the work ended; anything still running is zero."""
        ended = self.ended
        return 0.0 if ended is None else max(0.0, (self.as_of - ended).days / 365.25)


class Requirement(TypedDict, total=False):
    name: str
    tokens: list[str]  # words that show the requirement is met
    # Words for closely related work, in older readings only; read by no code since word mode was retired, and no
    # longer asked for.
    partial: list[str]
    required: bool  # a minimum qualification or core duty, rather than a preference
    capability: str | None  # capability mode: the first of `capabilities`, or None
    capabilities: list[str]  # capability mode: the vocabulary capabilities it asks for, any one meeting it
    conditions: list[str]  # capability mode: languages, tools, degrees it names as needed
    line: int | None  # model call 1: its posting line's number, for a person reading the file; no code reads it
    source: str | None  # model call 1: that line's text, for a person reading the file; no code reads it


@dataclass(frozen=True)
class SkillRow:
    id: str
    titles: tuple[TaggedItem, ...]
    members: tuple[TaggedItem, ...]
    prior: float = 0.5  # how much the row is worth on any page, authored
    always_shown: bool = False  # shown on every page as written (`fixed` in skills.toml)


@dataclass(frozen=True)
class Degree:
    id: str
    institution: str
    award: str
    dates: str = ""
    fixed_line: str = ""  # a line always shown under the degree (`fixed` in education.toml)
    evidence: tuple[str, ...] = ()
    # Other ways a posting writes this degree ("bachelor's degree"): shown text that meets a requirement for the
    # degree, and, unlike `evidence`, points at no topic.
    names: tuple[str, ...] = ()
    courses: tuple[TaggedItem, ...] = ()


# ─────────────────────────────────────────────────────────────
# Postings and what is read from them
# ─────────────────────────────────────────────────────────────


class PayTier(TypedDict):
    label: str | None
    min: int
    max: int
    currency: str
    period: str  # "year" or "hour"


class Pay(TypedDict, total=False):
    min: int
    max: int
    currency: str | None
    period: str | None
    text: str | None
    tiers: list[PayTier]


class BoardFacts(TypedDict, total=False):
    source: str
    pay: Pay | None
    work_mode: str | None
    location: str | None


class Posting(TypedDict):
    id: str
    url: str | None
    source: str
    title: str
    company: str
    text: str
    board: BoardFacts


class Reading(TypedDict, total=False):
    id: str
    sha: str  # the text key again (it is in the file name), for a person reading the file; no code reads it
    requirements: list[Requirement]
    keywords: list[str]
    # Which reader, the model that answered and any fallback, attempts, tokens, seconds (older readings also hold a
    # family and level, and lack the answering model).
    info: dict[str, Any]


class Fallback(TypedDict):
    """The command line's notice that the model asked for refused the call and another answered it.

    Opus 5.5's safety classifier stops call 3 and the judge (category "cyber"), and Opus 4.8 answers.
    """

    original_model: str | None
    fallback_model: str | None
    api_refusal_category: str | None


class CallRecord(TypedDict, total=False):
    """What the saved answer of model calls 2 and 3 keeps of the call itself."""

    valid: bool
    errors: list[str]
    first_errors: list[str]
    attempts: int  # every request made, retries included
    # The requests one posting's call was split into, sent at once: Jev's call 2 only; `attempts` counts the
    # requests made for all of them, so the retries are `attempts - requests`. One when missing.
    requests: int
    input_tokens: int
    output_tokens: int
    seconds: float
    # Postings answered by the same call, when more than one: call 2's batched call only. Here rather than on
    # CapabilityMapping so a mapping record can be built from a CallRecord outcome (mypy checks the spread's keys).
    batch: int
    # The capability descriptions Jev's call 2 was asked with (Jev's call 2 only), here for the same reason.
    descriptions_sha256_16: str
    model: str  # the model asked for
    answered_by: str | None  # the model that wrote the kept answer, as the command line reports it
    fallback: Fallback | None  # set when the model asked for refused and the command line fell back to another
    made: str
    posting: str  # the posting's id


class CapabilityMapping(CallRecord, total=False):
    requirements: list[Requirement]
    title: str
    vocabulary_sha256_16: str
    format: int  # `store.MAPPING_FORMAT` when made


class ProjectChoice(CallRecord, total=False):
    """Model call 3's saved answer: the projects that make the strongest case for one posting."""

    projects: list[str]  # 4 to 6 project ids, best first
    why: str  # the model's one sentence
    projects_sha256_16: str  # the projects it was chosen from (`project_choice.projects_hash`); others make it stale
    format: int  # `store.CHOICE_FORMAT` when made


# ─────────────────────────────────────────────────────────────
# A built page
# ─────────────────────────────────────────────────────────────


class TraceEntry(NamedTuple):
    """One accepted move, saved as a three-item list.

    The pass that made it ("add", "swap", "fill", "prune", "refill"), the bullet or project ids it moved, and the page's
    score after it.
    """

    event: str
    ids: list[str]
    score: float


Trace = list[TraceEntry]


class PageEntry(TypedDict):
    group: str  # the project's id; saved pages keep this key
    title: str
    bullets: list[str]


class SkillRowEntry(TypedDict):
    row: str  # the row's id in skills.toml
    title: str
    members: list[str]


class CourseworkEntry(TypedDict):
    degree: str  # the degree's id in education.toml
    courses: list[str]


class RequirementCredit(TypedDict):
    name: str
    required: bool
    credit: float  # 0 to 1: 1 met, between 0 and 1 met in part, 0 missing
    bullets: list[str]  # the page's bullets that earn it; empty when only text outside the projects does


class CostDetail(TypedDict, total=False):
    liability: float
    redundancy: float


class ExtractionSummary(TypedDict):
    requirements: int
    required: int
    keywords: int


class ShownChoice(TypedDict):
    projects: list[str]
    why: str
    placed: list[str]  # the chosen projects on the page, best first; the selector may leave some out


class PageResult(TypedDict, total=False):
    posting: str  # the posting's title (a mapping's and a choice's "posting" is its id)
    posting_id: str | None
    mode: str
    extraction: ExtractionSummary
    unmet: float
    demand: dict[str, float]
    reach: float
    selected: list[str]
    bullets: int
    project_lines: int
    coverage: float
    emphasis: float
    standing: float
    incoherence: float
    cost: float
    cost_detail: CostDetail
    depth: float
    score: float
    missing_required: list[str]
    requirement_credit: list[RequirementCredit]  # in the reading's order
    trace: Trace  # kept for a person reading the page file, to see how it was chosen; no code reads it
    page: list[PageEntry]
    requirements: list[Requirement]
    unmapped: list[str]  # capability mode: requirements the mapping does not name, met by their words alone
    skill_rows: list[SkillRowEntry]  # the rows printed, fixed rows left out
    # Skills answering a requirement in full, with their share. Nothing in this package reads it; it is
    # kept in the page file for other tools.
    skill_scores: dict[str, float]
    coursework: list[CourseworkEntry]  # in the library's degree order
    projects_by: str  # hybrid mode: "model" when call 3's projects make the page, else "engine"
    choice: ShownChoice  # hybrid mode: call 3's usable answer, used or not
    engine_match: float  # hybrid mode, projects by the model: the engine's own page's match
