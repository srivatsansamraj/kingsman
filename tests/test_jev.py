"""Calls 2 and 3 answered by TypeSafe's Jev, offline: no request leaves the machine and no key is read.

The requests' bodies: call 2's choices the tuned request byte for byte, five requirements a request, and every word's
question as measured in one more request (none when there are no words), all sent at once; the model pinned; how
answers become a mapping ("none of these" first gives no capability) and a choice of six projects; the fallback to Opus
for each kind of failure (no key, any error from the credential store, its Windows error number named, a 4xx, a 5xx or a
timeout tried once more, an answer that does not fit, a probability outside 0 to 1), for the whole posting when any one
of its requests fails, the word request included, and the retry made exactly once; an HTTP 429 asked again after its
wait, at most twice; the saved records' fields, every version that answered named once, the posting's own time rather
than its requests' sum, Opus's own refusal category kept, the capability descriptions' hash on call 2's; at most six
requests in flight across postings read at once, or the caller's own cap; the key kept out of every record, error,
traceback and log, and a malformed one stripped or refused; the privacy guard before either call's request, reading
call 3's project list, once, and not its posting; the classifier and call 2 settings. A guard fails any real Jev request
or credential-store read; one test sends malformed requests to a socket on this machine.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import runpy
import socket
import sys
import tempfile
import threading
import time
import traceback
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import httpx
import keyring
import keyring.errors

from tailor_engine import cli, privacy, settings
from tailor_engine.library import Library
from tailor_engine.library.capabilities import vocabulary_hash
from tailor_engine.library.capability_descriptions import NONE, CapabilityDescriptions, descriptions_hash
from tailor_engine.library.projects import ProjectProfile
from tailor_engine.reading import capability_mapping, jev, jev_choice, jev_mapping, pipeline, project_choice, store
from tailor_engine.reading.jev import JevUnavailableError, Post
from tailor_engine.records import CapabilityMapping, ProjectChoice, Requirement

HTTPX_POST = httpx.post

VOCABULARY = {"threat modelling": "Application and platform security", "penetration testing": "Offensive security"}
REQUIREMENTS: list[Requirement] = [
    {"name": "Threat modelling", "tokens": ["threat modelling", "STRIDE"], "required": True},
    {"name": "Python", "tokens": ["Python"], "required": False},
]
DESCRIPTIONS: CapabilityDescriptions = {
    "threat modelling": {
        "domain": "Application and platform security",
        "what": "finding how a design can be attacked before it is built",
        "not_for": "penetration testing (attacking a running system)",
        "examples": ["Threat modelling with STRIDE"],
    },
    "penetration testing": {
        "domain": "Offensive security",
        "what": "attacking a running system to find its flaws",
        "not_for": "threat modelling (finding how a design can be attacked)",
        "examples": ["Web application penetration testing", "Hands-on with Burp Suite"],
    },
    NONE: {"what": "a condition only", "not_for": "an ability named with its tool", "examples": ["5+ years"]},
}
# Call 2's Jev request for example posting X1, from its saved reading and the example library's capability
# descriptions, recorded with `jev_mapping.request_bodies`: any change to the request fails here.
TUNED_REQUEST = Path(__file__).parent / "fixtures" / "jev-call2-request.json"
KEY = "sk-test-KEEP-OUT-0000"
PROJECTS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8")
# Call 2's question as tuned, written out so any drift from it fails here.
TUNED_QUESTION = "Which capability from the list does `requirements.R1` ask the candidate to be able to do?"
TUNED_FOCUS = (
    "Judge what the requirement asks the candidate to do as the posting means it; a capability whose name shares a "
    "word with the requirement is not enough."
)
# Call 2's word question as measured (the "separate judgments" variant), written out for the same reason.
MEASURED_WORD_QUESTION = (
    "Is `requirements.R0.words[1]` the name of a specific language, tool, platform, framework, standard or protocol, "
    "certification, degree or field of study, clearance, or an amount of experience that `requirements.R0` names, "
    "alone or as one of several examples?"
)
MEASURED_WORD_CRITERIA = {
    "true": {
        "what": "a named language, tool, platform, framework, standard or protocol, certification, degree or field of "
        "study, clearance, or amount of experience",
        "examples": ["AWS", "YARA", "OWASP Top 10", "OSCP", "5+ years", "Top Secret clearance"],
    },
    "false": {
        "what": "a kind of work, practice or skill",
        "examples": ["penetration testing", "threat modelling", "security research"],
    },
}
# Call 3's question as measured, written out so any drift from it fails here.
MEASURED_PURPOSE = (
    "A one-page resume for this job posting shows 4 to 6 of the candidate's projects: the ones that make the "
    "strongest case for shortlisting the candidate for this specific role, judged by relevance to what the role "
    "is about and by the strength and credibility of the evidence."
)
MEASURED_CRITERIA = {
    "true": "Among the strongest evidence for this role; it belongs on the page.",
    "false": "Other projects make a stronger case for this role; leave it off.",
}


def library(summary: str = "Built and measured a detector") -> Library:
    """A stand-in with what call 3 reads: the usable bullets' projects and each project's profile."""
    profiles = {
        project_id: ProjectProfile(f"Project {project_id}", "shipped", "2026-05", "", (), summary)
        for project_id in PROJECTS
    }
    bullets = [SimpleNamespace(parent=project_id) for project_id in PROJECTS]
    return cast(Library, SimpleNamespace(usable_bullets=bullets, profiles=profiles))


def answers_to(
    body: dict[str, Any], noul: Callable[[str], float] = lambda name: 0.2, model: str = "jev-1.13.0"
) -> dict[str, Any]:
    """TypeSafe's answer to every question in `body`: each choice's first option most likely, each yes-or-no `noul`."""
    answers: dict[str, Any] = {}
    for name, question in body["questions"].items():
        if question["type"] == "choice":
            options = list(question["criteria"])
            chances = [0.7, 0.2] + [0.1 / max(len(options) - 2, 1)] * (len(options) - 2)
            answers[name] = {"type": "choice", "probabilities": dict(zip(options, chances, strict=True))}
        else:
            answers[name] = {"type": "noul", "noul": noul(name)}
    return {"answers": answers, "model": model, "usage": {"input_tokens": 900, "output_tokens": 40}}


# The question that marks each of call 2's two kinds of request for REQUIREMENTS: its choices, and its words.
CHOICES, WORDS = "R0|capability", "R0|word0"


class StandIn:
    """Jev at the request: each request takes the next outcome (an error to raise, (status, body), or ANSWER).

    With `asking`, only the requests holding that question take the outcomes and every other one is answered: a
    posting's call 2 sends its requests at once, in no set order.
    """

    ANSWER = "answer"

    def __init__(
        self, *outcomes: Any, noul: Callable[[str], float] = lambda name: 0.2, asking: str | None = None
    ) -> None:
        self.outcomes = list(outcomes)
        self.noul = noul
        self.asking = asking
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        with self._lock:
            self.requests.append((url, body, headers))
            takes = self.asking is None or self.asking in body["questions"]
            outcome = self.outcomes.pop(0) if takes and self.outcomes else self.ANSWER
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == self.ANSWER:
            return 200, answers_to(body, self.noul)
        status, data = cast(tuple[int, Any], outcome)
        return status, data

    def count(self, question: str) -> int:
        """The requests made that hold `question`, retries included."""
        return sum(question in body["questions"] for _url, body, _headers in self.requests)


def httpx_stand_in(*outcomes: Any, asking: str = CHOICES) -> mock.Mock:
    """`httpx.post` stood in, recording each call.

    The requests holding `asking` take each outcome in turn (a response, or an error to raise); every other request,
    and each once the outcomes are spent, is answered whole.
    """
    left = list(outcomes)
    lock = threading.Lock()

    def post(url: str, **options: Any) -> httpx.Response:
        body = options["json"]
        with lock:
            outcome = left.pop(0) if asking in body["questions"] and left else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, httpx.Response) else httpx.Response(200, json=answers_to(body))

    return mock.Mock(side_effect=post)


def write_descriptions(folder: Path, descriptions: CapabilityDescriptions) -> None:
    """A library's capability-descriptions.toml holding `descriptions`, in their order."""
    # A JSON string or list of strings is also TOML's.
    written = "".join(
        f"[{json.dumps(name)}]\n" + "".join(f"{field} = {json.dumps(value)}\n" for field, value in entry.items())
        for name, entry in descriptions.items()
    )
    (folder / "capability-descriptions.toml").write_text(written, encoding="utf-8")


def requirements_with_a_word(count: int) -> list[Requirement]:
    """`count` requirements of one word each: `count` over five choice requests, and the word request."""
    return [{"name": f"Requirement {number}", "tokens": ["Python"], "required": True} for number in range(count)]


def opus_mapping(title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
    """Opus's call 2 stood in: its record as `capability_mapping.map_requirements` saves it."""
    return {
        "requirements": [{**item, "capabilities": [], "capability": None, "conditions": []} for item in requirements],
        "valid": True,
        "errors": [],
        "first_errors": ["an answer asked for again"],
        "attempts": 2,
        "seconds": 8.0,
        "vocabulary_sha256_16": vocabulary_hash(vocabulary),
        "format": store.MAPPING_FORMAT,
        "model": "claude-opus-5-5",
        "answered_by": "claude-opus-5-5",
        "fallback": None,
    }


def opus_choice(title: str, text: str, listed: Library) -> ProjectChoice:
    return {
        "projects": ["P2", "P1", "P3", "P4"],
        "why": "Closest to the role.",
        "valid": True,
        "seconds": 5.5,
        "projects_sha256_16": project_choice.projects_hash(listed),
        "format": store.CHOICE_FORMAT,
        "model": "claude-opus-5",
        "answered_by": "claude-opus-5",
        "fallback": None,
    }


class JevTestCase(unittest.TestCase):
    """No real request, no credential-store read, and a fingerprints file of its own for the privacy guard."""

    def setUp(self) -> None:
        for target, name in ((httpx, "post"), (keyring, "get_password")):
            guard = mock.patch.object(target, name, mock.Mock(side_effect=AssertionError(f"a real {name}")))
            reached: mock.Mock = guard.start()
            self.addCleanup(guard.stop)
            # Checked afterwards too: any error in a request becomes a fallback, so the guard's own error is not seen.
            self.addCleanup(self.assert_not_reached, reached, name)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        fingerprints = Path(folder.name) / "identity-fingerprints.txt"
        fingerprints.write_text(privacy.fingerprint("secretname") + "\n", encoding="utf-8")
        patch = mock.patch.object(privacy, "FINGERPRINTS_FILE", fingerprints)
        patch.start()
        self.addCleanup(patch.stop)

    def assert_not_reached(self, guard: mock.Mock, name: str) -> None:
        self.assertFalse(guard.called, f"a real {name} was attempted")

    def map_with(self, stand_in: Post, opus: Any = opus_mapping) -> CapabilityMapping:
        return jev_mapping.map_requirements(
            "Security Engineer",
            REQUIREMENTS,
            VOCABULARY,
            descriptions=DESCRIPTIONS,
            post=stand_in,
            key=lambda: KEY,
            fallback=opus,
        )


def tuned_inputs() -> tuple[list[dict[str, Any]], str, list[Requirement], dict[str, str], CapabilityDescriptions]:
    """The recorded request's bodies, and the inputs they were built from.

    The posting's title and requirements, the vocabulary, and the capabilities as the library describes them.
    """
    tuned: list[dict[str, Any]] = json.loads(TUNED_REQUEST.read_text(encoding="utf-8"))["bodies"]
    state = tuned[0]["state"]
    requirements: list[Requirement] = [
        {"name": item["name"], "tokens": item["words"], "required": item["required"]}
        for item in state["requirements"].values()
    ]
    described = tuned[0]["questions"]["R0|capability"]["criteria"]
    vocabulary = {name: entry["domain"] for name, entry in described.items() if name != NONE}
    return tuned, state["role"], requirements, vocabulary, described


class Requests(JevTestCase):
    def test_call_2_asks_a_described_choice_per_requirement_then_each_word_in_one_more_request(self) -> None:
        choices, words = jev_mapping.request_bodies("Security Engineer", REQUIREMENTS, VOCABULARY, DESCRIPTIONS)
        state = {
            "role": "Security Engineer",
            "requirements": {
                "R0": {"name": "Threat modelling", "words": ["threat modelling", "STRIDE"], "required": True},
                "R1": {"name": "Python", "words": ["Python"], "required": False},
            },
        }
        self.assertEqual([(body["model"], body["state"]) for body in (choices, words)], [("jev-1.13.0", state)] * 2)
        self.assertEqual(list(choices["questions"]), ["R0|capability", "R1|capability"])
        # Each capability as the library describes it, in the vocabulary's order, whatever the descriptions' order.
        self.assertEqual(
            choices["questions"]["R1|capability"],
            {
                "type": "choice",
                "instructions": {"question": TUNED_QUESTION, "focus": TUNED_FOCUS},
                "criteria": dict(DESCRIPTIONS),
            },
        )
        backwards = dict(reversed(DESCRIPTIONS.items()))
        again, _words = jev_mapping.request_bodies("Security Engineer", REQUIREMENTS, VOCABULARY, backwards)
        self.assertEqual(list(again["questions"]["R1|capability"]["criteria"]), [*VOCABULARY, NONE])
        self.assertEqual(list(words["questions"]), ["R0|word0", "R0|word1", "R1|word0"])
        self.assertEqual(
            words["questions"]["R0|word1"],
            {"type": "noul", "instructions": MEASURED_WORD_QUESTION, "criteria": MEASURED_WORD_CRITERIA},
        )

    def test_call_2_s_choices_are_the_tuned_request_byte_for_byte(self) -> None:
        # The recorded request's own inputs: its choice requests byte for byte, the word request apart (below).
        tuned, role, requirements, vocabulary, described = tuned_inputs()
        self.assertEqual(
            (len(requirements), len(vocabulary), {body["model"] for body in tuned}),
            (18, 76, {jev.MODEL}),
        )
        *choices, _words = jev_mapping.request_bodies(role, requirements, vocabulary, described)
        self.assertEqual(json.dumps(choices), json.dumps(tuned[:-1]))

    def test_call_2_asks_every_word_of_every_requirement_in_one_request_with_the_whole_state(self) -> None:
        tuned, role, requirements, vocabulary, described = tuned_inputs()
        *choices, words = jev_mapping.request_bodies(role, requirements, vocabulary, described)
        self.assertEqual(json.dumps(words["state"]), json.dumps(tuned[0]["state"]))
        # Each word of each requirement in order, as the recorded request named them, each the measured question and
        # criteria.
        named = [name for body in tuned for name in body["questions"] if "|word" in name]
        self.assertEqual(list(words["questions"]), named)
        self.assertEqual(len(named), sum(len(item["tokens"]) for item in requirements))
        for name, question in words["questions"].items():
            rid, number = name.split("|word")
            self.assertEqual(
                question,
                {
                    "type": "noul",
                    "instructions": jev_mapping.WORD_QUESTION.format(rid=rid, n=int(number)),
                    "criteria": jev_mapping.WORD_CRITERIA,
                },
            )
        self.assertEqual(
            (jev_mapping.WORD_QUESTION.format(rid="R0", n=1), jev_mapping.WORD_CRITERIA),
            (MEASURED_WORD_QUESTION, MEASURED_WORD_CRITERIA),
        )
        self.assertEqual([name for body in choices for name in body["questions"] if "|word" in name], [])

    def test_call_2_asks_five_requirements_a_request_each_holding_the_whole_state(self) -> None:
        many: list[Requirement] = [
            {"name": f"Requirement {number}", "tokens": [f"word {number}"] * (number % 3), "required": True}
            for number in range(11)
        ]
        *choices, words = jev_mapping.request_bodies("Role", many, VOCABULARY, DESCRIPTIONS)
        chosen = [list(body["questions"]) for body in choices]
        self.assertEqual([len(names) for names in chosen], [5, 5, 1])
        self.assertEqual(chosen[1], [f"R{number}|capability" for number in range(5, 10)])
        self.assertEqual(chosen[2], ["R10|capability"])
        self.assertEqual({len(body["state"]["requirements"]) for body in [*choices, words]}, {11})
        # One more request asks each requirement's words; a requirement with none has no question there.
        self.assertEqual(list(words["questions"])[:3], ["R1|word0", "R2|word0", "R2|word1"])
        self.assertEqual(len(words["questions"]), sum(number % 3 for number in range(11)))
        # With no word at all, there is no word request.
        wordless: list[Requirement] = [{**item, "tokens": []} for item in many]
        bodies = jev_mapping.request_bodies("Role", wordless, VOCABULARY, DESCRIPTIONS)
        self.assertEqual([len(body["questions"]) for body in bodies], [5, 5, 1])

    def test_call_3_holds_the_posting_and_the_listed_projects_and_asks_the_measured_question(self) -> None:
        posting = "Detection engineer. " + "x" * 9000
        body = jev_choice.request_body("Detection Engineer", posting, jev_choice.candidate_projects(library()))
        state = body["state"]
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(state["job_posting"], {"title": "Detection Engineer", "text": posting[:7000]})
        self.assertEqual(list(state["candidate_projects"]), list(PROJECTS))
        self.assertEqual(
            state["candidate_projects"]["P3"],
            {
                "title": "Project P3",
                "what it is": "Built and measured a detector",
                "status": "shipped",
                "ended": "2026-05",
            },
        )
        self.assertEqual(list(body["questions"]), list(PROJECTS))
        self.assertEqual(
            body["questions"]["P3"],
            {
                "type": "noul",
                "instructions": f"{MEASURED_PURPOSE} Is the project `candidate_projects.P3` one of those 4 to 6 "
                "projects for `job_posting`?",
                "criteria": MEASURED_CRITERIA,
            },
        )


class Answers(JevTestCase):
    def test_the_first_choice_is_kept_and_alternatives_at_the_threshold(self) -> None:
        answers = {
            "R0|capability": {
                "probabilities": {"threat modelling": 0.5, "penetration testing": 0.3, "none of these": 0.2}
            },
            "R0|word0": {"noul": 0.2},
            "R0|word1": {"noul": 0.5},
            "R1|capability": {
                "probabilities": {"none of these": 0.6, "threat modelling": 0.29, "penetration testing": 0.11}
            },
            "R1|word0": {"noul": 0.9},
        }
        found = jev_mapping.mapped(answers, REQUIREMENTS, VOCABULARY)
        self.assertEqual(
            [(item["capabilities"], item["capability"], item["conditions"]) for item in found],
            [(["threat modelling", "penetration testing"], "threat modelling", ["STRIDE"]), ([], None, ["Python"])],
        )

    def test_none_of_these_first_gives_no_capability_and_a_real_first_option_keeps_its_runner_up(self) -> None:
        # A runner-up behind "none of these" is Jev doubting there is any, not a second capability.
        requirement: Requirement = {"name": "Active clearance", "tokens": [], "required": True}
        found = [
            jev_mapping.mapped({"R0|capability": {"probabilities": probabilities}}, [requirement], VOCABULARY)[0]
            for probabilities in (
                {NONE: 0.5, "threat modelling": 0.4, "penetration testing": 0.1},
                {"penetration testing": 0.5, "threat modelling": 0.4, NONE: 0.1},
            )
        ]
        self.assertEqual(
            [(item["capabilities"], item["capability"]) for item in found],
            [([], None), (["penetration testing", "threat modelling"], "penetration testing")],
        )

    def test_an_answer_missing_a_question_or_not_fitting_it_is_refused(self) -> None:
        bodies = jev_mapping.request_bodies("Role", REQUIREMENTS, VOCABULARY, DESCRIPTIONS)
        whole = {name: answer for body in bodies for name, answer in answers_to(body)["answers"].items()}
        self.assertEqual(len(jev_mapping.mapped(whole, REQUIREMENTS, VOCABULARY)), 2)  # whole, it is read
        broken = {
            "no choice for a requirement": {key: value for key, value in whole.items() if key != "R1|capability"},
            "no answer for a word": {key: value for key, value in whole.items() if key != "R0|word1"},
            "a probability that is not a number": {**whole, "R0|word0": {"noul": "high"}},
            "an option outside the list": {**whole, "R0|capability": {"probabilities": {"cooking": 0.9}}},
            "True as a probability": {**whole, "R0|word0": {"noul": True}},
            "NaN as a probability": {**whole, "R0|word0": {"noul": math.nan}},
            "Infinity as a probability": {**whole, "R0|word0": {"noul": math.inf}},
            "a probability over 1": {**whole, "R0|word0": {"noul": 1.5}},
            "a probability under 0": {**whole, "R0|capability": {"probabilities": {"threat modelling": -0.1}}},
        }
        for case, answers in broken.items():
            with self.subTest(case=case), self.assertRaises(JevUnavailableError):
                jev_mapping.mapped(answers, REQUIREMENTS, VOCABULARY)

    def test_at_most_three_capabilities_are_kept(self) -> None:
        vocabulary = {name: "Offensive security" for name in ("a", "b", "c", "d")}
        requirement: Requirement = {"name": "Offensive work", "tokens": [], "required": True}
        answers = {"R0|capability": {"probabilities": {"a": 0.4, "b": 0.35, "c": 0.33, "d": 0.31}}}
        found = jev_mapping.mapped(answers, [requirement], vocabulary)
        self.assertEqual(found[0]["capabilities"], ["a", "b", "c"])

    def test_the_six_most_likely_projects_are_kept_best_first_with_no_floor(self) -> None:
        chances = {"P1": 0.9, "P2": 0.1, "P3": 0.8, "P4": 0.05, "P5": 0.3, "P6": 0.2, "P7": 0.01, "P8": 0.4}
        answers = {project: {"type": "noul", "noul": chance} for project, chance in chances.items()}
        # Three of the six are under 0.5: a floor there once cut pages to three projects.
        self.assertEqual(jev_choice.chosen(answers, list(PROJECTS)), ["P1", "P3", "P8", "P5", "P6", "P2"])
        # Equal chances keep the library's order.
        level = {project: {"type": "noul", "noul": 0.5} for project in PROJECTS}
        self.assertEqual(jev_choice.chosen(level, list(PROJECTS)), list(PROJECTS[:6]))
        with self.assertRaises(JevUnavailableError):
            jev_choice.chosen({"P1": {"noul": 0.9}}, list(PROJECTS))


class Records(JevTestCase):
    def test_call_2s_record_is_a_saved_mapping_answered_by_jev(self) -> None:
        stand_in = StandIn()
        record = self.map_with(stand_in)
        self.assertEqual(stand_in.requests[0][0], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(stand_in.requests[0][2], {"Authorization": f"Bearer {KEY}"})
        # Two requests: the choices, and the words.
        self.assertEqual(
            (record["valid"], record["errors"], record["first_errors"], record["attempts"], record["requests"]),
            (True, [], [], 2, 2),
        )
        self.assertEqual((record["input_tokens"], record["output_tokens"]), (1800, 80))
        self.assertEqual(
            (record["model"], record["answered_by"], record["fallback"], record["format"]),
            ("jev-1.13.0", "jev-1.13.0", None, store.MAPPING_FORMAT),
        )
        self.assertEqual(
            (record["title"], record["vocabulary_sha256_16"], record["descriptions_sha256_16"]),
            ("Security Engineer", vocabulary_hash(VOCABULARY), descriptions_hash(DESCRIPTIONS)),
        )
        self.assertIn("made", record)
        self.assertGreaterEqual(record["seconds"], 0)
        self.assertEqual([item["capability"] for item in record["requirements"]], ["threat modelling"] * 2)
        hashes = (vocabulary_hash(VOCABULARY), descriptions_hash(DESCRIPTIONS))
        self.assertTrue(store.is_current(record, *hashes))
        self.assertFalse(store.mapping_needs_call(record, *hashes))
        # An edited description makes it stale.
        edited = {**DESCRIPTIONS, NONE: {**DESCRIPTIONS[NONE], "examples": ["10+ years"]}}
        self.assertTrue(store.mapping_needs_call(record, vocabulary_hash(VOCABULARY), descriptions_hash(edited)))

    def test_a_record_keeps_the_pinned_model_and_the_version_that_answered(self) -> None:
        def newer(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            return 200, answers_to(body, model="jev-1.14.0")

        record = self.map_with(newer)
        # Both requests answered by the one version: named once.
        self.assertEqual((record["model"], record["answered_by"]), ("jev-1.13.0", "jev-1.14.0"))
        answer = {"answers": {project: {"noul": 0.5} for project in PROJECTS}, "model": "jev-1.14.0"}
        choice = jev_choice.ask("Role", "posting", library(), post=StandIn((200, answer)), key=lambda: KEY)
        self.assertEqual((choice["model"], choice["answered_by"]), ("jev-1.13.0", "jev-1.14.0"))

    def test_each_version_that_answered_a_posting_s_requests_is_named_once_in_the_requests_order(self) -> None:
        # TypeSafe may move to a new version while a posting's requests are in flight: here the words' request is
        # answered by the older one.
        def split(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            return 200, answers_to(body, model="jev-1.13.0" if WORDS in body["questions"] else "jev-1.14.0")

        record = jev_mapping.ask(
            "Role", requirements_with_a_word(7), VOCABULARY, descriptions=DESCRIPTIONS, post=split, key=lambda: KEY
        )
        self.assertEqual((record["requests"], record["answered_by"]), (3, "jev-1.14.0, jev-1.13.0"))

    def test_seconds_is_the_time_the_posting_took_not_the_sum_of_its_requests(self) -> None:
        together = threading.Barrier(3, timeout=5)

        def slow(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            together.wait()  # all three requests in flight at once
            time.sleep(0.3)
            return 200, answers_to(body)

        record = jev_mapping.ask(
            "Role", requirements_with_a_word(6), VOCABULARY, descriptions=DESCRIPTIONS, post=slow, key=lambda: KEY
        )
        self.assertEqual(record["requests"], 3)
        # About 0.3 s: the three requests' own times add up to 0.9 s.
        self.assertGreaterEqual(record["seconds"], 0.3)
        self.assertLess(record["seconds"], 0.6)

    def test_a_posting_s_requests_go_at_once_and_its_record_counts_every_one(self) -> None:
        in_flight = threading.Barrier(4, timeout=5)
        stand_in = StandIn((503, None))
        seen: set[int] = set()
        lock = threading.Lock()

        def all_at_once(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            with lock:
                first = id(body) not in seen
                seen.add(id(body))
            if first:
                in_flight.wait()  # each request's first attempt waits for the other three: all four are in flight
            return stand_in(url, body, headers)

        slot = threading.BoundedSemaphore(4)
        record = jev_mapping.map_requirements(
            "Role",
            requirements_with_a_word(11),
            VOCABULARY,
            descriptions=DESCRIPTIONS,
            post=all_at_once,
            key=lambda: KEY,
            slot=slot,
            fallback=opus_mapping,
        )
        # Three choice requests and the word request; the one that drew the 503 asked once more.
        self.assertEqual((len(stand_in.requests), record["fallback"]), (5, None))
        self.assertEqual((record["requests"], record["attempts"]), (4, 5))
        self.assertEqual(record["first_errors"], ["TypeSafe answered HTTP 503"])
        self.assertEqual((record["input_tokens"], record["output_tokens"]), (3600, 160))
        self.assertEqual(len(record["requirements"]), 11)

    def test_a_posting_with_no_requirements_sends_no_request(self) -> None:
        stand_in = StandIn()
        record = jev_mapping.ask("Role", [], VOCABULARY, descriptions=DESCRIPTIONS, post=stand_in, key=lambda: KEY)
        self.assertEqual((stand_in.requests, record["requirements"], record["valid"]), ([], [], True))
        self.assertEqual((record["requests"], record["attempts"], record["answered_by"]), (0, 0, None))

    def test_the_descriptions_are_the_library_s_unless_given(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            write_descriptions(Path(folder), dict(reversed(DESCRIPTIONS.items())))  # written in the other order
            stand_in = StandIn()
            with mock.patch.object(settings, "LIBRARY", Path(folder)):
                record = jev_mapping.map_requirements(
                    "Role", REQUIREMENTS, VOCABULARY, post=stand_in, key=lambda: KEY, fallback=opus_mapping
                )
        [choices] = [body for _url, body, _headers in stand_in.requests if CHOICES in body["questions"]]
        self.assertEqual(choices["questions"]["R0|capability"]["criteria"], DESCRIPTIONS)
        self.assertEqual(
            (record["fallback"], record["descriptions_sha256_16"]), (None, descriptions_hash(DESCRIPTIONS))
        )

    def test_call_3s_record_is_a_saved_choice_the_engine_reads_unchanged(self) -> None:
        stand_in = StandIn(noul=lambda project: {"P8": 0.9, "P7": 0.8}.get(project, 0.1))
        listed = library()
        record = jev_choice.choose_projects("Role", "posting", listed, post=stand_in, key=lambda: KEY)
        self.assertEqual(record["projects"], ["P8", "P7", "P1", "P2", "P3", "P4"])
        self.assertEqual(record["why"], "Jev: the six projects most likely to belong on the page")
        self.assertEqual(
            (record["valid"], record["errors"], record["first_errors"], record["attempts"]), (True, [], [], 1)
        )
        self.assertEqual(
            (record["model"], record["answered_by"], record["fallback"], record["format"]),
            ("jev-1.13.0", "jev-1.13.0", None, store.CHOICE_FORMAT),
        )
        self.assertEqual((record["input_tokens"], record["output_tokens"]), (900, 40))
        self.assertIn("made", record)
        self.assertTrue(store.choice_is_current(record, project_choice.projects_hash(listed)))

    def test_usage_that_is_not_a_number_is_counted_as_none(self) -> None:
        def unreadable(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            return 200, {**answers_to(body), "usage": {"input_tokens": "many", "output_tokens": math.nan}}

        record = jev_mapping.ask(
            "Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS, post=unreadable, key=lambda: KEY
        )
        self.assertEqual((record["input_tokens"], record["output_tokens"], record["fallback"]), (0, 0, None))


class InFlight(JevTestCase):
    """The requests in flight across postings mapped at once: `jev.AT_ONCE` at most, or the caller's own cap."""

    def most_in_flight(self, map_posting: Callable[[str], CapabilityMapping]) -> tuple[int, list[CapabilityMapping]]:
        """(the most Jev requests in flight at once, the records) for three postings mapped at once by `map_posting`.

        Each posting has eleven requirements, so four requests: twelve in all.
        """
        lock = threading.Lock()
        in_flight, most = [0], [0]

        def request(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            with lock:
                in_flight[0] += 1
                most[0] = max(most[0], in_flight[0])
            time.sleep(0.2)
            with lock:
                in_flight[0] -= 1
            return 200, answers_to(body)

        with tempfile.TemporaryDirectory() as folder:
            write_descriptions(Path(folder), DESCRIPTIONS)
            with (
                mock.patch.object(settings, "LIBRARY", Path(folder)),
                mock.patch.object(jev, "_httpx_post", request),
                mock.patch.object(jev, "stored_key", return_value=KEY),
                mock.patch.object(
                    capability_mapping, "map_requirements", mock.Mock(side_effect=AssertionError("Opus"))
                ),
                ThreadPoolExecutor(max_workers=3) as pool,
            ):
                records = list(pool.map(map_posting, ["Role 0", "Role 1", "Role 2"]))
        return most[0], records

    def test_postings_mapped_at_once_by_the_command_line_have_at_most_six_requests_in_flight(self) -> None:
        mapper, _chooser = cli.calls_answered_by("jev", call2="jev")
        most, records = self.most_in_flight(lambda title: mapper(title, requirements_with_a_word(11), VOCABULARY))
        # Without the process-wide cap all twelve would be in flight.
        self.assertEqual(most, jev.AT_ONCE)
        self.assertEqual(
            {(record["model"], record["fallback"], record["requests"]) for record in records}, {("jev-1.13.0", None, 4)}
        )

    def test_a_caller_s_own_slot_caps_its_requests_in_place_of_the_process_wide_cap(self) -> None:
        slot = threading.BoundedSemaphore(2)  # as the dashboard's cap, shared by its postings

        def map_posting(title: str) -> CapabilityMapping:
            return jev_mapping.map_requirements(
                title, requirements_with_a_word(11), VOCABULARY, slot=slot, fallback=opus_mapping
            )

        most, records = self.most_in_flight(map_posting)
        self.assertEqual((most, {record["fallback"] for record in records}), (2, {None}))

    def test_a_caller_s_slot_above_the_process_wide_cap_replaces_it_rather_than_adding_to_it(self) -> None:
        # A dashboard cap raised above AT_ONCE must be what holds; holding both would keep it at AT_ONCE.
        slot = threading.BoundedSemaphore(jev.AT_ONCE + 2)

        def map_posting(title: str) -> CapabilityMapping:
            return jev_mapping.map_requirements(
                title, requirements_with_a_word(11), VOCABULARY, slot=slot, fallback=opus_mapping
            )

        most, _records = self.most_in_flight(map_posting)
        self.assertEqual(most, jev.AT_ONCE + 2)


class Fallbacks(JevTestCase):
    def assert_fell_back(self, record: CapabilityMapping | ProjectChoice, *reasons: str) -> None:
        self.assertEqual(
            record["fallback"],
            {"original_model": "jev-1.13.0", "fallback_model": record["answered_by"], "api_refusal_category": None},
        )
        self.assertEqual(record["first_errors"][: len(reasons)], [f"Jev: {reason}" for reason in reasons])

    def test_no_key_goes_straight_to_opus_with_no_request(self) -> None:
        stand_in = StandIn()
        with mock.patch.object(keyring, "get_password", return_value=None):
            record = jev_mapping.map_requirements(
                "Security Engineer",
                REQUIREMENTS,
                VOCABULARY,
                descriptions=DESCRIPTIONS,
                post=stand_in,
                fallback=opus_mapping,
            )
        self.assertEqual(stand_in.requests, [])
        self.assert_fell_back(record, "no Jev key in the credential store")
        # Opus's own record stays: its model, attempts and first errors after Jev's reason.
        self.assertEqual((record["model"], record["attempts"]), ("claude-opus-5-5", 2))
        self.assertEqual(record["first_errors"][1:], ["an answer asked for again"])

    def test_a_credential_store_that_cannot_be_read_or_is_missing_is_named(self) -> None:
        with (
            mock.patch.object(keyring, "get_password", side_effect=keyring.errors.NoKeyringError("no backend")),
            self.assertRaisesRegex(JevUnavailableError, r"the credential store could not be read \(NoKeyringError\)"),
        ):
            jev.stored_key()
        with (
            mock.patch.dict(sys.modules, {"keyring": None}),
            self.assertRaisesRegex(JevUnavailableError, "keyring is not installed"),
        ):
            jev.stored_key()
        with mock.patch.object(keyring, "get_password", return_value=KEY) as read:
            self.assertEqual(jev.stored_key(), KEY)
        read.assert_called_once_with("typesafe", "jev")

    def test_a_credential_store_error_that_is_not_keyring_s_goes_to_opus_with_no_request(self) -> None:
        # keyring's Windows backend passes on pywintypes.error from CredRead: here, a process with no logon session.
        class StoreError(Exception):
            """As pywintypes.error, whose first argument is also its `winerror`."""

            def __init__(self, *details: object) -> None:
                super().__init__(*details)
                self.winerror = details[0]

        StoreError.__name__ = "error"
        refused = StoreError(1312, "CredRead", "A specified logon session does not exist.")
        stand_in = StandIn()
        with mock.patch.object(keyring, "get_password", side_effect=refused):
            record = jev_mapping.map_requirements(
                "Security Engineer",
                REQUIREMENTS,
                VOCABULARY,
                descriptions=DESCRIPTIONS,
                post=stand_in,
                fallback=opus_mapping,
            )
            with self.assertRaises(JevUnavailableError) as raised:
                jev.stored_key()
        self.assertEqual(stand_in.requests, [])
        # Its kind and its Windows error number; not its text.
        self.assert_fell_back(record, "the credential store could not be read (error, Windows error 1312)")
        self.assertEqual((raised.exception.__cause__, raised.exception.__suppress_context__), (None, True))
        self.assertNotIn("logon session", "".join(traceback.format_exception(raised.exception)))

    def test_a_4xx_goes_straight_to_opus_without_a_second_request(self) -> None:
        stand_in = StandIn((401, {"error": "unauthorised"}), StandIn.ANSWER, asking=CHOICES)
        record = self.map_with(stand_in)
        # The choices asked once; the words' request went beside it and was answered.
        self.assertEqual((stand_in.count(CHOICES), stand_in.count(WORDS)), (1, 1))
        self.assert_fell_back(record, "TypeSafe answered HTTP 401")

    def test_a_5xx_is_asked_once_more_and_a_second_5xx_goes_to_opus(self) -> None:
        recovered = StandIn((503, None), StandIn.ANSWER, asking=CHOICES)
        record = self.map_with(recovered)
        self.assertEqual((recovered.count(CHOICES), record["model"], record["fallback"]), (2, "jev-1.13.0", None))
        self.assertEqual((record["attempts"], record["first_errors"]), (3, ["TypeSafe answered HTTP 503"]))
        # Exactly once more: the answer waiting as a third outcome is never asked for.
        failed = StandIn((503, None), (502, {"error": "bad gateway"}), StandIn.ANSWER, asking=CHOICES)
        record = self.map_with(failed)
        self.assertEqual(failed.count(CHOICES), 2)
        self.assert_fell_back(record, "TypeSafe answered HTTP 503", "TypeSafe answered HTTP 502")

    def test_http_500_is_asked_once_more_and_a_4xx_after_a_5xx_keeps_both_reasons(self) -> None:
        recovered = StandIn((500, None), StandIn.ANSWER, asking=CHOICES)
        self.assertEqual((self.map_with(recovered)["fallback"], recovered.count(CHOICES)), (None, 2))
        refused = StandIn((503, None), (401, None), StandIn.ANSWER, asking=CHOICES)
        record = self.map_with(refused)
        self.assertEqual(refused.count(CHOICES), 2)
        self.assert_fell_back(record, "TypeSafe answered HTTP 503", "TypeSafe answered HTTP 401")

    def test_an_unexpected_error_after_a_5xx_keeps_both_reasons_and_its_text_is_not_chained(self) -> None:
        # Its text may repeat the request, key included: only its kind is kept.
        error = RuntimeError(f"the request {KEY} could not be made")
        unexpected = StandIn((503, None), error, StandIn.ANSWER, asking=CHOICES)
        record = self.map_with(unexpected)
        self.assertEqual(unexpected.count(CHOICES), 2)
        self.assert_fell_back(record, "TypeSafe answered HTTP 503", "the request failed (RuntimeError)")
        with self.assertRaises(JevUnavailableError) as raised:
            jev_mapping.ask(
                "Role",
                REQUIREMENTS,
                VOCABULARY,
                descriptions=DESCRIPTIONS,
                post=StandIn(RuntimeError(f"the request {KEY} could not be made")),
                key=lambda: KEY,
            )
        self.assertNotIn(KEY, "".join(traceback.format_exception(raised.exception)))

    def test_opus_s_own_refusal_category_is_kept_in_the_fallback_notice(self) -> None:
        # Jev could not answer, then Opus 5.5 refused and the command line fell back to Opus 4.8.
        def refused_opus(title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
            record = opus_mapping(title, requirements, vocabulary)
            record["answered_by"] = "claude-opus-4-8"
            record["fallback"] = {
                "original_model": "claude-opus-5-5",
                "fallback_model": "claude-opus-4-8",
                "api_refusal_category": "cyber",
            }
            return record

        record = self.map_with(StandIn((401, None)), opus=refused_opus)
        self.assertEqual(
            record["fallback"],
            {"original_model": "jev-1.13.0", "fallback_model": "claude-opus-4-8", "api_refusal_category": "cyber"},
        )

    def test_a_429_is_asked_again_after_a_backoff_at_most_twice_and_outside_the_slot(self) -> None:
        slot = Slot()
        for outcomes, requests, fell_back in (
            (((429, None), StandIn.ANSWER), 2, False),
            (((429, None), (429, None), StandIn.ANSWER), 3, False),
            (((429, None), (429, None), (429, None), StandIn.ANSWER), 3, True),
        ):
            with self.subTest(requests=requests, fell_back=fell_back):
                stand_in = StandIn(*outcomes, asking=CHOICES)
                waits: list[tuple[float, str]] = []
                sleep = mock.Mock(side_effect=lambda seconds, waits=waits: waits.append((seconds, slot.held[-1])))
                with mock.patch.object(time, "sleep", sleep):
                    record = jev_mapping.map_requirements(
                        "Role",
                        REQUIREMENTS,
                        VOCABULARY,
                        descriptions=DESCRIPTIONS,
                        post=stand_in,
                        key=lambda: KEY,
                        slot=slot,
                        fallback=opus_mapping,
                    )
                self.assertEqual((stand_in.count(CHOICES), stand_in.count(WORDS)), (requests, 1))
                # 1 s, then 2 s, each waited with the slot let go by the request that waits.
                self.assertEqual(waits, [(1.0, "out"), (2.0, "out")][: requests - 1])
                limited = ["TypeSafe answered HTTP 429"] * (3 if fell_back else requests - 1)
                if fell_back:
                    self.assert_fell_back(record, *limited)
                else:
                    self.assertEqual((record["fallback"], record["attempts"]), (None, requests + 1))
                    self.assertEqual(record["first_errors"], limited)

    def test_a_429_waits_as_its_retry_after_says_within_the_request_s_limit(self) -> None:
        for retry_after, wait in (
            ("3", 3.0),
            ("0.5", 0.5),
            ("90", 5.0),
            ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0),
            ("-1", 1.0),  # time.sleep(-1) raises, and its error would escape the fallback to Opus
        ):
            with self.subTest(retry_after=retry_after):
                limited = httpx.Response(429, headers={"Retry-After": retry_after})
                with (
                    mock.patch.object(httpx, "post", httpx_stand_in(limited)),
                    mock.patch.object(time, "sleep") as sleep,
                ):
                    record = jev_mapping.map_requirements(
                        "Role",
                        REQUIREMENTS,
                        VOCABULARY,
                        descriptions=DESCRIPTIONS,
                        key=lambda: KEY,
                        fallback=opus_mapping,
                    )
                # The choices asked twice, the words once.
                self.assertEqual((record["fallback"], record["attempts"]), (None, 3))
                sleep.assert_called_once_with(wait)

    def test_a_timeout_or_a_network_error_is_asked_once_more_through_httpx(self) -> None:
        for first, reason in (
            (httpx.ReadTimeout("slow"), "the request timed out after 5 s"),
            (httpx.ConnectError("refused"), "the request failed (ConnectError)"),
        ):
            with self.subTest(first=type(first).__name__):
                recovered = httpx_stand_in(first)
                with mock.patch.object(httpx, "post", recovered):
                    record = jev_mapping.map_requirements(
                        "Security Engineer",
                        REQUIREMENTS,
                        VOCABULARY,
                        descriptions=DESCRIPTIONS,
                        key=lambda: KEY,
                        fallback=opus_mapping,
                    )
                # The choices asked twice, the words once.
                self.assertEqual(
                    (recovered.call_count, record["fallback"], record["first_errors"]), (3, None, [reason])
                )
                failing = httpx_stand_in(first, first)
                with mock.patch.object(httpx, "post", failing):
                    record = jev_mapping.map_requirements(
                        "Security Engineer",
                        REQUIREMENTS,
                        VOCABULARY,
                        descriptions=DESCRIPTIONS,
                        key=lambda: KEY,
                        fallback=opus_mapping,
                    )
                self.assertEqual(failing.call_count, 3)
                self.assert_fell_back(record, reason, reason)
        # The request itself: the address, the header and the 5-second limit.
        self.assertEqual(recovered.call_args.args, ("https://api.typesafe.ai/v1/systemone",))
        self.assertEqual(recovered.call_args.kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(recovered.call_args.kwargs["timeout"], 5.0)

    def test_a_partial_answer_or_one_with_no_answers_goes_straight_to_opus(self) -> None:
        _choices, words = jev_mapping.request_bodies("Security Engineer", REQUIREMENTS, VOCABULARY, DESCRIPTIONS)
        partial = answers_to(words)
        del partial["answers"]["R0|word1"]
        for outcome, reason in (
            ((200, partial), "an answer without its noul"),
            ((200, None), "TypeSafe's answer holds no answers"),
        ):
            with self.subTest(reason=reason):
                stand_in = StandIn(outcome, StandIn.ANSWER, asking=WORDS)
                record = self.map_with(stand_in)
                self.assertEqual(stand_in.count(WORDS), 1)
                self.assert_fell_back(record, reason)
        # A body that is not JSON at all reads as one with no answers.
        with mock.patch.object(httpx, "post", return_value=httpx.Response(200, content=b"<html>")):
            record = jev_mapping.map_requirements(
                "Security Engineer",
                REQUIREMENTS,
                VOCABULARY,
                descriptions=DESCRIPTIONS,
                key=lambda: KEY,
                fallback=opus_mapping,
            )
        self.assert_fell_back(record, "TypeSafe's answer holds no answers")

    def test_any_one_request_jev_cannot_answer_sends_the_whole_posting_to_opus(self) -> None:
        many = requirements_with_a_word(11)
        asked_opus: list[int] = []

        def opus(title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
            asked_opus.append(len(requirements))
            return opus_mapping(title, requirements, vocabulary)

        def request_asking(
            question: str, broken: Callable[[dict[str, Any]], tuple[int, Any]]
        ) -> Callable[..., tuple[int, Any]]:
            # The request holding `question` gets `broken`; the others are answered whole.
            def post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
                return broken(body) if question in body["questions"] else (200, answers_to(body))

            return post

        def one_left_out(body: dict[str, Any]) -> tuple[int, Any]:
            answer = answers_to(body)
            del answer["answers"]["R7|word0"]
            return 200, answer

        def answered_elsewhere(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            # The second request leaves R9's choice out, and the third answers it though it was not asked: an answer
            # counts only from the request that asked the question.
            answer = answers_to(body)
            if "R5|capability" in body["questions"]:
                del answer["answers"]["R9|capability"]
            if "R10|capability" in body["questions"]:
                answer["answers"]["R9|capability"] = {"type": "choice", "probabilities": {"threat modelling": 0.9}}
            return 200, answer

        for case, (post, reason) in enumerate(
            (
                (request_asking("R5|capability", lambda body: (401, None)), "TypeSafe answered HTTP 401"),
                (request_asking(WORDS, lambda body: (401, None)), "TypeSafe answered HTTP 401"),  # the words' request
                (request_asking(WORDS, one_left_out), "an answer without its noul"),
                (answered_elsewhere, "no answer for requirement 9"),
            )
        ):
            with self.subTest(case=case, reason=reason):
                asked_opus.clear()
                record = jev_mapping.map_requirements(
                    "Role",
                    many,
                    VOCABULARY,
                    descriptions=DESCRIPTIONS,
                    post=post,
                    key=lambda: KEY,
                    fallback=opus,
                )
                self.assertEqual(asked_opus, [11])
                self.assert_fell_back(record, reason)

    def test_call_3_goes_to_opus_5_when_jev_cannot_answer(self) -> None:
        stand_in = StandIn((400, {"error": "bad request"}))
        record = jev_choice.choose_projects(
            "Role", "posting", library(), post=stand_in, key=lambda: KEY, fallback=opus_choice
        )
        self.assertEqual(len(stand_in.requests), 1)
        self.assert_fell_back(record, "TypeSafe answered HTTP 400")
        self.assertEqual((record["projects"], record["answered_by"]), (["P2", "P1", "P3", "P4"], "claude-opus-5"))
        self.assertTrue(store.choice_is_current(record, project_choice.projects_hash(library())))
        partial = StandIn((200, {"answers": {"P1": {"noul": 0.9}}}))
        record = jev_choice.choose_projects(
            "Role", "posting", library(), post=partial, key=lambda: KEY, fallback=opus_choice
        )
        self.assert_fell_back(record, "an answer without its noul")

    def test_the_default_fallbacks_are_opus_looked_up_when_made(self) -> None:
        with (
            mock.patch.object(jev, "stored_key", side_effect=JevUnavailableError("no Jev key in the credential store")),
            mock.patch.object(capability_mapping, "map_requirements", opus_mapping),
            mock.patch.object(project_choice, "choose_projects", opus_choice),
        ):
            mapping = jev_mapping.map_requirements("Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS)
            choice = jev_choice.choose_projects("Role", "posting", library())
        self.assertEqual((mapping["answered_by"], choice["answered_by"]), ("claude-opus-5-5", "claude-opus-5"))

    def test_an_identity_word_stops_call_3_before_any_request_or_fallback(self) -> None:
        stand_in = StandIn()
        opus = mock.Mock(side_effect=AssertionError("Opus asked"))
        with self.assertRaises(privacy.PrivacyError):
            jev_choice.choose_projects(
                "Role", "posting", library("Built for SecretName"), post=stand_in, key=lambda: KEY, fallback=opus
            )
        self.assertEqual(stand_in.requests, [])

    def test_an_identity_word_beside_a_dash_is_still_found(self) -> None:
        # Written as JSON with escapes, "for—SecretName" would read as the words "for" and "u2014secretname".
        stand_in = StandIn()
        with self.assertRaises(privacy.PrivacyError):
            jev_choice.ask("Role", "posting", library("Built for—SecretName"), post=stand_in, key=lambda: KEY)
        self.assertEqual(stand_in.requests, [])

    def test_an_identity_word_after_a_tab_or_a_line_break_in_a_summary_stops_call_3(self) -> None:
        # In JSON "for\tSecretName" is written with an escape joined to the word, which hid it from the guard.
        for summary in ("Built for\tSecretName", "Built for\r\nSecretName"):
            with self.subTest(summary=summary), self.assertRaises(privacy.PrivacyError):
                stand_in = StandIn()
                jev_choice.ask("Role", "posting", library(summary), post=stand_in, key=lambda: KEY)
            self.assertEqual(stand_in.requests, [])

    def test_call_3_s_guard_reads_exactly_the_project_list_once(self) -> None:
        listed = library()
        with mock.patch.object(jev_choice, "check_outgoing") as guard:
            jev_choice.ask("Role", "Apply at github.com/employer", listed, post=StandIn(), key=lambda: KEY)
        guard.assert_called_once_with(project_choice.project_list(listed))

    def test_a_posting_holding_an_identity_word_is_sent_and_a_summary_holding_one_is_stopped(self) -> None:
        # Call 3's guard reads the project list alone, as before Opus: the posting is the employer's.
        title, posting = "SecretName Engineer", "Apply at github.com/secretname or noreply@gmail.com"
        stand_in = StandIn()
        record = jev_choice.ask(title, posting, library(), post=stand_in, key=lambda: KEY)
        self.assertEqual((len(stand_in.requests), record["fallback"]), (1, None))
        self.assertEqual(stand_in.requests[0][1]["state"]["job_posting"], {"title": title, "text": posting})
        stopped = StandIn()
        with self.assertRaises(privacy.PrivacyError):
            jev_choice.ask(title, posting, library("Built for SecretName"), post=stopped, key=lambda: KEY)
        self.assertEqual(stopped.requests, [])

    def test_an_identity_word_in_call_2_s_request_stops_it_before_any_request_or_fallback(self) -> None:
        opus = mock.Mock(side_effect=AssertionError("Opus asked"))
        named: list[Requirement] = [{**REQUIREMENTS[0], "tokens": ["threat modelling", "SecretName"]}, REQUIREMENTS[1]]
        tabbed = "fuzzing\tSecretName"
        with_tab = {**DESCRIPTIONS, tabbed: {**DESCRIPTIONS["penetration testing"], "domain": "Offensive"}}
        described = {**DESCRIPTIONS, "threat modelling": {**DESCRIPTIONS["threat modelling"], "what": "as SecretName"}}
        for case, title, requirements, vocabulary, descriptions in (
            ("a requirement's word", "Role", named, VOCABULARY, DESCRIPTIONS),
            ("a capability after a tab", "Role", REQUIREMENTS, {**VOCABULARY, tabbed: "Offensive"}, with_tab),
            ("the role", "SecretName Engineer", REQUIREMENTS, VOCABULARY, DESCRIPTIONS),
            ("a capability's description", "Role", REQUIREMENTS, VOCABULARY, described),
        ):
            with self.subTest(case=case), self.assertRaises(privacy.PrivacyError):
                stand_in = StandIn()
                jev_mapping.map_requirements(
                    title,
                    requirements,
                    vocabulary,
                    descriptions=descriptions,
                    post=stand_in,
                    key=lambda: KEY,
                    fallback=opus,
                )
            self.assertEqual(stand_in.requests, [])

    def test_jev_time_is_added_to_opus_time_where_the_posting_waited(self) -> None:
        def slow_refusal(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            time.sleep(0.3)
            return 401, None

        record = jev_mapping.map_requirements(
            "Role",
            REQUIREMENTS,
            VOCABULARY,
            descriptions=DESCRIPTIONS,
            post=slow_refusal,
            key=lambda: KEY,
            fallback=opus_mapping,
        )
        self.assertGreaterEqual(record["seconds"], 8.3)  # Opus's 8.0 s and Jev's 0.3 s before it
        self.assertLess(record["seconds"], 9.0)

    def test_the_slot_is_held_around_each_attempt(self) -> None:
        slot = Slot()
        asked: set[int] = set()
        lock = threading.Lock()

        def post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            self.assertEqual(slot.held[-1], "in", "the request went out without its slot")
            with lock:
                first = id(body) not in asked
                asked.add(id(body))
            return (503, None) if first else (200, answers_to(body))

        record = jev_mapping.ask(
            "Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS, post=post, key=lambda: KEY, slot=slot
        )
        # Each of the two requests asked twice, the slot taken and let go around each attempt.
        held = [entry for thread in slot.threads.values() for entry in thread]
        self.assertEqual((held.count("in"), record["attempts"]), (4, 4))
        for thread in slot.threads.values():
            self.assertEqual(thread, ["in", "out"] * (len(thread) // 2))


class Slot:
    """A stand-in for the dashboard's cap on requests in flight: records each time it is taken and let go.

    One record a thread: a posting's requests go at once, each on a thread of its own; `held` is this thread's.
    """

    def __init__(self) -> None:
        self.threads: dict[int, list[str]] = {}

    @property
    def held(self) -> list[str]:
        return self.threads.setdefault(threading.get_ident(), [])

    def __enter__(self) -> None:
        self.held.append("in")

    def __exit__(self, *details: object) -> None:
        self.held.append("out")


class TheKey(JevTestCase):
    def test_the_key_is_in_no_record_error_or_log(self) -> None:
        seen = io.StringIO()
        handler = logging.StreamHandler(seen)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        echoed = {"error": f"the key {KEY} is not valid"}  # a service that echoes the key back must not carry it on
        records: list[Any] = []
        failures: list[str] = []
        # What the choices' request meets in turn; the words' request is answered.
        sequences: list[list[Any]] = [
            [httpx.Response(401, json=echoed)],
            [httpx.Response(503, json=echoed), httpx.ConnectError(f"refused {KEY}")],  # an error repeating the key
            [httpx.ReadTimeout("slow")],
        ]
        for sequence in sequences:
            with mock.patch.object(httpx, "post", httpx_stand_in(*sequence)):
                records.append(
                    jev_mapping.map_requirements(
                        "Role",
                        REQUIREMENTS,
                        VOCABULARY,
                        descriptions=DESCRIPTIONS,
                        key=lambda: KEY,
                        fallback=opus_mapping,
                    )
                )
            with mock.patch.object(httpx, "post", httpx_stand_in(*sequence)):
                try:
                    jev_mapping.ask("Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS, key=lambda: KEY)
                except JevUnavailableError as error:
                    failures.append("".join(traceback.format_exception(error)))
        with mock.patch.object(httpx, "post", mock.Mock(side_effect=[httpx.Response(401, json=echoed)])):
            records.append(
                jev_choice.choose_projects("Role", "posting", library(), key=lambda: KEY, fallback=opus_choice)
            )
        self.assertEqual([bool(record["fallback"]) for record in records], [True, True, False, True])
        self.assertEqual(len(failures), 2)
        for text in [json.dumps(record) for record in records] + failures + [seen.getvalue()]:
            self.assertNotIn(KEY, text)


FAKE_KEY = "fake-KEY-0123456789"


class MalformedKeys(JevTestCase):
    """A key copied with whitespace or a character a header cannot carry.

    Given straight to the request, as a stand-in could, it goes to a socket on this machine through the real httpx,
    whose protocol layer refuses it with an error that repeats the header.
    """

    def setUp(self) -> None:
        super().setUp()
        listening = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listening.close)

        def answer() -> None:  # HTTP 401 to every request that is sent whole, once it has been read whole
            while True:
                try:
                    connection, _address = listening.accept()
                except OSError:
                    return
                with connection, connection.makefile("rb") as sent:
                    head = list(iter(lambda: sent.readline().strip().lower(), b""))  # up to the blank line, or none
                    if head:
                        length = next((int(line[15:]) for line in head if line.startswith(b"content-length:")), 0)
                        sent.read(length)
                        connection.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")

        threading.Thread(target=answer, daemon=True).start()
        local = f"http://127.0.0.1:{listening.getsockname()[1]}/"

        def local_only(url: str, **options: Any) -> httpx.Response:
            self.assertEqual(url, local, "a request not to this machine")
            return HTTPX_POST(url, **options)

        self.requests = mock.Mock(side_effect=local_only)
        for patch in (mock.patch.object(jev, "URL", local), mock.patch.object(httpx, "post", self.requests)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_key_is_stripped_and_one_holding_a_space_or_a_character_outside_ascii_is_refused(self) -> None:
        for stored in (f"{FAKE_KEY} \n", f"\t{FAKE_KEY}\u00a0"):
            with self.subTest(stored=stored), mock.patch.object(keyring, "get_password", return_value=stored):
                self.assertEqual(jev.stored_key(), FAKE_KEY)
        for stored in ("fake KEY", "fake\u00a0KEY", "fake\x00KEY", "fake-KEY-\u00e9"):
            with (
                self.subTest(stored=stored),
                mock.patch.object(keyring, "get_password", return_value=stored),
                self.assertRaisesRegex(JevUnavailableError, "holds a space, a control character") as refused,
            ):
                jev.stored_key()
            self.assertNotIn("KEY", "".join(traceback.format_exception(refused.exception)))

    def test_a_malformed_key_is_in_no_traceback_log_or_record_when_opus_then_fails(self) -> None:
        seen = io.StringIO()
        handler = logging.StreamHandler(seen)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        opus_fails = mock.Mock(side_effect=RuntimeError("Opus did not answer"))
        texts: list[str] = []
        # (the key, whether it is read from the credential store, the requests expected, why Jev could not answer):
        # call 2's two requests, the choices and the words, each sent once.
        for key, stored, requests, reason in (
            (f"{FAKE_KEY} \n", True, 2, "TypeSafe answered HTTP 401"),
            (f"{FAKE_KEY}\u00a0", True, 2, "TypeSafe answered HTTP 401"),
            ("fake\u00a0KEY-0123456789", True, 0, "holds a space, a control character"),
            (f"{FAKE_KEY} \n", False, 2, r"the request could not be sent \(LocalProtocolError\)"),
            (f"{FAKE_KEY}\u00a0", False, 2, r"the request failed \(UnicodeEncodeError\)"),
        ):
            self.requests.reset_mock()
            seen.seek(0)
            seen.truncate()
            with (
                self.subTest(key=key, stored=stored),
                mock.patch.object(keyring, "get_password", return_value=key),
                self.assertRaises(RuntimeError) as failed,
            ):
                options: dict[str, Any] = {} if stored else {"key": lambda key=key: key}
                jev_mapping.map_requirements(
                    "Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS, fallback=opus_fails, **options
                )
            text = "".join(traceback.format_exception(failed.exception))
            self.assertRegex(text, reason)
            self.assertEqual(self.requests.call_count, requests)  # a malformed request is not sent again
            texts.append(text)
            with mock.patch.object(keyring, "get_password", return_value=key):
                record = jev_mapping.map_requirements(
                    "Role", REQUIREMENTS, VOCABULARY, descriptions=DESCRIPTIONS, fallback=opus_mapping, **options
                )
            texts.append(json.dumps(record))
            # The logs of a key read from the store. A malformed header given straight to httpx is repeated by
            # httpcore's own debug log when it refuses it, which is why the key is checked where it is read.
            texts += [seen.getvalue()] if stored else []
        for text in texts:
            self.assertNotIn("KEY-0123456789", text)


class Classifier(unittest.TestCase):
    def test_jev_answers_unless_the_environment_names_opus(self) -> None:
        path = str(Path(settings.__file__))
        without = {
            name: value for name, value in os.environ.items() if name not in ("TAILOR_CLASSIFIER", "TAILOR_CALL2")
        }
        with mock.patch.dict(os.environ, without, clear=True):
            defaults = runpy.run_path(path)
        self.assertEqual((defaults["CLASSIFIER"], defaults["CALL2"]), ("jev", "opus"))  # call 2 on Opus
        with mock.patch.dict(os.environ, {"TAILOR_CLASSIFIER": "opus", "TAILOR_CALL2": "jev"}):
            named = runpy.run_path(path)
        self.assertEqual((named["CLASSIFIER"], named["CALL2"]), ("opus", "jev"))

    def test_opus_as_the_classifier_answers_call_2_whatever_call_2_s_setting(self) -> None:
        with mock.patch.object(settings, "CALL2", "jev"):
            models = [
                pipeline.call2_model(classifier, call2) for classifier in ("jev", "opus") for call2 in (None, "opus")
            ]
        self.assertEqual(models, ["jev", "opus", "opus", "opus"])

    def test_each_classifier_answers_calls_2_and_3_and_an_unknown_one_is_refused(self) -> None:
        with (
            mock.patch.object(jev_mapping, "map_requirements", return_value={"model": "jev-latest"}),
            mock.patch.object(capability_mapping, "map_requirements", return_value={"model": "claude-opus-5-5"}),
            mock.patch.object(jev_choice, "choose_projects", return_value={"model": "jev-latest"}),
            mock.patch.object(project_choice, "choose_projects", return_value={"model": "claude-opus-5"}),
        ):
            answered = {
                classifier: (
                    pipeline.call2_mapper(classifier)("Role", [], {})["model"],
                    pipeline.call3_chooser(classifier)("Role", "text", library())["model"],
                )
                for classifier in settings.CLASSIFIERS
            }
        self.assertEqual(answered, {"jev": ("jev-latest", "jev-latest"), "opus": ("claude-opus-5-5", "claude-opus-5")})
        for make in (pipeline.call2_mapper, pipeline.call3_chooser):
            with self.assertRaisesRegex(ValueError, "one of jev, opus, not 'gpt'"):
                make("gpt")


if __name__ == "__main__":
    unittest.main()
