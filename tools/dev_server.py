"""The dashboard on a copy of the example data, with every model call stood in, for checking the page in a browser.

  python tools/dev_server.py [--fresh] [--copy NAME] [--port 8771]

The copy (`local/dev-data/`, not in git; `--fresh` makes it again) holds the postings, readings, mappings and project
choices of `examples/data/`. Two postings' readings and mappings are held back, so they show unread; reading one returns
its held records after a few seconds (call 1 about 4 s, call 2 about 0.5 s as Jev and 5 s as Opus). Call 3 answers the
first six projects of the list after 0.5 s as Jev, or the first five after 5 s as Opus. The judge answers a fixed
stand-in after 4 s, with one page edit that fits (the privacy check on what is sent still runs, against a fingerprints
file of the copy's own). A pasted posting fails its stand-in call 1 on purpose, since no reading is held for it. Nothing
outside the copy is written, no model is called, no Jev request is sent and no key is read. TAILOR_CLASSIFIER=opus runs
it with Opus as the classifier.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from typing import Any
from unittest import mock

import uvicorn

import tailor_dashboard.review as review_module
import tailor_engine.reading.capability_mapping as mapping_module
import tailor_engine.reading.jev_choice as jev_choice_module
import tailor_engine.reading.jev_mapping as jev_mapping_module
import tailor_engine.reading.project_choice as choice_module
import tailor_engine.reading.requirements as requirements_module
from tailor_dashboard.edits import edit_problem
from tailor_dashboard.jobs import Paths
from tailor_dashboard.server import create_app
from tailor_engine import privacy, settings
from tailor_engine.library import Library
from tailor_engine.library.capability_descriptions import CapabilityDescriptions, descriptions_hash
from tailor_engine.reading import jev, store
from tailor_engine.reading.model_call import Effort, JsonAnswer
from tailor_engine.records import CapabilityMapping, ProjectChoice, Requirement

ROOT = settings.REPOSITORY_ROOT
EXAMPLES = ROOT / "examples"
LIBRARY = EXAMPLES / "library"
DEV = ROOT / "local" / "dev-data"
HELD = DEV / "held"
# Two example postings held back, so they show unread: cloud security (X5) and the backend role (X6).
UNREAD = ("paste-afdfe55e", "paste-12648881")
# The privacy guard's word for the copy: the example candidate's handle, which only the template holds.
GUARD_WORD = "rkestrel"


def make_copy() -> None:
    for kind in ("postings", "readings", "mappings", "choices"):
        (DEV / kind).mkdir(parents=True)
        for path in (EXAMPLES / "data" / kind).glob("*.json"):
            shutil.copy2(path, DEV / kind / path.name)
    (DEV / "identity-fingerprints.txt").write_text(privacy.fingerprint(GUARD_WORD) + "\n", encoding="utf-8")
    for kind in ("readings", "mappings"):
        (HELD / kind).mkdir(parents=True)
    for posting_id in UNREAD:
        posting = store.load_posting(posting_id, DEV / "postings")
        if posting is None:
            raise SystemExit(f"{posting_id} is not among the saved postings")
        key = store.text_key(posting["text"])
        for path in (DEV / "readings").glob(f"*-{key}.json"):
            shutil.move(path, HELD / "readings" / path.name)
        shutil.move(DEV / "mappings" / f"{key}.json", HELD / "mappings" / f"{key}.json")


def fake_read(text: str, call: object = None) -> Any:
    time.sleep(4)
    reading = store.load_reading(text, directory=HELD / "readings")
    if reading is None:
        raise RuntimeError("stand-in call 1: no held reading for this text (expected for a pasted posting)")
    return reading["requirements"], reading["keywords"], reading.get("info") or {}


def held_mapping(requirements: list[Requirement]) -> CapabilityMapping:
    names = {entry["name"] for entry in requirements}
    for path in (HELD / "mappings").glob("*.json"):
        record: CapabilityMapping = json.loads(path.read_text(encoding="utf-8"))
        if {entry["name"] for entry in record["requirements"]} == names:
            return record
    raise RuntimeError("stand-in call 2: no held mapping answers these requirements")


def fake_map(
    title: str, requirements: list[Requirement], vocabulary: dict[str, str], call: object = None
) -> CapabilityMapping:
    time.sleep(5)
    return held_mapping(requirements)


def fake_jev_map(
    title: str,
    requirements: list[Requirement],
    vocabulary: dict[str, str],
    *,
    descriptions: CapabilityDescriptions,
    **options: object,
) -> CapabilityMapping:
    time.sleep(0.5)
    # Made with the library's capability descriptions, as Jev's own record says, so it stays current.
    return {
        **held_mapping(requirements),
        "seconds": 0.5,
        "model": jev.MODEL,
        "answered_by": "jev-stand-in",
        "descriptions_sha256_16": descriptions_hash(descriptions),
    }


def fake_map_many(
    postings: list[tuple[str, list[Requirement]]], vocabulary: dict[str, str], call: object = None
) -> list[CapabilityMapping]:
    return [fake_map(title, requirements, vocabulary) for title, requirements in postings]


def fake_choose(title: str, text: str, library: Library, call: object = None) -> ProjectChoice:
    time.sleep(5)
    return {
        "projects": choice_module.listed_projects(library)[:5],
        "why": "A stand-in choice from the dev server; no model was called.",
        "valid": True,
        "seconds": 5.0,
        "projects_sha256_16": choice_module.projects_hash(library),
        "format": store.CHOICE_FORMAT,
    }


def fake_jev_choose(title: str, text: str, library: Library, **options: object) -> ProjectChoice:
    time.sleep(0.5)
    return {
        "projects": choice_module.listed_projects(library)[:6],
        "why": "A stand-in choice from the dev server; no Jev request was sent.",
        "valid": True,
        "seconds": 0.5,
        "projects_sha256_16": choice_module.projects_hash(library),
        "format": store.CHOICE_FORMAT,
        "model": jev.MODEL,
        "answered_by": "jev-stand-in",
    }


def fitting_swap(user_prompt: str) -> list[dict[str, Any]]:
    """One bullet off the page in the prompt and one fitting bullet on, as a real judge might propose."""
    page_text = user_prompt.split("THE PAGE:")[1].split("EACH REQUIREMENT")[0]
    on_page = re.findall(r"\[([A-Za-z0-9]+\.[0-9]+)\]", page_text)
    library = Library.load(LIBRARY)
    for removed in reversed(on_page):
        for bullet in library.usable_bullets:
            if bullet.id not in on_page and edit_problem(library, on_page, [removed], [bullet.id]) is None:
                return [{"remove": [removed], "add": [bullet.id], "why": "A stand-in proposal; no model was called."}]
    return []


def fake_judge(
    model: str, system_prompt: str, user_prompt: str, effort: Effort | None = None, timeout: int = 600
) -> JsonAnswer:
    time.sleep(4)
    detailed = "LENGTH: detailed" in user_prompt
    gaps = [
        {"gap": "Stand-in gap one", "note": "A stand-in note; no model was called."},
        {"gap": "Stand-in gap two", "note": "Another stand-in note."},
    ]
    return {
        "json": {
            "verdict": "moderate",
            "summary": "A stand-in answer from the dev server; no model was called.",
            "relevance": "Stand-in relevance text." + (" A longer detailed paragraph." * 3 if detailed else ""),
            "alignment": "Stand-in alignment text, naming [P.1] as an example.",
            "gaps": gaps * (2 if detailed else 1),
            "better_fits": [{"role": "Stand-in Role", "why": "A stand-in reason."}],
            "edits": fitting_swap(user_prompt),
        },
        "input_tokens": 9000,
        "output_tokens": 600 if detailed else 250,
        "seconds": 4.0,
        "model": model,
        "effort": effort,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="make the copy of the data again")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--copy", default="dev-data", help="the copy's folder under local/ (default dev-data)")
    arguments = parser.parse_args()
    global DEV, HELD  # noqa: PLW0603  the copy's folder, chosen once before anything reads it
    DEV = ROOT / "local" / arguments.copy
    HELD = DEV / "held"
    if arguments.fresh and DEV.exists():
        shutil.rmtree(DEV)
    if not DEV.exists():
        make_copy()
    # The dashboard looks the model calls up on these modules when it makes them, so the stand-ins take their place.
    with (
        mock.patch.object(requirements_module, "read_requirements", fake_read),
        mock.patch.object(mapping_module, "map_requirements", fake_map),
        mock.patch.object(mapping_module, "map_many", fake_map_many),
        mock.patch.object(choice_module, "choose_projects", fake_choose),
        mock.patch.object(jev_mapping_module, "ask", fake_jev_map),
        mock.patch.object(jev_choice_module, "ask", fake_jev_choose),
        mock.patch.object(review_module, "call_json", fake_judge),
        mock.patch.object(privacy, "FINGERPRINTS_FILE", DEV / "identity-fingerprints.txt"),
    ):
        print(
            f"dev dashboard on http://127.0.0.1:{arguments.port}/ (a copy of the data, stand-in model calls)",
            flush=True,
        )
        app = create_app(Paths.under(DEV, LIBRARY))
        uvicorn.run(app, host="127.0.0.1", port=arguments.port, log_level="warning", timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
