"""Model call 2: mapping a posting's requirements onto the capability vocabulary.

Sent: the role title, the vocabulary (each capability with its domain), and each requirement's name and
words. Nothing else from the candidate's library is sent, and the request passes the privacy guard first. The
answer gives each requirement its capabilities (usually one; up to three when the requirement names alternatives,
any one of which meets it; none for a bare condition), and the conditions it names (languages, tools, degrees). It
is checked (every requirement once, every name in the vocabulary) and asked for again once with the problems listed.

Several capabilities a requirement: "agent frameworks, LLM orchestration, ML infrastructure or evaluation harnesses"
had no single capability, so it got none and nothing could meet it. The call once also asked for the one or two
domains the role is about; nothing read them (a standing boost for projects in them was judged blind no better than
the page without it), so it no longer asks.

The vocabulary sits in the system prompt, which is then the same text for every posting. The command line
writes the prompt to the one-hour cache at twice the input price; with the vocabulary in the user text, no
call ever read that write back.

Opus 5.5 at medium effort: against Sonnet 4.6 medium, the same benchmark projects or better, its answers
the most repeatable of the models measured, at a third of the time and less cost; high effort bought nothing measured,
and low shifted its answers when postings were batched.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

from ..library.capabilities import vocabulary_hash
from ..privacy import check_outgoing
from ..records import CallRecord, CapabilityMapping, Requirement
from . import model_call
from .model_call import Effort, JsonAnswer, answering_model, ask_checked, call_outcome, problems_note
from .store import MAPPING_FORMAT

MODEL = "claude-opus-5-5"
EFFORT: Effort = "medium"

SYSTEM_PROMPT = """You map the requirements of a job posting onto a fixed list of capabilities.

For each requirement give:
- "capabilities": the capabilities from the list that the requirement asks the candidate to be able to do,
  spelled exactly as listed. Usually one. Give two or three only when the requirement names alternatives, any
  one of which would satisfy it ("agent frameworks, LLM orchestration or evaluation harnesses"); for a
  requirement joining several abilities, give the main one. The name in square brackets after each capability
  is the domain it belongs to; it is context, never an answer. Give [] when the requirement names only a
  condition (a degree, years of experience, a language, tool, platform or clearance) or when no capability in
  the list fits. Do not force the nearest capability.
- "conditions": the languages, tools, platforms, frameworks, degrees, certifications or years of
  experience the requirement names as needed, each as a short string; [] if there are none.

Output ONLY JSON: {"items": [{"id": <int>, "capabilities": ["<name>", ...], "conditions": ["..."]}, ...]}
Every requirement appears exactly once."""

# The most capabilities one requirement may name.
MAX_CAPABILITIES = 3

# The model writes American spellings the vocabulary does not use.
SPELLINGS = (
    ("modeling", "modelling"),
    ("optimization", "optimisation"),
    ("defense", "defence"),
    ("license", "licence"),
    ("visualization", "visualisation"),
)


def _folded(name: str) -> str:
    folded = name.strip().lower()
    for american, british in SPELLINGS:
        folded = folded.replace(american, british)
    return folded


def canonical_name(name: object, vocabulary: dict[str, str]) -> str | None:
    """The vocabulary's own name for a capability the model spelled differently, or None.

    Both sides are folded the same way, so a vocabulary written in American spelling matches too.
    """
    if not isinstance(name, str):
        return None
    return {_folded(capability): capability for capability in vocabulary}.get(_folded(name))


def system_prompt(vocabulary: dict[str, str]) -> str:
    # One capability per line with its domain in brackets. Listing "Domain: capability; capability" made
    # the model answer with domain names.
    """Call 2's instructions and every capability with its domain: the same text for every posting."""
    capabilities = "\n".join(f"- {capability} [{domain}]" for capability, domain in vocabulary.items())
    return f"{SYSTEM_PROMPT}\n\nCAPABILITIES:\n{capabilities}"


def build_prompt(title: str, requirements: list[Requirement]) -> str:
    """Call 2's user text: the role and every requirement by number."""
    items = "\n".join(
        f"[{index}] ({'required' if requirement['required'] else 'preferred'}) {requirement['name']} | "
        f"words: {', '.join(requirement['tokens'])}"
        for index, requirement in enumerate(requirements)
    )
    return f"ROLE: {title}\n\nREQUIREMENTS:\n{items}"


# Several postings in one call: the one-posting instructions with their opening and answer format reworded.
BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "You map the requirements of a job posting onto a fixed list of capabilities.",
    "You map the requirements of several job postings onto one fixed list of capabilities. Answer each posting on its "
    "own: a requirement's answer depends only on that posting.",
).replace(
    'Output ONLY JSON: {"items": [{"id": <int>, "capabilities": ["<name>", ...], "conditions": ["..."]}, ...]}\n'
    "Every requirement appears exactly once.",
    'Output ONLY JSON: {"postings": [{"posting": <int>, "items": [{"id": <int>, "capabilities": ["<name>", ...], '
    '"conditions": ["..."]}, ...]}, ...]}\nEvery posting appears exactly once, and within it every requirement appears '
    "exactly once, numbered as in that posting.",
)


def batch_system_prompt(vocabulary: dict[str, str]) -> str:
    """`system_prompt` for several postings in one call."""
    return system_prompt(vocabulary).replace(SYSTEM_PROMPT, BATCH_SYSTEM_PROMPT)


def batch_prompt(postings: list[tuple[str, list[Requirement]]]) -> str:
    """Each posting's `build_prompt`, numbered from 0."""
    return "\n\n".join(
        f"POSTING {number}\n{build_prompt(title, requirements)}"
        for number, (title, requirements) in enumerate(postings)
    )


def answered_capabilities(item: dict[str, Any]) -> list[object] | None:
    """The capability names an answer item gives, "none" left out; None when they are missing or not a list.

    Missing counts as wrong, so an answer in the older one-capability form is asked for again.
    """
    names = item.get("capabilities")
    return [name for name in names if name != "none"] if isinstance(names, list) else None


def answer_problems(answer: Any, requirement_count: int, vocabulary: dict[str, str]) -> list[str]:
    """What is wrong with the model's JSON answer; [] when it can be used."""
    items = answer.get("items") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return ['the answer must be one JSON object with an "items" list']
    problems: list[str] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            problems.append(f"every item must be an object; found {item!r}")
            continue
        index = item.get("id")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < requirement_count:
            problems.append(f"item id {index!r} is not a requirement number from 0 to {requirement_count - 1}")
            continue
        if index in seen:
            problems.append(f"item {index} appears more than once")
        seen.add(index)
        names = answered_capabilities(item)
        if names is None or len(names) > MAX_CAPABILITIES:
            problems.append(f"item {index}: capabilities must be a list of at most {MAX_CAPABILITIES} names")
        else:
            problems += [
                f"item {index}: '{name}' is not a capability in the list"
                for name in names
                if canonical_name(name, vocabulary) is None
            ]
        if not isinstance(item.get("conditions", []), list):
            problems.append(f"item {index}: conditions must be a list")
    missing = [index for index in range(requirement_count) if index not in seen]
    if missing:
        problems.append(f"missing items {missing}")
    return problems


def map_requirements(
    title: str,
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    call: Callable[[str, str], JsonAnswer] | None = None,
) -> CapabilityMapping:
    """The saved-mapping record for one posting.

    `call(system, user)` returns {"json", "input_tokens", "output_tokens"}; by default it is one Opus 5.5 medium-effort
    call through the signed-in CLI. Raises PrivacyError, before any call, when the request holds an identity word.
    """
    call = call or _default_call
    system, user = system_prompt(vocabulary), build_prompt(title, requirements)
    check_outgoing(f"{system}\n{user}")
    checked = ask_checked(
        call,
        system,
        user,
        lambda answer: answer_problems(answer["json"], len(requirements), vocabulary),
        problems_note,
    )
    answer = checked.answer["json"]
    return _record(
        title, requirements, vocabulary, answer, call_outcome(checked), answered=answering_model(checked.answer)
    )


def map_many(
    postings: list[tuple[str, list[Requirement]]],
    vocabulary: dict[str, str],
    call: Callable[[str, str], JsonAnswer] | None = None,
) -> list[CapabilityMapping]:
    """The saved-mapping record for each of several (title, requirements) postings, from one call for all of them.

    Up to five a call measured no worse than one a call, in less time per posting. A posting that the
    answer leaves out or answers unusably is asked for again on its own through `map_requirements`, with its check and
    retry. Each batched record carries the batch's size and an equal share of its tokens; its seconds are the call's.
    """
    if len(postings) == 1:
        return [map_requirements(*postings[0], vocabulary, call)]
    call = call or _default_call
    system, user = batch_system_prompt(vocabulary), batch_prompt(postings)
    check_outgoing(f"{system}\n{user}")
    started = time.perf_counter()
    answer = call(system, user)
    seconds = round(time.perf_counter() - started, 1)
    body = answer.get("json")
    entries = body.get("postings") if isinstance(body, dict) else None
    entry_of = {
        entry.get("posting"): entry
        for entry in entries or []
        if isinstance(entry, dict) and isinstance(entry.get("posting"), int)
    }
    batch_size = len(postings)
    records: list[CapabilityMapping] = []
    for number, (title, requirements) in enumerate(postings):
        entry = entry_of.get(number, {})
        if answer_problems(entry, len(requirements), vocabulary):
            records.append(map_requirements(title, requirements, vocabulary, call))
            continue
        outcome: CallRecord = {
            "valid": True,
            "errors": [],
            "first_errors": [],
            "attempts": 1,
            "input_tokens": round(answer.get("input_tokens", 0) / batch_size),
            "output_tokens": round(answer.get("output_tokens", 0) / batch_size),
            "seconds": seconds,
            "batch": batch_size,
        }
        records.append(_record(title, requirements, vocabulary, entry, outcome, answered=answering_model(answer)))
    return records


def _default_call(system: str, user: str) -> JsonAnswer:
    return model_call.call_json(MODEL, system, user, effort=EFFORT)


def _record(
    title: str,
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    answer: Any,
    outcome: CallRecord,
    *,
    answered: CallRecord,
) -> CapabilityMapping:
    """A mapping record from an answer's items.

    `outcome` holds validity, errors, attempts, tokens, seconds and a batched call's size; `answered` the model that
    answered and any fallback.
    """
    # Still-broken answers are kept too (marked not valid); their unusable items are skipped here. `answer` is the
    # answer's JSON object for this posting: its items.
    items = answer.get("items") if isinstance(answer, dict) else None
    items_by_index = (
        {item.get("id"): item for item in items if isinstance(item, dict) and isinstance(item.get("id"), int)}
        if isinstance(items, list)
        else {}
    )
    mapped: list[Requirement] = []
    for index, requirement in enumerate(requirements):
        item = items_by_index.get(index, {})
        names = [canonical_name(name, vocabulary) for name in answered_capabilities(item) or []]
        capabilities = list(dict.fromkeys(name for name in names if name))[:MAX_CAPABILITIES]
        mapped.append(
            {
                **requirement,
                "capabilities": capabilities,
                # The first, kept under its old name in the saved mapping; pages and the dashboard read every one.
                "capability": capabilities[0] if capabilities else None,
                "conditions": [str(condition) for condition in item.get("conditions") or []],
            }
        )
    # "errors" and "first_errors" keep the names saved mappings have always used.
    return {
        "requirements": mapped,
        **outcome,
        "title": title,
        "vocabulary_sha256_16": vocabulary_hash(vocabulary),
        "format": MAPPING_FORMAT,
        "model": MODEL,
        **answered,
        "made": dt.datetime.now().isoformat(timespec="seconds"),
    }
