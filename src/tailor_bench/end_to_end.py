"""One posting from its URL to a judged page, every step timed.

  fetch        the posting and its board facts (one request)
  facts        salary, level, sponsorship... read by rule
  read         model call 1 (requirements) and call 2 (capabilities), each only if not saved already; calls 2 and 3
               answered as `mapper` and `chooser` give them, by default as the classifier and call 2 settings say
  engine page  selection over every project, to see whether the engine's match is low
  call 3       the model's projects, only when that match is under `model_projects_below` and none is saved already
  page         the hybrid page, as the dashboard builds it by default
  document     skill rows, courses and projects as the page will print
  judge        the judge model on the page (optional)

The posting, its reading, mapping and project choice are saved in the run's data folder (by default
benchmark/runs/<label>/data/), never in the product's store (data/). The run's record goes to benchmark/runs/<label>/:
facts.json, page.json, page.txt, judge.json, timings.json.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from tailor_engine import settings
from tailor_engine.library import Library
from tailor_engine.reading import pipeline, store
from tailor_engine.reading.facts import posting_facts
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.reading.requirements import RequirementReading, read_requirements
from tailor_engine.records import CapabilityMapping, ProjectChoice, Requirement
from tailor_engine.rendering.document import ResumeDocument, document_from_page
from tailor_engine.rendering.judge_input import page_of
from tailor_engine.selection.page import build_hybrid_page, build_page
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

from .dataset import FACTS_FILE, JUDGE_FILE, PAGE_FILE, PAGE_TEXT_FILE, RUNS, TIMINGS_FILE, run_data
from .judge import judge_page

T = TypeVar("T")


def page_text(document: ResumeDocument) -> str:
    """The document as plain text, for a person to read beside the Word file."""
    lines = ["PROJECTS"]
    for project in document.projects:
        lines.append(f"  {project.title}")
        lines += [f"    - {bullet}" for bullet in project.bullets]
    lines.append("SKILLS")
    lines += [f"  {row.title}: {', '.join(row.members)}" for row in document.skill_rows]
    lines.append("COURSES")
    lines += [f"  {entry.degree_id}: {', '.join(entry.courses)}" for entry in document.coursework]
    return "\n".join(lines)


def run(
    url: str,
    label: str,
    with_judge: bool = True,
    data: Path | None = None,
    *,
    mapper: pipeline.Mapper | None = None,
    chooser: pipeline.Chooser | None = None,
) -> Path:
    """The run's folder under benchmark/runs/, with its record written there.

    `data` holds the posting and its saved records, by default the run folder's data/. Each record is written as its
    step finishes, and the timings always are, so a run that stops part-way keeps what it measured. `mapper` and
    `chooser` make calls 2 and 3, by default as `settings.CLASSIFIER` and `settings.CALL2` have them answered.
    """
    run_folder = RUNS / label
    data = data or run_data(label)
    run_folder.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []

    def save(name: str, data: object) -> None:
        (run_folder / name).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")

    def timed(name: str, action: Callable[[], T], **details: object) -> T:
        started = time.perf_counter()
        value = action()
        steps.append({"step": name, "seconds": round(time.perf_counter() - started, 2), **details})
        print(f"  {name:36s} {steps[-1]['seconds']:7.2f} s", flush=True)
        return value

    usage: dict[str, Mapping[str, Any]] = {}

    def reader(text: str) -> RequirementReading:
        reading = read_requirements(text)
        usage["requirements"] = reading.info
        return reading

    def mapped(title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
        call2 = mapper or pipeline.call2_mapper(pipeline.call2_model(settings.CLASSIFIER))
        mapping_record = call2(title, requirements, vocabulary)
        usage["mapping"] = mapping_record
        return mapping_record

    try:
        posting = timed("fetch and board facts", lambda: pipeline.posting_from_url(url))
        store.save_posting(posting, data / "postings")
        save(FACTS_FILE, timed("posting facts", lambda: posting_facts(posting)))
        library = Library.load()
        read_result = timed(
            "read (model calls 1 and 2)",
            lambda: pipeline.read_posting(
                posting, readings=data / "readings", mappings=data / "mappings", reader=reader, mapper=mapped
            ),
        )
        steps[-1]["calls"] = read_result["calls"]
        steps += _call_steps(usage)
        reading, mapping = read_result["reading"], read_result["mapping"]["requirements"]
        engine = timed("engine page", lambda: build_page(posting, reading, library, mapping))
        choice: ProjectChoice | None = None
        if engine["coverage"] < DEFAULT_WEIGHTS.model_projects_below:
            choice, made = timed(
                "call 3: projects",
                lambda: pipeline.project_choice_for(
                    posting,
                    library,
                    choices=data / "choices",
                    chooser=chooser or pipeline.call3_chooser(settings.CLASSIFIER),
                ),
            )
            _note_call_3(steps[-1], choice, made)
        usable = choice if store.choice_is_current(choice, projects_hash(library)) else None
        result = timed("page (hybrid)", lambda: build_hybrid_page(posting, reading, library, mapping, usable))
        save(PAGE_FILE, result)
        document = timed("document", lambda: document_from_page(result))
        if with_judge:
            judged = timed("judge", lambda: judge_page(posting, page_of(result), library))
            steps[-1].update({"input_tokens": judged["input_tokens"], "output_tokens": judged["output_tokens"]})
            save(JUDGE_FILE, judged)
            print(f"  judge answer valid: {judged['valid']}")
        (run_folder / PAGE_TEXT_FILE).write_text(page_text(document), encoding="utf-8")
    except Exception as error:
        steps.append({"step": "stopped", "error": f"{type(error).__name__}: {error}"})
        raise
    finally:
        save(TIMINGS_FILE, steps)
    return run_folder


def _call_steps(usage: dict[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A step for each of calls 1 and 2 made in this run, with its own time and tokens, printed as it is added."""
    steps: list[dict[str, Any]] = []
    for usage_key, name in (("requirements", "call 1: requirements"), ("mapping", "call 2: capabilities")):
        if usage_key in usage:
            steps.append(
                {
                    "step": name,
                    "seconds": usage[usage_key]["seconds"],
                    "input_tokens": usage[usage_key]["input_tokens"],
                    "output_tokens": usage[usage_key]["output_tokens"],
                }
            )
            print(
                f"    {name:34s} {usage[usage_key]['seconds']:7.2f} s  "
                f"{usage[usage_key]['input_tokens']} in / {usage[usage_key]['output_tokens']} out"
            )
    return steps


def _note_call_3(step: dict[str, Any], choice: ProjectChoice, made: bool) -> None:
    """Add call 3's tokens to its step when the call was made in this run, and say when its answer cannot be used."""
    if made:
        step.update({"input_tokens": choice.get("input_tokens"), "output_tokens": choice.get("output_tokens")})
    if not choice.get("valid"):
        # Saved all the same, as the engine's `read --hybrid` does; the page is then the engine's.
        print("  call 3's answer cannot be used: " + "; ".join(choice.get("errors") or []))
