"""Reading a posting end to end: fetch or paste it, then make and save its reading, mapping and project choice once.

  posting_from_url / posting_from_text   {"id", "url", "source", "title", "company", "text", "board"}
  read_posting                           the saved reading and mapping, each made by a model call only when
                                         missing, stale, or asked for again
  project_choice_for                     the saved call 3 answer, made the same way; stale once the projects it
                                         was chosen from change

A mapping names capabilities, so it is stale once the vocabulary it was made under has changed, and Jev's once the
capability descriptions it was asked with have; `read_posting` makes it again. A mapping also answers one reading's
requirements, so a reading made again always gets a new mapping. A reading does not depend on the vocabulary and is
never remade unless asked.

The three model calls are looked up on their modules when they are made, so a test or the dev server can stand in for
them there. `call2_mapper` and `call3_chooser` give calls 2 and 3 answered by a model; `call2_model` names the model
that answers call 2 under the classifier and call 2 settings.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from .. import settings
from ..library import Library
from ..library.capabilities import load_capability_vocabulary, vocabulary_hash
from ..library.capability_descriptions import CapabilityDescriptions, descriptions_hash, load_capability_descriptions
from ..records import CapabilityMapping, Posting, ProjectChoice, Reading, Requirement
from . import capability_mapping, jev_choice, jev_mapping, project_choice, requirements, store
from .fetch import fetch_posting, make_posting
from .requirements import RequirementReading

Reader = Callable[[str], RequirementReading]
Mapper = Callable[[str, list[Requirement], dict[str, str]], CapabilityMapping]
Chooser = Callable[[str, str, Library], ProjectChoice]

# Which model answers calls 2 and 3 (`settings.CLASSIFIER` and `settings.CALL2`): TypeSafe's Jev, with Opus as
# its fallback, or Opus alone. The dashboard and the engine's command line choose; `read_posting` and
# `project_choice_for` still default to Opus, so a caller that names no mapper or chooser never reaches a network
# service by surprise.


def call2_model(classifier: str, call2: str | None = None) -> str:
    """The model that answers call 2: Opus whenever `classifier` is Opus, else `call2`, by default `settings.CALL2`."""
    return "opus" if classifier == "opus" else call2 or settings.CALL2


def call2_mapper(classifier: str) -> Mapper:
    """Call 2 answered by `classifier`, looked up on its module when called, so tests and the dev server stand in."""
    if classifier == "jev":
        return lambda title, found, vocabulary: jev_mapping.map_requirements(title, found, vocabulary)
    if classifier == "opus":
        return lambda title, found, vocabulary: capability_mapping.map_requirements(title, found, vocabulary)
    raise ValueError(_unknown(classifier))


def call3_chooser(classifier: str) -> Chooser:
    """Call 3 answered by `classifier`, looked up on its module when called, as `call2_mapper`."""
    if classifier == "jev":
        return lambda title, text, library: jev_choice.choose_projects(title, text, library)
    if classifier == "opus":
        return lambda title, text, library: project_choice.choose_projects(title, text, library)
    raise ValueError(_unknown(classifier))


def _unknown(classifier: str) -> str:
    return f"calls 2 and 3 are answered by one of {', '.join(settings.CLASSIFIERS)}, not {classifier!r}"


class ReadResult(TypedDict):
    reading: Reading
    mapping: CapabilityMapping
    calls: list[str]  # the model calls made: "requirements", "mapping", or none


def posting_from_url(url: str) -> Posting:
    return fetch_posting(url)


def posting_from_text(text: str, title: str = "", company: str = "") -> Posting:
    """A pasted posting, tidied exactly as a fetched one is: HTML entities decoded, spacing normalised."""
    return make_posting(text, "paste", url=None, title=title, company=company, board={})


def read_posting(
    posting: Posting,
    *,
    refresh: bool = False,
    vocabulary: dict[str, str] | None = None,
    descriptions: CapabilityDescriptions | None = None,
    readings: Path | None = None,
    mappings: Path | None = None,
    reader: Reader | None = None,
    mapper: Mapper | None = None,
) -> ReadResult:
    """{"reading", "mapping", "calls"}; `calls` names the model calls made ([] when both were saved).

    `vocabulary` is the capability vocabulary call 2 maps onto, by default the library's as saved; `descriptions` are
    the capability descriptions a saved Jev mapping must have been asked with to stay current, by default the
    library's, and are given with a vocabulary that is not the library's. `reader(text)` and
    `mapper(title, requirements, vocabulary)` stand in for the two model calls.
    """
    text = posting["text"]
    calls: list[str] = []
    reading = None if refresh else store.load_reading(text, posting_id=posting["id"], directory=readings)
    if reading is None:
        found, keywords, info = (reader or requirements.read_requirements)(text)
        store.save_reading(posting["id"], text, found, keywords, info=info, directory=readings)
        # Read back from disk, so this page is built from exactly what every later run will load.
        reading = store.load_reading(text, posting_id=posting["id"], directory=readings)
        if reading is None:
            raise RuntimeError(f"the reading just saved for {posting['id']} cannot be read back")
        calls.append("requirements")
    vocabulary = vocabulary or load_capability_vocabulary(settings.LIBRARY)
    if descriptions is None:
        descriptions = load_capability_descriptions(settings.LIBRARY, vocabulary)
    # A saved mapping is found by the posting's text alone, so after a reading is made again (its file deleted by
    # hand) it would still be found, answering the old reading's requirement names.
    reading_made = "requirements" in calls
    mapping = None if refresh or reading_made else store.load_mapping(text, directory=mappings)
    if mapping is None or store.mapping_needs_call(
        mapping, vocabulary_hash(vocabulary), descriptions_hash(descriptions)
    ):
        requirements_to_map: list[Requirement] = [
            {"name": item["name"], "tokens": item["tokens"], "required": item["required"]}
            for item in reading["requirements"]
        ]
        mapping = (mapper or capability_mapping.map_requirements)(
            posting.get("title") or "", requirements_to_map, vocabulary
        )
        mapping["posting"] = posting["id"]
        store.save_mapping(text, mapping, directory=mappings)
        calls.append("mapping")
    return {"reading": reading, "mapping": mapping, "calls": calls}


def project_choice_for(
    posting: Posting,
    library: Library,
    *,
    refresh: bool = False,
    choices: Path | None = None,
    chooser: Chooser | None = None,
) -> tuple[ProjectChoice, bool]:
    """(the saved call 3 answer for this posting, whether a model call made it now).

    A call is made only when no usable answer was saved from the current projects, or when asked for again.
    `chooser(title, text, library)` stands in for the call; by default `project_choice.choose_projects`.
    """
    text = posting["text"]
    saved = None if refresh else store.load_choice(text, choices)
    if saved is not None and store.choice_is_current(saved, project_choice.projects_hash(library)):
        return saved, False
    choice = (chooser or project_choice.choose_projects)(posting.get("title") or "", text, library)
    choice["posting"] = posting["id"]
    store.save_choice(text, choice, choices)
    return choice, True
