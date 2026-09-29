"""Model call 2 answered by TypeSafe's Jev: a probability for each capability a requirement may ask for.

The request is the tuned one ("described capabilities"). Its state holds the role's title and the requirements (names
and words). For each requirement it asks a choice over the capability vocabulary, each capability given as its domain,
what it covers, what it is not for and examples (`library/capability-descriptions.toml`), plus a described "none of
these", and asks what the requirement asks the candidate to do, since a capability that shares a word with it is not
enough. The choices go `PER_REQUEST` requirements to a request, each request with the whole state. One more request
asks, for each of the requirements' words, whether it is a named language, tool, platform, framework, standard,
certification, degree or field of study, clearance or amount of experience the requirement names, alone or as one of
several examples (the conditions). A posting's requests are sent at once. When "none of these" ranks first the
requirement gets no capability; otherwise the capabilities kept are the first choice and up to two more at or above
`THRESHOLD`; the conditions are the words judged yes at 0.5 or more. The record is call 2's saved-mapping record, so
selection reads it as it reads Opus's.

The word question and the "none first" rule replace a first form, which asked whether each word was one "the
candidate must have" (an "or" list then kept one item and a preferred clearance none) and kept a runner-up even when
"none of these" ranked first. Scored against blinded labels of 561 requirements on cached answers, conditions went
from 448 correct / 110 missing / 3 wrong to about 501 / 28 / 32 (Opus 526 / 8 / 27); the capabilities stay below
Opus's (402 correct against 448), so Opus answers call 2 by default. The cached answers' choice requests are these;
their word request also asked two more questions a requirement (does its name name a kind of work, does it list
alternatives), which this read does not use and this request leaves out.

Measured with the first form, against an earlier request that gave each capability only as "a capability in
<domain>": blind judges preferred its pages 6 to 5 on 14 benchmark pages and 8 to 2 on 12 other real postings;
agreement with the advisors on projects 0.689 and 0.693 in two runs against 0.679 and 0.680; the engine's match on the
benchmark 0.551 and 0.569 against 0.640 and 0.657 (Opus's mapping gives 0.541). A posting took 3 requests at the
median (5 for 25 requirements), about 121,000 input tokens and a median of 1.36 to 1.55 s; the word request adds one.

Sent: the role's title, the requirements (names and words), the capability names with their domains and descriptions;
nothing from the candidate's projects. Every request of a posting passes the privacy guard before the first is sent, as
Opus's call 2 does.

When Jev cannot answer any one request (`jev`: no key, a request that failed twice, an answer that does not fit),
`map_requirements` makes call 2 on Opus for the whole posting instead, and the record names the fallback and why.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import Any

from .. import settings
from ..library.capabilities import vocabulary_hash
from ..library.capability_descriptions import (
    NONE,
    CapabilityDescriptions,
    descriptions_hash,
    load_capability_descriptions,
)
from ..privacy import check_outgoing
from ..records import CapabilityMapping, Requirement
from . import capability_mapping, jev
from .jev import JevUnavailableError, Post
from .store import MAPPING_FORMAT

# A second or third alternative is kept at this probability or above; measured at 0.3.
THRESHOLD = 0.3
CONDITION_THRESHOLD = 0.5
# Requirements a request. One choice with every capability described is about 8,000 tokens, and six requirements a
# request measured 55,000 of Jev's 64,000-token limit, so five leave room for a long state.
PER_REQUEST = 5
QUESTION = "Which capability from the list does `requirements.{rid}` ask the candidate to be able to do?"
FOCUS = (
    "Judge what the requirement asks the candidate to do as the posting means it; a capability whose name shares a "
    "word with the requirement is not enough."
)
# The conditions question, word for word as measured (the "separate judgments" variant's word question): the
# engine reads a requirement's conditions as "any one of these", so it asks whether the word is a named item the
# requirement lists, alone or among examples, not whether each one alone is a must-have.
WORD_QUESTION = (
    "Is `requirements.{rid}.words[{n}]` the name of a specific language, tool, platform, framework, standard or "
    "protocol, certification, degree or field of study, clearance, or an amount of experience that "
    "`requirements.{rid}` names, alone or as one of several examples?"
)
WORD_CRITERIA = {
    "true": {
        "what": (
            "a named language, tool, platform, framework, standard or protocol, certification, degree or field of "
            "study, clearance, or amount of experience"
        ),
        "examples": ["AWS", "YARA", "OWASP Top 10", "OSCP", "5+ years", "Top Secret clearance"],
    },
    "false": {
        "what": "a kind of work, practice or skill",
        "examples": ["penetration testing", "threat modelling", "security research"],
    },
}

Mapper = Callable[[str, list[Requirement], dict[str, str]], CapabilityMapping]


def request_bodies(
    title: str, requirements: list[Requirement], vocabulary: dict[str, str], descriptions: CapabilityDescriptions
) -> list[dict[str, Any]]:
    """The requests for one posting, every one holding the whole state.

    The choices over the described capabilities (in the vocabulary's order, and "none of these"), `PER_REQUEST`
    requirements to a request; then one request with a yes-or-no for each word of every requirement.
    """
    state = {
        "role": title,
        "requirements": {
            f"R{index}": {"name": item["name"], "words": item["tokens"], "required": item["required"]}
            for index, item in enumerate(requirements)
        },
    }
    criteria = {name: descriptions[name] for name in vocabulary}
    criteria[NONE] = descriptions[NONE]
    bodies: list[dict[str, Any]] = []
    for start in range(0, len(requirements), PER_REQUEST):
        questions: dict[str, Any] = {}
        for index in range(start, min(start + PER_REQUEST, len(requirements))):
            rid = f"R{index}"
            questions[f"{rid}|capability"] = {
                "type": "choice",
                "instructions": {"question": QUESTION.format(rid=rid), "focus": FOCUS},
                "criteria": criteria,
            }
        bodies.append({"model": jev.MODEL, "state": state, "questions": questions})
    words = {
        f"R{index}|word{number}": {
            "type": "noul",
            "instructions": WORD_QUESTION.format(rid=f"R{index}", n=number),
            "criteria": WORD_CRITERIA,
        }
        for index, item in enumerate(requirements)
        for number in range(len(item["tokens"]))
    }
    if words:
        bodies.append({"model": jev.MODEL, "state": state, "questions": words})
    return bodies


def mapped(
    answers: dict[str, Any], requirements: list[Requirement], vocabulary: dict[str, str], threshold: float = THRESHOLD
) -> list[Requirement]:
    """Each requirement with the capabilities and conditions Jev's answers give it.

    Raises JevUnavailableError when any question's answer is missing or does not fit (a probability that is not a
    number, an option outside the list), so a partial answer is never saved as a mapping.
    """
    out: list[Requirement] = []
    for index, item in enumerate(requirements):
        choice = answers.get(f"R{index}|capability")
        probabilities = choice.get("probabilities") if isinstance(choice, dict) else None
        if not isinstance(probabilities, dict) or not probabilities:
            raise JevUnavailableError(f"no answer for requirement {index}")
        if not set(probabilities) <= {*vocabulary, NONE}:
            raise JevUnavailableError(f"an option outside the list for requirement {index}")
        chances = {option: jev.number(probabilities, option) for option in probabilities}
        ranked = sorted(chances, key=lambda option: -chances[option])
        # "None of these" first means none: a runner-up then is Jev's doubt, not a second ask.
        capabilities = (
            []
            if ranked[0] == NONE
            else [
                option
                for option in ranked[:3]
                if option != NONE and (option == ranked[0] or chances[option] >= threshold)
            ]
        )
        conditions = [
            word
            for number, word in enumerate(item["tokens"])
            if jev.number(answers.get(f"R{index}|word{number}"), "noul") >= CONDITION_THRESHOLD
        ]
        out.append(
            {
                **item,
                "capabilities": capabilities,
                "capability": capabilities[0] if capabilities else None,
                "conditions": conditions,
            }
        )
    return out


def ask(
    title: str,
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    *,
    descriptions: CapabilityDescriptions | None = None,
    post: Post | None = None,
    key: Callable[[], str] | None = None,
    slot: AbstractContextManager[Any] | None = None,
) -> CapabilityMapping:
    """The saved-mapping record from the posting's Jev requests, sent at once.

    `descriptions` are the library's (`settings.LIBRARY`) unless given. Raises PrivacyError, before any request, when a
    request holds an identity word, and JevUnavailableError when Jev cannot answer every question of every request.
    """
    described = _described(descriptions, vocabulary)
    bodies = request_bodies(title, requirements, vocabulary, described)
    check_outgoing(jev.sent_text(bodies))
    started = time.perf_counter()
    answered = _answered(bodies, post=post, key=key, slot=slot)
    # Each request's answers to its own questions only: a question it left out stays missing, and `mapped` refuses it.
    answers = {
        name: one.answers.get(name) for body, one in zip(bodies, answered, strict=True) for name in body["questions"]
    }
    return {
        "requirements": mapped(answers, requirements, vocabulary),
        "valid": True,
        "errors": [],
        "first_errors": [reason for one in answered for reason in one.first_errors],
        "attempts": sum(one.attempts for one in answered),
        "requests": len(bodies),
        "input_tokens": sum(one.input_tokens for one in answered),
        "output_tokens": sum(one.output_tokens for one in answered),
        "seconds": round(time.perf_counter() - started, 2),
        "title": title,
        "vocabulary_sha256_16": vocabulary_hash(vocabulary),
        "descriptions_sha256_16": descriptions_hash(described),
        "format": MAPPING_FORMAT,
        "model": jev.MODEL,
        "answered_by": ", ".join(dict.fromkeys(one.answered_by for one in answered)) or None,
        "fallback": None,
        "made": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _described(descriptions: CapabilityDescriptions | None, vocabulary: dict[str, str]) -> CapabilityDescriptions:
    return descriptions if descriptions is not None else load_capability_descriptions(settings.LIBRARY, vocabulary)


def _answered(
    bodies: list[dict[str, Any]],
    *,
    post: Post | None,
    key: Callable[[], str] | None,
    slot: AbstractContextManager[Any] | None,
) -> list[jev.Answers]:
    """Jev's answers to each request, all sent at once, each holding `slot` while in flight.

    Once every request has ended, the first that Jev could not answer raises its JevUnavailableError.
    """
    if not bodies:
        return []
    with ThreadPoolExecutor(max_workers=len(bodies), thread_name_prefix="jev-call2") as pool:
        asked = [pool.submit(jev.ask, body, post=post, key=key, slot=slot) for body in bodies]
        return [one.result() for one in asked]


def map_requirements(
    title: str,
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    *,
    descriptions: CapabilityDescriptions | None = None,
    post: Post | None = None,
    key: Callable[[], str] | None = None,
    slot: AbstractContextManager[Any] | None = None,
    fallback: Mapper | None = None,
) -> CapabilityMapping:
    """Call 2 on Jev; on Opus when Jev cannot answer: `fallback`, by default `capability_mapping.map_requirements`.

    `descriptions` are the library's (`settings.LIBRARY`) unless given. The dashboard passes its batcher as
    `fallback`, so Opus's call 2 stays batched there (a posting read on its own still goes at once); the command line's
    default is one Opus call for the posting.
    """
    described = _described(descriptions, vocabulary)
    started = time.perf_counter()
    try:
        return ask(title, requirements, vocabulary, descriptions=described, post=post, key=key, slot=slot)
    except JevUnavailableError as error:
        waited = time.perf_counter() - started
        record = (fallback or capability_mapping.map_requirements)(title, requirements, vocabulary)
        jev.fell_back(record, error, seconds=waited)
        return record
