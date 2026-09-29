"""Model call 1: reading a posting's requirements.

One Sonnet 4.6 call at low effort fills a fixed line template. The posting goes in as numbered lines; the
answer comes back as one `REQ:` line per requirement carrying a label, the words that would show it is
met, and the number of the posting line it came from. Code builds the requirement records and resolves each
line number to the posting's own sentence, so the model never copies text back, which keeps the answer
short (about 500 output tokens and 12 seconds, measured while each line also carried words for closely
related work, a slot since dropped, as no code read it).

The template states what each slot holds and gives no counts: a count is a target the model pads towards,
and padding invents requirements. The scope is stated instead, because without it the model read
"requirements" as the qualifications list alone and dropped the duties.

Posting facts (salary, sponsorship, years) are not asked for; the board and `facts.py` give them free.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, NamedTuple

from ..records import Requirement
from . import model_call
from .model_call import Effort, ModelAnswerError, TextAnswer, answering_model, ask_checked

# Full model names: the command line's short names change meaning between its versions.
MODEL = "claude-sonnet-4-6"
EFFORT: Effort = "low"
READER = f"slots-{MODEL}-{EFFORT}"
# Postings are cut at this length so readings stay comparable with those saved before; no posting read so
# far has had requirements past it.
MAX_POSTING_CHARACTERS = 14000
MIN_REQUIREMENTS = 3

# Soft items that slip through the prompt's own rule against them. A hiring condition is dropped wherever its phrase
# appears; a soft skill only when nothing is left of the label without it. A word stem alone would also drop technical
# work: "High availability", "ELF relocations", "Communicate evaluation results", "Purple teaming with the Blue Team".
HIRING_CONDITIONS = re.compile(
    r"\b(?:availab\w* (?:to|for) (?:start|begin|work|join)|(?:intern|internship) availability|start dates?"
    r"|(?:prior|previous) internships?|internship experience|(?:willing\w*|able) to relocate|relocation|relocate to"
    r"|work authori[sz]ation|authori[sz]ed to work|visa (?:sponsorship|status|support)|sponsor\w* (?:a )?visas?"
    r"|work visas?)\b",
    re.I,
)
SOFT_SKILLS = re.compile(
    r"\b(?:communicat\w*|collaborat\w*|teamwork|team player|cross[- ]?functional\w*|culture|self[- ]starter"
    r"|passion\w*|interpersonal)\b",
    re.I,
)
# Words that ask for nothing of their own beside a soft skill ("strong written and verbal communication skills").
SOFT_SKILL_FILLER = re.compile(
    r"\b(?:strong|excellent|good|great|clear\w*|effective\w*|proven|demonstrated|ability|able|skills?|experience|fit"
    r"|written|verbal|oral|and|or|with|in|of|for|to|the|a|an|across|work\w*|teams?|stakeholders?|partners?"
    r"|colleagues|others|product|engineering|business|functions?|environment|approach)\b",
    re.I,
)


def is_soft(label: str) -> bool:
    """Whether a requirement label is a hiring condition, or a soft skill with nothing beside it."""
    if HIRING_CONDITIONS.search(label):
        return True
    if not SOFT_SKILLS.search(label):
        return False
    rest = SOFT_SKILL_FILLER.sub(" ", SOFT_SKILLS.sub(" ", label))
    return not re.search(r"[^\W_]", rest)


SYSTEM_PROMPT = """You extract the requirements of a job posting for a resume-matching system.
Be literal: requirements come from the posting. Normalise aliases (AWS = Amazon Web Services; K8s = Kubernetes).
Treat "X or Y" as ONE requirement whose words include both X and Y.
Requirements are TECHNICAL and VERIFIABLE only: skills, tools, domains, credentials, certifications, years/level.
Never emit soft skills or culture items (communication, collaboration, cross-functional, teamwork, ownership,
startup pace, adaptability, curiosity, availability/start dates, "passion", "self-starter"). Never emit the degree twice.
Answer ONLY in the line format you are given, one item per line, nothing else."""

USER_PROMPT_TEMPLATE = """Read the numbered posting below, then fill in this template. Each <...> says what goes there.

REQ: <R or P> | <label> | <evidence> | <line>
KEYWORDS: <the terms a recruiter would search for to find candidates for this role, separated by ;>

For each REQ line:
- R or P: R if the posting treats it as a minimum qualification or a core duty; P if it is preferred, a
  bonus or nice to have.
- label: a short name for the requirement, in the posting's terms.
- evidence: the words or phrases that, found in a resume, would show this requirement is met: the
  posting's own wording for it and the standard names others use for the same thing (for example AWS
  and Amazon Web Services). Only words that mean this requirement. Separated by ;
- line: the number of the posting line the requirement comes from.
Read the whole posting. What the role does (its responsibilities and duties) and what it asks the
person to have (qualifications) are both requirements. Give each its own REQ line; a sentence that
asks for several things gets one REQ line for each.

POSTING (numbered lines):
%s"""

RETRY_NOTE = (
    "\n\nYOUR PREVIOUS ANSWER HAD FEWER THAN THREE READABLE REQ LINES. "
    "Answer again, only in the template's line format."
)


def posting_lines(text: str) -> list[str]:
    return [line.strip() for line in text[:MAX_POSTING_CHARACTERS].split("\n") if line.strip()]


def _split_list(field: str) -> list[str]:
    return [item.strip() for item in field.split(";") if item.strip()]


# The slots of a REQ line, in the template's order: R or P | label | evidence | line.
REQUIRED_OR_PREFERRED, LABEL, EVIDENCE, LINE = range(4)


class ParsedAnswer(NamedTuple):
    requirements: list[Requirement]
    keywords: list[str]
    dropped: list[str]  # the labels of soft items, left out


def parse_answer(answer: str, lines: list[str]) -> ParsedAnswer:
    """(requirements, keywords, dropped) from a filled template.

    Each requirement keeps the number of its posting line and that line's text; a line that cannot be read is skipped,
    and so is any other line. A soft item is left out and its label kept in `dropped`.
    """
    requirements: list[Requirement] = []
    keywords: list[str] = []
    dropped: list[str] = []
    for raw in answer.split("\n"):
        line = raw.strip().strip("`")
        if line.startswith("REQ:"):
            slots = [slot.strip() for slot in line[len("REQ:") :].split("|")]
            if len(slots) <= EVIDENCE or not slots[LABEL]:
                continue
            if is_soft(slots[LABEL]):
                dropped.append(slots[LABEL])
                continue
            has_line = len(slots) > LINE and slots[LINE].isdigit()
            line_number = int(slots[LINE]) if has_line else None
            requirements.append(
                {
                    "name": slots[LABEL],
                    "tokens": _split_list(slots[EVIDENCE]) or [slots[LABEL]],
                    "required": slots[REQUIRED_OR_PREFERRED].upper().startswith("R"),
                    "line": line_number,
                    "source": lines[line_number] if line_number is not None and line_number < len(lines) else None,
                }
            )
        elif line.startswith("KEYWORDS:"):
            keywords = _split_list(line[len("KEYWORDS:") :])
    return ParsedAnswer(requirements, keywords, dropped)


class RequirementReading(NamedTuple):
    """Call 1's reading of a posting, before it is saved."""

    requirements: list[Requirement]
    keywords: list[str]
    info: dict[str, Any]  # which reader made it, the model that answered and any fallback, attempts, tokens, seconds


def read_requirements(text: str, call: Callable[[str, str], TextAnswer] | None = None) -> RequirementReading:
    """(requirements, keywords, info) for a posting.

    `call(system, user)` returns {"text", "input_tokens", "output_tokens", "seconds"}; by default it is one sonnet low-
    effort call through the signed-in CLI. The call is repeated once when fewer than three requirements can be read.
    Raises ModelAnswerError after that.
    """
    call = call or (
        lambda system_prompt, user_prompt: model_call.call_text(MODEL, system_prompt, user_prompt, effort=EFFORT)
    )
    lines = posting_lines(text)

    def problems_of(answer: TextAnswer) -> list[str]:
        found = parse_answer(answer["text"], lines).requirements
        return [] if len(found) >= MIN_REQUIREMENTS else ["fewer than three readable REQ lines"]

    user_prompt = USER_PROMPT_TEMPLATE % "\n".join(f"[{index}] {line}" for index, line in enumerate(lines))
    checked = ask_checked(call, SYSTEM_PROMPT, user_prompt, problems_of, lambda _problems: RETRY_NOTE)
    if checked.problems:
        raise ModelAnswerError("the model's answer could not be read as requirements, twice")
    parsed = parse_answer(checked.answer["text"], lines)
    return RequirementReading(
        parsed.requirements,
        parsed.keywords,
        {
            "reader": READER,
            **answering_model(checked.answer),
            "attempts": checked.attempts,
            "input_tokens": checked.input_tokens,
            "output_tokens": checked.output_tokens,
            "seconds": checked.seconds,
            "dropped": parsed.dropped,
        },
    )
