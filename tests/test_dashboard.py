"""The dashboard's server, on a copy of the saved postings, readings and mappings, with the model calls replaced.

Only this machine is answered, and a change needs the dashboard's header; saved postings are tracked, at "read" when
read; a read job's page is built with each requirement's credit and the Word file's paragraphs; status and notes are
kept; a pasted posting is added once, and text too short to be one is refused; reading runs the calls a posting lacks,
reports each step, builds the page and moves the job to "read"; a failed reading leaves the job unread; the judge
reads the page and each requirement's credit, an answer over its length's limits is asked for again, and the answer
is kept; a judge's page edit that does not fit is asked for again, an accepted one changes the page, survives a
rebuild and can be undone, one that stops fitting is marked; Profile and Stats read the library and the saved costs.
Jev answers call 3 by default, and call 2 on Speed (Accuracy, the default, has Opus answer it in its batches, call 3
still on Jev); which model answers call 2 is chosen as each reading starts, a mapping the other made stays current,
and Read again makes it with the mode then. On Jev, call 2 goes at once, several postings at a time up to a cap and
never batched, asked with the capability descriptions of the dashboard's library and stale once one changes, and a
Jev mapping asked with other descriptions gives the page no fingerprint; call 3 starts beside call 1 on its own pool,
a thread for each Jev request in flight, holding the cap; a page built from its choice is not built again; the
reading schedule says how many call 2s and call 3s run at once for the model answering each. Where Jev cannot map a
posting, Opus does in its batches (with no key stored, every posting) without waiting for another posting's Jev call
2; where Jev cannot choose, Opus makes call 3 only for a low-match page, not behind other postings' hung Jev calls,
and the record, Stats and the job say which model answered. With Opus as the classifier, call 2 goes in batches
whatever the mode and call 3 follows a low-match page on its own pool. Legacy mode never makes call 3, and Read all
makes it for a read page that wants it; a refused page makes none, and an early call 3 that fails is logged, as one
line when Jev or the privacy guard stopped it. The modes are kept, an old modes file holding a priority or no call 2
choice still loads, and a change naming a field it does not know is refused; Read again makes every call again; every
alternative capability is shown and counted; Stats counts call 3, and Jev's requests apart from the Claude plan's
tokens; the reading estimate follows the calls of the model answering each call now, Opus's in Jev's place counting
for Jev; a page another thread holds is read or replaced once it is let go. Guards fail any real model call, Jev
request or credential-store read.
Runs on a copy of the example postings, readings and mappings, with the example library.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import keyring

from tailor_engine import layout, privacy, settings
from tailor_engine.library.capability_descriptions import descriptions_hash
from tailor_engine.reading import capability_mapping, jev, jev_mapping, model_call, project_choice, requirements, store
from tailor_engine.rendering.judge_input import NUMBER_WORDS
from tailor_engine.selection.capability_matching import capabilities_of
from tailor_engine.selection.page import Refused
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

try:
    from fastapi.testclient import TestClient

    from tailor_dashboard import review
    from tailor_dashboard.display import printed_paragraphs
    from tailor_dashboard.jobs import JUDGE_SECONDS_BEFORE_ANY, Paths
    from tailor_dashboard.server import create_app
except ImportError:  # the dashboard's optional dependencies are not installed
    TestClient = None  # type: ignore[assignment,misc]

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_LIBRARY = EXAMPLES / "library"
EXAMPLE_DATA = EXAMPLES / "data"
# Example posting X8, the non-technical control: read, its engine match low (0.28), and no saved call 3 answer in the
# copy each test makes. Pasted postings are read with its saved reading and mapping, so their pages are low too.
READ_POSTING = "paste-f1f45dad"
# Example posting X1, read: its page meets most requirements through bullets.
WELL_MET = "paste-89f5dd84"
CHANGE = {"X-Tailor": "1"}
PASTED = (
    "Security Engineer, Test Posting\nLocation: Portland, OR\n\nRequirements\n- 2+ years of security engineering\n"
    "- Python, threat modelling and secure code review\n\nNice to have\n- Fuzzing and reverse engineering\n\n"
    "We sponsor visas for this role."
)


@unittest.skipIf(TestClient is None, "the dashboard's dependencies (fastapi) are not installed")
@unittest.skipUnless((EXAMPLE_DATA / "postings" / f"{READ_POSTING}.json").exists(), "the example data is not here")
class Dashboard(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        for kind in ("postings", "readings", "mappings"):
            shutil.copytree(EXAMPLE_DATA / kind, self.data / kind)
        # No test may reach a model: a call a test forgot to stand in fails here instead of spending the account's use.
        guard = mock.patch.object(model_call, "_ask_model", mock.Mock(side_effect=AssertionError("a real model call")))
        guard.start()
        self.addCleanup(guard.stop)
        # Nor TypeSafe, nor the credential store. A Jev request runs on a background thread, where the guard's error is
        # only logged, so each test also fails afterwards if either guard was reached.
        for target, name in ((httpx, "post"), (keyring, "get_password")):
            jev_guard = mock.patch.object(target, name, mock.Mock(side_effect=AssertionError(f"a real {name}")))
            reached: mock.Mock = jev_guard.start()
            self.addCleanup(jev_guard.stop)
            self.addCleanup(self.assert_not_reached, reached, name)
        # Call 3 on Jev runs the privacy guard; it checks against a word of its own, not the user's file.
        fingerprints = self.data / "fingerprints-for-tests.txt"
        fingerprints.write_text(privacy.fingerprint("zzqxjvword") + "\n", encoding="utf-8")
        listed = mock.patch.object(privacy, "FINGERPRINTS_FILE", fingerprints)
        listed.start()
        self.addCleanup(listed.stop)
        # Anything that falls back to the default library reads the example library, never library/.
        default_library = mock.patch.object(settings, "LIBRARY", EXAMPLE_LIBRARY)
        default_library.start()
        self.addCleanup(default_library.stop)
        # Jev answers calls 2 and 3 unless a test makes Opus the classifier or switches call 2 to Accuracy, whatever
        # TAILOR_CLASSIFIER says here: call 2 is on Speed, not the default Accuracy.
        self.app = create_app(Paths.under(self.data, EXAMPLE_LIBRARY), hosts={"testserver"}, classifier="jev")
        self.app.state.jobs.modes.set(call2="jev")
        # Listing the jobs queues a rebuild of every read job's page, and each test would end waiting on one. No test
        # here reads a page the list queued (one that needs a page asks for it), so the queue is stood in.
        self.rebuilds = mock.patch.object(self.app.state.background, "rebuild_stale")
        self.rebuilds.start()
        self.addCleanup(self.rebuilds.stop)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def assert_not_reached(self, guard: mock.Mock, name: str) -> None:
        self.assertFalse(guard.called, f"a real {name} was attempted")

    def wait_for_the_end(self, heard: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
        """The reading's last event ("read" or "read-failed"), once it has been sent."""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ends = [event for event in heard if event.get("id") == job_id and event["type"] in ("read", "read-failed")]
            if ends:
                return ends[0]
            time.sleep(0.05)
        raise self.failureException("the reading did not finish")

    def test_only_this_machine_is_answered_and_a_change_needs_the_header(self) -> None:
        self.assertEqual(self.client.get("/api/jobs", headers={"Host": "attacker.example"}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/jobs/{READ_POSTING}", json={"notes": "x"}).status_code, 403)
        self.assertEqual(
            self.client.patch(f"/api/jobs/{READ_POSTING}", json={"notes": "x"}, headers=CHANGE).status_code, 200
        )

    def test_saved_postings_are_tracked_and_a_read_one_gets_its_page(self) -> None:
        jobs = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}
        self.assertEqual(jobs[WELL_MET]["status"], "read")
        self.assertEqual(jobs[WELL_MET]["needs"], [])
        detail = self.client.post(f"/api/jobs/{WELL_MET}/rebuild", headers=CHANGE).json()
        self.assertTrue(detail["page_current"])
        self.assertGreater(len(detail["requirements"]), 0)
        states = {requirement["state"] for requirement in detail["requirements"]}
        self.assertLessEqual(states, {"met", "partial", "missing"})
        met = [requirement for requirement in detail["requirements"] if requirement["state"] == "met"]
        self.assertTrue(
            any(requirement["by"] for requirement in met), "a met requirement names the bullet that meets it"
        )
        self.assertEqual(
            detail["tally"]["met"], sum(r["required"] and r["state"] == "met" for r in detail["requirements"])
        )
        self.assertTrue(any(paragraph["heading"] for paragraph in detail["printed"]))
        self.assertTrue(any(paragraph["bullet"] for paragraph in detail["printed"]))
        word = self.client.get(f"/api/jobs/{WELL_MET}/word")
        self.assertEqual(word.status_code, 200)
        self.assertEqual(word.content[:2], b"PK")  # a Word file is a zip package
        self.assertIn("Resume", word.headers["content-disposition"])

    def test_the_list_and_the_detail_carry_the_fields_the_page_reads(self) -> None:
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        listed = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}
        job = listed[READ_POSTING]
        self.assertIn(job["work_mode"], ("Remote", "Hybrid", "Onsite", "Not stated"))
        self.assertFalse({"mode", "clearance"} & set(job))
        self.assertGreater(len(job["reqs"]), 0)
        self.assertEqual({key for entry in job["reqs"] for key in entry}, {"state", "caps"})
        detail = self.client.get(f"/api/jobs/{READ_POSTING}").json()
        self.assertFalse({"stage", "page_built"} & set(detail))
        self.assertIsNone(detail["judging"])
        self.assertFalse({"cap", "credit"} & {key for entry in detail["requirements"] for key in entry})

    def test_every_alternative_capability_is_shown_and_counted(self) -> None:
        jobs = self.app.state.jobs
        posting = jobs.posting(READ_POSTING)
        _reading, mapping = jobs.records(posting)
        assert mapping is not None
        library, _fingerprint = jobs.library_cache.get()
        asked_before = self.client.get("/api/profile").json()["asked"]
        # Two capabilities the posting does not ask for yet become its first requirement's alternatives.
        named = {name for entry in mapping["requirements"] for name in capabilities_of(entry)}
        either = [name for name in library.capability_vocabulary if name not in named][:2]
        first, *rest = mapping["requirements"]
        changed: Any = {**mapping, "requirements": [{**first, "capabilities": either}, *rest]}
        store.save_mapping(posting["text"], changed, self.data / "mappings")
        detail = self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE).json()
        self.assertEqual({entry["name"]: entry["caps"] for entry in detail["requirements"]}[first["name"]], either)
        listed = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}
        self.assertIn(either, [entry["caps"] for entry in listed[READ_POSTING]["reqs"]])
        asked = self.client.get("/api/profile").json()["asked"]
        self.assertEqual([asked.get(name, 0) - asked_before.get(name, 0) for name in either], [1, 1])

    def test_status_and_notes_are_kept(self) -> None:
        self.client.patch(f"/api/jobs/{READ_POSTING}", json={"status": "applied", "notes": "Referral"}, headers=CHANGE)
        detail = self.client.get(f"/api/jobs/{READ_POSTING}").json()
        self.assertEqual((detail["status"], detail["notes"]), ("applied", "Referral"))
        wrong = self.client.patch(f"/api/jobs/{READ_POSTING}", json={"status": "hired"}, headers=CHANGE)
        self.assertEqual(wrong.status_code, 400)

    def test_a_pasted_posting_is_added_once_and_a_scrap_of_text_is_refused(self) -> None:
        self.assertEqual(self.client.post("/api/jobs", json={"source": "Python"}, headers=CHANGE).status_code, 400)
        first = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()
        again = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()
        self.assertEqual((first["created"], again["created"]), (True, False))
        job = first["job"]
        self.assertEqual(
            (job["title"], job["place"], job["spons"], job["status"], job["read"]),
            ("Security Engineer, Test Posting", "Portland, OR", "yes", "added", False),
        )
        self.assertEqual(job["needs"], ["requirements", "mapping"])

    def saved_records(self) -> tuple[Any, Any]:
        """The read posting's saved reading and mapping, from the test's copy of the data."""
        posting = store.load_posting(READ_POSTING, self.data / "postings")
        assert posting is not None
        saved_reading = store.load_reading(posting["text"], posting_id=READ_POSTING, directory=self.data / "readings")
        saved_mapping = store.load_mapping(posting["text"], directory=self.data / "mappings")
        assert saved_reading is not None and saved_mapping is not None
        return saved_reading, saved_mapping

    def test_postings_read_together_share_one_call_2(self) -> None:
        saved_reading, saved_mapping = self.saved_records()
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        sizes: list[int] = []

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            time.sleep(0.3)
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 0.3}

        def fake_many(postings: list[Any], vocabulary: dict[str, str]) -> list[Any]:
            sizes.append(len(postings))
            return [dict(saved_mapping) for _posting in postings]

        self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 3 has tests of its own
        self.app.state.jobs.classifier = "opus"  # with Jev, call 2 is never batched
        ids = [
            self.client.post("/api/jobs", json={"source": f"{PASTED}\n- Posting {number}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            for number in range(3)
        ]
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_many", fake_many),
            mock.patch.object(capability_mapping, "map_requirements", mock.Mock(side_effect=AssertionError("single"))),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": ids}, headers=CHANGE).json()["started"], ids)
            ends = [self.wait_for_the_end(heard, job_id) for job_id in ids]
        self.assertEqual(sizes, [3])
        self.assertEqual([end["type"] for end in ends], ["read", "read", "read"])

    def reading_stand_ins(self) -> tuple[Any, Any]:
        """Call 1 and call 2 answering the read posting's saved records, whatever posting they are given."""
        saved_reading, saved_mapping = self.saved_records()

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 1.0}

        def fake_map(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            return dict(saved_mapping)

        return fake_read, fake_map

    @staticmethod
    def fake_choose(asked: list[str]) -> Any:
        """Opus's call 3 stood in: the first four listed projects, recorded in `asked` by title."""

        def choose(title: str, text: str, library: Any, call: object = None) -> Any:
            asked.append(title)
            return {
                "projects": project_choice.listed_projects(library)[:4],
                "why": "A stand-in choice.",
                "valid": True,
                "seconds": 5.5,
                "projects_sha256_16": project_choice.projects_hash(library),
                "format": store.CHOICE_FORMAT,
                "model": "claude-opus-5",
                "answered_by": "claude-opus-5",
                "fallback": None,
            }

        return choose

    @contextmanager
    def jev_stand_in(self, call_3: Callable[[dict[str, Any]], tuple[int, Any]] | None = None) -> Iterator[list[str]]:
        """Jev stood in; yields the calls asked of it, in order, with the thread each call 3 request ran on.

        Call 2 answers the read posting's saved mapping as Jev's, so pages are those of the saved mapping. Call 3 is
        answered at the request, the listed projects in their order, or by `call_3(body)`; the key is a stand-in.
        """
        _reading, saved_mapping = self.saved_records()
        asked: list[str] = []

        def call_2(title: str, found: list[Any], vocabulary: dict[str, str], **options: Any) -> Any:
            asked.append("call 2")
            # Asked with the library's capability descriptions, as Jev's own record says.
            described = descriptions_hash(options["descriptions"])
            return {
                **saved_mapping,
                "model": jev.MODEL,
                "answered_by": "jev-test",
                "fallback": None,
                "descriptions_sha256_16": described,
            }

        def request(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            asked.append(f"call 3 on {threading.current_thread().name}")
            if call_3 is not None:
                return call_3(body)
            listed = list(body["state"]["candidate_projects"])
            answers = {project: {"type": "noul", "noul": 0.9 - 0.01 * number} for number, project in enumerate(listed)}
            return 200, {"answers": answers, "model": "jev-test", "usage": {"input_tokens": 50, "output_tokens": 5}}

        with (
            mock.patch.object(jev_mapping, "ask", call_2),
            mock.patch.object(jev, "_httpx_post", request),
            mock.patch.object(jev, "stored_key", return_value="test-key-not-real"),
        ):
            yield asked

    def read_pasted(
        self,
        modes: dict[str, str],
        choose: Any,
        fake_read: Any = None,
        jev_call_3: Callable[[dict[str, Any]], tuple[int, Any]] | None = None,
        heard: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any], list[Any]]:
        """Add the pasted posting and read it under these modes: (its id, the last event, the stages heard).

        Opus's calls and Jev's are stood in; what was asked of Jev is kept in `self.jev_asked`. `heard`, when given,
        hears the events.
        """
        self.assertEqual(self.client.post("/api/modes", json=modes, headers=CHANGE).status_code, 200)
        heard = [] if heard is None else heard
        self.app.state.background.events.publish = heard.append
        stand_in_read, fake_map = self.reading_stand_ins()
        job_id = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
        with (
            mock.patch.object(requirements, "read_requirements", fake_read or stand_in_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", choose),
            self.jev_stand_in(jev_call_3) as self.jev_asked,
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{job_id}/read", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
        stages = [event.get("stage") for event in heard if event.get("id") == job_id and event["type"] == "stage"]
        return job_id, end, stages

    def test_with_opus_hybrid_mode_makes_call_3_after_a_low_match_page_and_builds_from_its_projects(self) -> None:
        self.app.state.jobs.classifier = "opus"
        asked: list[str] = []
        job_id, end, stages = self.read_pasted({"pages": "hybrid"}, self.fake_choose(asked))
        self.assertEqual(self.jev_asked, [])
        self.assertEqual(stages, ["queued", "call1", "call2", "building", "call3", "building"])
        self.assertEqual(end, {"type": "read", "id": job_id, "calls": ["requirements", "mapping", "projects"]})
        self.assertEqual(len(asked), 1)
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(
            (detail["projects_by"], detail["choice_needed"], detail["page_current"]), ("model", False, True)
        )
        self.assertEqual(detail["choice"]["why"], "A stand-in choice.")
        self.assertLess(detail["engine_match"], DEFAULT_WEIGHTS.model_projects_below)
        # A second read makes no call: the choice is saved with the posting.
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/rebuild", headers=CHANGE).json()["projects_by"], "model")

    def test_legacy_mode_never_makes_call_3_and_hybrid_then_asks_for_it(self) -> None:
        asked: list[str] = []
        job_id, end, stages = self.read_pasted({"pages": "legacy"}, self.fake_choose(asked))
        # The reading makes the calls the posting lacks, reports each step, builds the page and moves the job to "read".
        self.assertEqual(stages, ["queued", "call1", "call2", "building"])
        self.assertEqual((end, asked), ({"type": "read", "id": job_id, "calls": ["requirements", "mapping"]}, []))
        self.assertEqual(self.jev_asked, ["call 2"])
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(
            (detail["read"], detail["status"], detail["page_current"], detail["projects_by"], detail["choice_needed"]),
            (True, "read", True, None, False),
        )
        self.assertGreater(len(printed_paragraphs(self.data / "pages" / f"{job_id}.docx")), 0)
        # Switched to hybrid, the page is built again with no call, and says call 3 is wanted.
        self.assertEqual(
            self.client.post("/api/modes", json={"pages": "hybrid"}, headers=CHANGE).json()["modes"]["pages"], "hybrid"
        )
        listed = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}
        self.assertFalse(listed[job_id]["page_current"])
        rebuilt = self.client.post(f"/api/jobs/{job_id}/rebuild", headers=CHANGE).json()
        self.assertEqual((rebuilt["projects_by"], rebuilt["choice_needed"], asked), ("engine", True, []))

    def test_read_all_makes_call_3_for_a_read_page_that_wants_it(self) -> None:
        asked: list[str] = []
        job_id, _end, _stages = self.read_pasted({"pages": "legacy"}, self.fake_choose(asked))
        self.client.post("/api/modes", json={"pages": "hybrid"}, headers=CHANGE)
        self.assertTrue(self.client.post(f"/api/jobs/{job_id}/rebuild", headers=CHANGE).json()["choice_needed"])
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        # The reading and mapping are saved, so the reading makes call 3 only, on Jev: calls 1 and 2 would reach the
        # guard, and so would Opus's call 3.
        with (
            mock.patch.object(project_choice, "choose_projects", self.fake_choose(asked)),
            self.jev_stand_in() as jev_asked,
        ):
            started = self.client.post("/api/read", json={"ids": [job_id]}, headers=CHANGE).json()["started"]
            end = self.wait_for_the_end(heard, job_id)
        self.assertEqual((started, end["type"], end["calls"], asked), ([job_id], "read", ["projects"], []))
        self.assertEqual([call.split(" on ")[0] for call in jev_asked], ["call 3"])
        self.assertFalse(self.client.get(f"/api/jobs/{job_id}").json()["choice_needed"])

    def test_a_refused_hybrid_page_makes_no_call_3_for_the_older_page_left_on_disk(self) -> None:
        from tailor_dashboard.jobs import is_low

        asked: list[str] = []
        job_id, _end, _stages = self.read_pasted({"pages": "legacy"}, self.fake_choose(asked))
        self.assertTrue(is_low(self.app.state.jobs.page(job_id)))
        # With Jev, call 3 starts beside call 1 whatever the page turns out to be; this is Opus's rule.
        self.app.state.jobs.classifier = "opus"
        self.client.post("/api/modes", json={"pages": "hybrid"}, headers=CHANGE)
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        fake_read, fake_map = self.reading_stand_ins()
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", self.fake_choose(asked)),
            mock.patch("tailor_dashboard.jobs.build_hybrid_page", side_effect=Refused("too little of the posting")),
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{job_id}/read-again", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
        self.assertEqual((end["type"], end["calls"], asked), ("read", ["requirements", "mapping"], []))
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["refused"], "too little of the posting")

    def test_opus_call_3_runs_on_the_call_3_pool(self) -> None:
        self.app.state.jobs.classifier = "opus"
        asked: list[str] = []
        choose = self.fake_choose(asked)
        threads: list[str] = []

        def named_choose(title: str, text: str, library: Any, call: object = None) -> Any:
            threads.append(threading.current_thread().name)
            return choose(title, text, library)

        _job_id, end, _stages = self.read_pasted({"pages": "hybrid"}, named_choose)
        self.assertEqual((end["calls"], len(threads)), (["requirements", "mapping", "projects"], 1))
        self.assertTrue(threads[0].startswith("call3"), threads[0])

    def test_an_early_call_3_that_fails_after_its_reading_failed_is_logged(self) -> None:
        call_3_started, reading_failed = threading.Event(), threading.Event()

        def failing_read(text: str) -> Any:
            self.assertTrue(call_3_started.wait(10), "call 3 did not start beside call 1")
            raise RuntimeError("call 1 did not answer")

        def failing_call_3(body: dict[str, Any]) -> tuple[int, Any]:
            call_3_started.set()
            reading_failed.wait(10)  # still running when the reading fails
            answers = {project: {"noul": 0.5} for project in body["state"]["candidate_projects"]}
            return 200, {"answers": answers, "model": "jev-test"}

        # Jev answers, and saving its answer fails: neither Jev's failure nor the guard's, so logged in full.
        with (
            mock.patch.object(store, "save_choice", side_effect=OSError("the disk is full")),
            self.assertLogs("tailor_dashboard.background", "ERROR"),
            self.assertLogs("tailor_dashboard.jobs", "ERROR") as logged,
        ):
            _job_id, end, _stages = self.read_pasted(
                {"pages": "hybrid"}, self.fake_choose([]), failing_read, jev_call_3=failing_call_3
            )
            reading_failed.set()
            _wait_until(lambda: len(logged.records) > 0)
        self.assertEqual((end["type"], logged.records[0].getMessage()), ("read-failed", "an early call 3 failed"))
        self.assertIn("the disk is full", str(logged.records[0].exc_info))

    def test_an_early_call_3_the_privacy_guard_stops_is_logged_as_one_line(self) -> None:
        # A copy with no fingerprints file refuses every call 3; the reading and its page go on.
        missing = mock.patch.object(privacy, "FINGERPRINTS_FILE", self.data / "no-fingerprints.txt")
        with (
            missing,
            mock.patch("tailor_dashboard.jobs.is_low", return_value=False),
            self.assertLogs("tailor_dashboard.jobs", "WARNING") as logged,
        ):
            _job_id, end, _stages = self.read_pasted({"pages": "hybrid"}, self.fake_choose([]))
            _wait_until(lambda: len(logged.records) > 0)
        self.assertEqual((end["type"], self.jev_asked), ("read", ["call 2"]))
        self.assertEqual(len(logged.output), 1)
        self.assertRegex(
            logged.output[0], "^WARNING:tailor_dashboard.jobs:Jev's early call 3 was not sent: .* is missing; nothing"
        )
        self.assertIsNone(logged.records[0].exc_info)

    def test_jev_call_3_starts_beside_call_1_on_its_own_pool_and_opus_is_not_asked(self) -> None:
        asked: list[str] = []
        call_3_started = threading.Event()
        stand_in_read, _fake_map = self.reading_stand_ins()

        def answer_call_3(body: dict[str, Any]) -> tuple[int, Any]:
            call_3_started.set()
            answers = {project: {"noul": 0.5} for project in body["state"]["candidate_projects"]}
            return 200, {"answers": answers, "model": "jev-test"}

        def read_after_call_3(text: str) -> tuple[Any, Any, Any]:
            # Call 1 finishes only once call 3 has started: were call 3 made after the page, this would wait in vain.
            self.assertTrue(call_3_started.wait(10), "call 3 did not start beside call 1")
            answer: tuple[Any, Any, Any] = stand_in_read(text)
            return answer

        held: list[int] = []
        slot = Held()

        def holding_call_3(body: dict[str, Any]) -> tuple[int, Any]:
            held.append(slot.depth)
            return answer_call_3(body)

        with mock.patch.object(self.app.state.background, "_jev", slot):
            job_id, end, _stages = self.read_pasted(
                {"pages": "hybrid"}, self.fake_choose(asked), read_after_call_3, jev_call_3=holding_call_3
            )
        self.assertEqual((end["type"], end["calls"], asked), ("read", ["requirements", "mapping", "projects"], []))
        self.assertEqual(held, [1])  # the request held the cap on Jev's requests in flight
        self.assertEqual([call.split(" on ")[0] for call in self.jev_asked], ["call 3", "call 2"])
        self.assertTrue(self.jev_asked[0].split(" on ")[1].startswith("jev-call3"), self.jev_asked[0])
        choice = store.load_choice(self.app.state.jobs.posting(job_id)["text"], self.data / "choices")
        assert choice is not None
        self.assertEqual((choice["model"], choice["answered_by"], choice["fallback"]), ("jev-1.13.0", "jev-test", None))
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(detail["projects_by"], "model")
        self.assertEqual(detail["choice_by"], {"by": "Jev", "fallback": None})

    def test_a_page_already_built_from_jev_s_choice_is_not_built_again(self) -> None:
        stand_in_read, _fake_map = self.reading_stand_ins()

        def read_after_the_choice_is_saved(text: str) -> tuple[Any, Any, Any]:
            _wait_until(lambda: store.load_choice(text, self.data / "choices") is not None)
            answer: tuple[Any, Any, Any] = stand_in_read(text)
            return answer

        job_id, end, stages = self.read_pasted(
            {"pages": "hybrid"}, self.fake_choose([]), read_after_the_choice_is_saved
        )
        # Jev's call 3 answered during call 1: no call 3 to wait for, and the first page is the page.
        self.assertEqual(stages, ["queued", "call1", "call2", "building"])
        self.assertEqual(end["calls"], ["requirements", "mapping", "projects"])
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual((detail["projects_by"], detail["page_current"]), ("model", True))

    def test_a_page_built_while_jev_s_call_3_runs_waits_for_it_and_is_built_again(self) -> None:
        heard = Heard()
        asked: list[str] = []

        def answer_after_the_wait_is_shown(body: dict[str, Any]) -> tuple[int, Any]:
            # Jev answers only once the page shows call 3 being waited for; an error here would reach Opus instead.
            if not heard.call3.wait(10):
                return 503, None
            answers = {project: {"noul": 0.5} for project in body["state"]["candidate_projects"]}
            return 200, {"answers": answers, "model": "jev-test"}

        job_id, end, stages = self.read_pasted(
            {"pages": "hybrid"}, self.fake_choose(asked), jev_call_3=answer_after_the_wait_is_shown, heard=heard
        )
        self.assertEqual(stages, ["queued", "call1", "call2", "building", "call3", "building"])
        self.assertEqual((end["calls"], asked), (["requirements", "mapping", "projects"], []))
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual((detail["projects_by"], detail["page_current"]), ("model", True))
        self.assertEqual(detail["choice_by"], {"by": "Jev", "fallback": None})

    def test_where_jev_cannot_choose_opus_does_only_for_a_low_page(self) -> None:
        asked: list[str] = []

        def refused(body: dict[str, Any]) -> tuple[int, Any]:
            return 400, {"error": "a bad request"}

        job_id, end, stages = self.read_pasted({"pages": "hybrid"}, self.fake_choose(asked), jev_call_3=refused)
        self.assertEqual((end["calls"], len(asked)), (["requirements", "mapping", "projects"], 1))
        # Opus's call 3 is waited for, once, and its page built.
        self.assertEqual(stages, ["queued", "call1", "call2", "building", "call3", "building"])
        choice = store.load_choice(self.app.state.jobs.posting(job_id)["text"], self.data / "choices")
        assert choice is not None
        self.assertEqual(
            choice["fallback"],
            {"original_model": "jev-1.13.0", "fallback_model": "claude-opus-5", "api_refusal_category": None},
        )
        self.assertEqual(choice["first_errors"], ["Jev: TypeSafe answered HTTP 400"])
        # Jev was asked beside call 1, so none of its time is added to Opus's 5.5 s.
        self.assertEqual(choice["seconds"], 5.5)
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(detail["projects_by"], "model")
        self.assertEqual(
            detail["choice_by"], {"by": "Opus 5", "fallback": "Jev could not answer: TypeSafe answered HTTP 400"}
        )
        # A page whose match is not low needs no choice: Opus is not asked, and Jev's failure is logged, key-free.
        asked.clear()
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        fake_read, fake_map = self.reading_stand_ins()
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", self.fake_choose(asked)),
            mock.patch("tailor_dashboard.jobs.is_low", return_value=False),
            self.jev_stand_in(refused),
            self.assertLogs("tailor_dashboard.jobs", "WARNING") as logged,
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{job_id}/read-again", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
            _wait_until(lambda: len(logged.records) > 0)
        self.assertEqual((end["calls"], asked), (["requirements", "mapping"], []))
        self.assertEqual(
            logged.output,
            ["WARNING:tailor_dashboard.jobs:Jev's early call 3 was not answered: TypeSafe answered HTTP 400"],
        )
        self.assertIsNone(logged.records[0].exc_info)

    def test_opus_in_jev_s_place_does_not_wait_behind_other_postings_hung_jev_call_3s(self) -> None:
        from tailor_dashboard.background import CHOICES_AT_ONCE

        asked: list[str] = []
        choose = self.fake_choose(asked)
        opus_asked = threading.Event()
        released: list[bool] = []

        def opus_choose(title: str, text: str, library: Any, call: object = None) -> Any:
            opus_asked.set()
            return choose(title, text, library)

        def call_3(body: dict[str, Any]) -> tuple[int, Any]:
            # TypeSafe refuses posting 0 and hangs on the others, as many as the call 3 pool has threads, until Opus has
            # been asked for posting 0's low page.
            if body["state"]["job_posting"]["text"].rstrip().endswith("Posting 0"):
                return 400, {"error": "a bad request"}
            released.append(opus_asked.wait(15))
            answers = {project: {"noul": 0.5} for project in body["state"]["candidate_projects"]}
            return 200, {"answers": answers, "model": "jev-test"}

        self.client.post("/api/modes", json={"pages": "hybrid"}, headers=CHANGE)
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        ids = [
            self.client.post("/api/jobs", json={"source": f"{PASTED}\n- Posting {number}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            for number in range(1 + CHOICES_AT_ONCE)
        ]
        fake_read, fake_map = self.reading_stand_ins()
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", opus_choose),
            self.jev_stand_in(call_3),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": ids}, headers=CHANGE).json()["started"], ids)
            ends = [self.wait_for_the_end(heard, job_id) for job_id in ids]
        self.assertEqual([end["type"] for end in ends], ["read"] * len(ids))
        self.assertEqual((released, len(asked)), ([True] * CHOICES_AT_ONCE, 1))

    def test_the_early_call_3_pool_has_a_thread_for_each_jev_request_in_flight(self) -> None:
        from tailor_dashboard.background import JEV_AT_ONCE

        asked: list[str] = []
        choose = self.fake_choose(asked)
        opus_asked = threading.Event()
        hanging: list[str] = []
        released: list[bool] = []

        def opus_choose(title: str, text: str, library: Any, call: object = None) -> Any:
            opus_asked.set()
            return choose(title, text, library)

        def call_3(body: dict[str, Any]) -> tuple[int, Any]:
            # TypeSafe hangs on every posting but the last, until Opus has been asked for the last one's low page, which
            # TypeSafe refuses. A pool one thread short would queue the last one's early call behind the hung ones.
            if body["state"]["job_posting"]["text"].rstrip().endswith("Refused"):
                return 400, {"error": "a bad request"}
            hanging.append(body["state"]["job_posting"]["text"][-9:])
            released.append(opus_asked.wait(15))
            answers = {project: {"noul": 0.5} for project in body["state"]["candidate_projects"]}
            return 200, {"answers": answers, "model": "jev-test"}

        self.client.post("/api/modes", json={"pages": "hybrid"}, headers=CHANGE)
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append

        def added(line: str) -> str:
            job_id: str = self.client.post("/api/jobs", json={"source": f"{PASTED}\n- {line}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            return job_id

        hung = [added(f"Posting {number}") for number in range(JEV_AT_ONCE - 1)]
        refused = added("Refused")
        fake_read, fake_map = self.reading_stand_ins()
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", opus_choose),
            self.jev_stand_in(call_3),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": hung}, headers=CHANGE).json()["started"], hung)
            _wait_until(lambda: len(hanging) == JEV_AT_ONCE - 1)
            self.assertTrue(self.client.post(f"/api/jobs/{refused}/read", headers=CHANGE).json()["started"])
            ends = [self.wait_for_the_end(heard, job_id) for job_id in [*hung, refused]]
        self.assertEqual([end["type"] for end in ends], ["read"] * JEV_AT_ONCE)
        self.assertEqual((released, len(asked)), ([True] * (JEV_AT_ONCE - 1), 1))

    def test_jev_s_call_2_is_asked_with_the_dashboard_library_s_descriptions_and_is_stale_once_one_changes(
        self,
    ) -> None:
        # The dashboard's own library: a copy with the first capability's description edited.
        library = self.data / "library"
        shutil.copytree(EXAMPLE_LIBRARY, library)
        described = library / "capability-descriptions.toml"
        original = described.read_text(encoding="utf-8")
        described.write_text(original.replace('what = "', 'what = "EDITED ', 1), encoding="utf-8")
        saved_reading, _saved_mapping = self.saved_records()
        sent: list[dict[str, Any]] = []

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 0.1}

        def request(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            sent.append(body)
            answers: dict[str, Any] = {}
            for name, question in body["questions"].items():
                if question["type"] == "choice":
                    answers[name] = {"probabilities": {next(iter(question["criteria"])): 0.9}}
                else:
                    answers[name] = {"noul": 0.1}
            return 200, {"answers": answers, "model": "jev-test"}

        app = create_app(Paths.under(self.data, library), hosts={"testserver"}, classifier="jev")
        refused = mock.Mock(side_effect=AssertionError("Opus"))
        heard: list[dict[str, Any]] = []
        with (
            TestClient(app) as client,
            mock.patch.object(app.state.background, "rebuild_stale"),
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(jev, "_httpx_post", request),
            mock.patch.object(jev, "stored_key", return_value="test-key-not-real"),
            mock.patch.object(capability_mapping, "map_requirements", refused),
            mock.patch.object(capability_mapping, "map_many", refused),
        ):
            app.state.background.events.publish = heard.append
            client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 2 alone
            job_id = client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
            self.assertTrue(client.post(f"/api/jobs/{job_id}/read", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
            # Read again as it is: the mapping is current under the dashboard's library, so no call is made.
            heard.clear()
            self.assertTrue(client.post(f"/api/jobs/{job_id}/read", headers=CHANGE).json()["started"])
            again = self.wait_for_the_end(heard, job_id)
            jobs = app.state.jobs
            needed = [jobs.calls_needed(jobs.posting(job_id))]
            # The description put back as it was: Jev's mapping is stale, and the saved one Opus made is not.
            described.write_text(original, encoding="utf-8")
            needed += [jobs.calls_needed(jobs.posting(job_id)), jobs.calls_needed(jobs.posting(READ_POSTING))]
        self.assertEqual((end["type"], end["calls"], again["calls"]), ("read", ["requirements", "mapping"], []))
        choices = [question for body in sent for question in body["questions"].values() if question["type"] == "choice"]
        self.assertEqual(
            {
                sum(entry.get("what", "").startswith("EDITED ") for entry in choice["criteria"].values())
                for choice in choices
            },
            {1},
        )
        self.assertEqual(needed, [[], ["mapping"], []])

    def test_jev_call_2_goes_at_once_several_at_a_time_up_to_the_cap_and_never_in_a_batch(self) -> None:
        saved_reading, _saved_mapping = self.saved_records()
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        lock = threading.Lock()
        in_flight, most = [0], [0]

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 0.1}

        def request(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            with lock:
                in_flight[0] += 1
                most[0] = max(most[0], in_flight[0])
            time.sleep(0.3)
            with lock:
                in_flight[0] -= 1
            answers: dict[str, Any] = {}
            for name, question in body["questions"].items():
                if question["type"] == "choice":
                    answers[name] = {"probabilities": {next(iter(question["criteria"])): 0.9}}
                else:
                    answers[name] = {"noul": 0.1}
            return 200, {"answers": answers, "model": "jev-test"}

        self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 2 alone
        ids = [
            self.client.post("/api/jobs", json={"source": f"{PASTED}\n- Posting {number}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            for number in range(3)
        ]
        refused = mock.Mock(side_effect=AssertionError("Opus or a batch"))
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(jev, "_httpx_post", request),
            mock.patch.object(jev, "stored_key", return_value="test-key-not-real"),
            mock.patch.object(capability_mapping, "map_many", refused),
            mock.patch.object(capability_mapping, "map_requirements", refused),
            mock.patch("tailor_dashboard.background.MappingBatcher.map", refused),
            # The cap, two here, so three postings show it holds.
            mock.patch.object(self.app.state.background, "_jev", threading.BoundedSemaphore(2)),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": ids}, headers=CHANGE).json()["started"], ids)
            ends = [self.wait_for_the_end(heard, job_id) for job_id in ids]
        self.assertEqual([end["type"] for end in ends], ["read", "read", "read"])
        self.assertEqual(most[0], 2)
        mappings = [self.app.state.jobs.records(self.app.state.jobs.posting(job_id))[1] for job_id in ids]
        self.assertEqual({(mapping["model"], mapping.get("batch")) for mapping in mappings}, {("jev-1.13.0", None)})

    def test_a_posting_jev_cannot_map_goes_to_opus_s_batches_and_alone_at_once(self) -> None:
        _saved_reading, saved_mapping = self.saved_records()
        threads: list[str] = []

        def single(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            threads.append(threading.current_thread().name)
            return dict(saved_mapping)

        def refused(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            return 400, {"error": "a bad request"}

        self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 2 alone
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        fake_read, _fake_map = self.reading_stand_ins()
        job_id = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(jev, "_httpx_post", refused),
            mock.patch.object(jev, "stored_key", return_value="test-key-not-real"),
            mock.patch.object(capability_mapping, "map_requirements", single),
            mock.patch.object(capability_mapping, "map_many", mock.Mock(side_effect=AssertionError("a batch"))),
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{job_id}/read", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
        self.assertEqual(end["type"], "read")
        # Answered on the batcher's own thread, as a batch of one: nothing else was being read.
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].startswith("mapping"), threads[0])
        mapping = self.app.state.jobs.records(self.app.state.jobs.posting(job_id))[1]
        self.assertEqual(mapping["fallback"]["original_model"], "jev-1.13.0")
        self.assertEqual(mapping["first_errors"][0], "Jev: TypeSafe answered HTTP 400")

    def test_a_posting_jev_cannot_map_does_not_wait_for_another_posting_s_jev_call_2(self) -> None:
        saved_reading, saved_mapping = self.saved_records()
        opus_mapped = threading.Event()
        requests: list[str] = []
        released: list[bool] = []
        # A posting's requirements go five to a request, and their words in one more, its requests at once.
        per_posting = -(-len(saved_reading["requirements"]) // jev_mapping.PER_REQUEST) + 1

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            # Jev refuses posting 0 while posting 1 is at call 1, so posting 0 waits in Opus's batches for posting 1.
            time.sleep(0.1 + 0.5 * int(text.rstrip()[-1]))
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 0.3}

        def request(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
            requests.append(threading.current_thread().name)
            if len(requests) <= per_posting:  # posting 0's, all sent before posting 1 has finished call 1
                return 400, {"error": "a bad request"}
            released.append(opus_mapped.wait(10))  # posting 1's request hangs until Opus has mapped posting 0
            answers: dict[str, Any] = {}
            for name, question in body["questions"].items():
                if question["type"] == "choice":
                    answers[name] = {"probabilities": {next(iter(question["criteria"])): 0.9}}
                else:
                    answers[name] = {"noul": 0.1}
            return 200, {"answers": answers, "model": "jev-test"}

        def single(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            opus_mapped.set()
            return dict(saved_mapping)

        self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 2 alone
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        ids = [
            self.client.post("/api/jobs", json={"source": f"{PASTED}\n- Posting {number}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            for number in range(2)
        ]
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(jev, "_httpx_post", request),
            mock.patch.object(jev, "stored_key", return_value="test-key-not-real"),
            mock.patch.object(capability_mapping, "map_requirements", single),
            mock.patch.object(capability_mapping, "map_many", mock.Mock(side_effect=AssertionError("a batch"))),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": ids}, headers=CHANGE).json()["started"], ids)
            ends = [self.wait_for_the_end(heard, job_id) for job_id in ids]
        self.assertEqual([end["type"] for end in ends], ["read", "read"])
        self.assertEqual((per_posting > 1, released), (True, [True] * per_posting))
        mappings = [self.app.state.jobs.records(self.app.state.jobs.posting(job_id))[1] for job_id in ids]
        self.assertEqual(
            [(mapping["model"] == "jev-1.13.0", bool(mapping["fallback"])) for mapping in mappings],
            [(False, True), (True, False)],
        )

    def test_with_no_key_stored_every_posting_goes_to_opus_in_batches(self) -> None:
        saved_reading, saved_mapping = self.saved_records()
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        sizes: list[int] = []

        def fake_read(text: str) -> tuple[Any, Any, Any]:
            # Call 1 ends 0.2 s apart: each posting Jev cannot map waits for those still at call 1, as in Opus mode.
            time.sleep(0.1 + 0.2 * int(text.rstrip()[-1]))
            return saved_reading["requirements"], saved_reading["keywords"], {"seconds": 0.3}

        def fake_many(postings: list[Any], vocabulary: dict[str, str]) -> list[Any]:
            sizes.append(len(postings))
            return [dict(saved_mapping) for _posting in postings]

        self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE)  # call 3 has tests of its own
        ids = [
            self.client.post("/api/jobs", json={"source": f"{PASTED}\n- Posting {number}"}, headers=CHANGE).json()[
                "job"
            ]["id"]
            for number in range(3)
        ]
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(keyring, "get_password", return_value=None),
            mock.patch.object(capability_mapping, "map_many", fake_many),
            mock.patch.object(capability_mapping, "map_requirements", mock.Mock(side_effect=AssertionError("single"))),
        ):
            self.assertEqual(self.client.post("/api/read", json={"ids": ids}, headers=CHANGE).json()["started"], ids)
            ends = [self.wait_for_the_end(heard, job_id) for job_id in ids]
        self.assertEqual(sizes, [3])
        self.assertEqual([end["type"] for end in ends], ["read", "read", "read"])
        mappings = [self.app.state.jobs.records(self.app.state.jobs.posting(job_id))[1] for job_id in ids]
        self.assertEqual(
            {(mapping["fallback"]["original_model"], mapping["first_errors"][0]) for mapping in mappings},
            {("jev-1.13.0", "Jev: no Jev key in the credential store")},
        )

    def test_jev_is_the_default_classifier_and_accuracy_the_default_for_call_2(self) -> None:
        from tailor_dashboard.jobs import Jobs
        from tailor_dashboard.page_modes import PageModes, PageModesFile

        paths = Paths.under(self.data, EXAMPLE_LIBRARY)
        for setting in settings.CLASSIFIERS:
            with mock.patch.object(settings, "CLASSIFIER", setting):
                self.assertEqual(create_app(paths, hosts={"testserver"}).state.jobs.classifier, setting)
        with self.assertRaisesRegex(ValueError, "one of jev, opus, not 'gpt'"):
            Jobs(paths, classifier="gpt")
        with (
            mock.patch.object(settings, "CALL2", "gpt"),
            self.assertRaisesRegex(ValueError, "one of jev, opus, not 'gpt'"),
        ):
            Jobs(paths)
        # With no modes saved, call 2 is on Accuracy: Opus. Opus as the classifier answers it whatever the mode says.
        jobs = self.app.state.jobs
        self.assertEqual(PageModesFile(self.data / "no-modes.json").get(), PageModes("hybrid", "opus"))
        self.assertEqual([jobs.call2_model(PageModes(call2=model)) for model in ("opus", "jev")], ["opus", "jev"])
        jobs.classifier = "opus"
        self.assertEqual(jobs.call2_model(PageModes(call2="jev")), "opus")
        jobs.classifier = "jev"
        # On Speed, as these tests set it, Jev answers call 2.
        job_id, end, _stages = self.read_pasted({"pages": "legacy"}, self.fake_choose([]))
        mapping = jobs.records(jobs.posting(job_id))[1]
        self.assertEqual(
            (end["calls"], mapping["model"], self.jev_asked), (["requirements", "mapping"], "jev-1.13.0", ["call 2"])
        )

    def test_on_accuracy_opus_answers_call_2_in_its_batches_and_jev_still_answers_call_3(self) -> None:
        asked: list[str] = []
        threads: list[str] = []
        _reading, saved_mapping = self.saved_records()

        def opus_map(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            threads.append(threading.current_thread().name)
            return dict(saved_mapping)

        fake_read, _fake_map = self.reading_stand_ins()
        with mock.patch.object(self, "reading_stand_ins", lambda: (fake_read, opus_map)):
            job_id, end, _stages = self.read_pasted({"pages": "hybrid", "call2": "opus"}, self.fake_choose(asked))
        mapping = self.app.state.jobs.records(self.app.state.jobs.posting(job_id))[1]
        self.assertEqual((end["type"], mapping["model"], asked), ("read", "claude-opus-5-5", []))
        # Answered on the batcher's own thread, as a batch of one; Jev was asked call 3 alone.
        self.assertEqual((len(threads), threads[0][:7]), (1, "mapping"))
        self.assertEqual([call.split(" on ")[0] for call in self.jev_asked], ["call 3"])

    def test_call_2_s_model_is_chosen_as_each_reading_starts_and_one_the_other_made_stays(self) -> None:
        jobs = self.app.state.jobs
        job_id, _end, _stages = self.read_pasted({"pages": "legacy", "call2": "jev"}, self.fake_choose([]))
        self.assertEqual(jobs.records(jobs.posting(job_id))[1]["model"], "jev-1.13.0")
        # Switched to Accuracy: Jev's mapping stays current, so the job is read, its page current, and reading it
        # makes no call.
        self.client.post("/api/modes", json={"call2": "opus"}, headers=CHANGE)
        listed = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}[job_id]
        self.assertEqual((listed["read"], listed["needs"], listed["page_current"]), (True, [], True))
        # Read again makes it on Opus, as the mode says now.
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        fake_read, fake_map = self.reading_stand_ins()
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            self.jev_stand_in() as jev_asked,
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{job_id}/read-again", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, job_id)
        self.assertEqual((end["calls"], jev_asked), (["requirements", "mapping"], []))
        self.assertEqual(jobs.records(jobs.posting(job_id))[1]["model"], "claude-opus-5-5")

    def test_a_jev_mapping_asked_with_other_descriptions_gives_the_page_no_fingerprint(self) -> None:
        jobs = self.app.state.jobs
        posting = jobs.posting(READ_POSTING)
        _reading, mapping = jobs.records(posting)
        library, _fingerprint = jobs.library_cache.get()
        by_jev: Any = {**mapping, "model": jev.MODEL, "descriptions_sha256_16": library.descriptions_hash}
        store.save_mapping(posting["text"], by_jev, jobs.paths.mappings)
        self.assertIsNotNone(jobs.fingerprint(posting))
        self.assertEqual(self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE).status_code, 200)
        # Asked with descriptions the library no longer has: no fingerprint, so the page is neither current nor built
        # again from it, and the job waits for call 2.
        asked_otherwise: Any = {**by_jev, "descriptions_sha256_16": "0" * 16}
        store.save_mapping(posting["text"], asked_otherwise, jobs.paths.mappings)
        self.assertIsNone(jobs.fingerprint(posting))
        self.assertFalse(jobs.page_is_current(READ_POSTING))
        listed = {job["id"]: job for job in self.client.get("/api/jobs").json()["jobs"]}[READ_POSTING]
        self.assertEqual((listed["read"], listed["needs"], listed["page_current"]), (False, ["mapping"], False))
        rebuilt = self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        self.assertEqual((rebuilt.status_code, rebuilt.json()["detail"]), (400, f"posting {READ_POSTING} is not read"))

    def test_modes_are_kept_and_an_unknown_one_is_refused(self) -> None:
        self.assertEqual(self.client.get("/api/jobs").json()["modes"], {"pages": "hybrid", "call2": "jev"})
        changed = self.client.post("/api/modes", json={"pages": "legacy"}, headers=CHANGE).json()["modes"]
        self.assertEqual(changed, {"pages": "legacy", "call2": "jev"})
        changed = self.client.post("/api/modes", json={"call2": "opus"}, headers=CHANGE).json()["modes"]
        self.assertEqual(changed, {"pages": "legacy", "call2": "opus"})
        again = create_app(Paths.under(self.data, EXAMPLE_LIBRARY), hosts={"testserver"})
        with TestClient(again) as client:
            self.assertEqual(client.get("/api/jobs").json()["modes"], changed)
        for unknown in ({"pages": "always"}, {"call2": "gpt"}):
            self.assertEqual(self.client.post("/api/modes", json=unknown, headers=CHANGE).status_code, 400)
        # An older open page may still send the priority it had.
        stale = self.client.post("/api/modes", json={"pages": "hybrid", "priority": "speed"}, headers=CHANGE)
        self.assertEqual(stale.status_code, 422)
        self.assertEqual(self.client.get("/api/jobs").json()["modes"], changed)
        self.assertEqual(self.client.post("/api/modes", json={"pages": "legacy"}).status_code, 403)

    def test_read_again_makes_every_call_again(self) -> None:
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        fake_read, fake_map = self.reading_stand_ins()
        asked: list[str] = []
        with (
            mock.patch.object(requirements, "read_requirements", fake_read),
            mock.patch.object(capability_mapping, "map_requirements", fake_map),
            mock.patch.object(project_choice, "choose_projects", self.fake_choose(asked)),
            self.jev_stand_in(),
        ):
            self.assertTrue(self.client.post(f"/api/jobs/{READ_POSTING}/read-again", headers=CHANGE).json()["started"])
            end = self.wait_for_the_end(heard, READ_POSTING)
        self.assertEqual(end["type"], "read")
        self.assertEqual(end["calls"][:2], ["requirements", "mapping"])

    def test_the_default_hosts_are_this_machine_by_address_and_by_name(self) -> None:
        app = create_app(Paths.under(self.data, EXAMPLE_LIBRARY))
        with TestClient(app, base_url="http://127.0.0.1:8770") as client:
            codes = {
                host: client.get("/", headers={"Host": host}).status_code
                for host in ("127.0.0.1:8770", "localhost:8770", "localhost", "127.0.0.1.evil.example", "evil.example")
            }
        self.assertEqual(
            codes,
            {
                "127.0.0.1:8770": 200,
                "localhost:8770": 200,
                "localhost": 200,
                "127.0.0.1.evil.example": 403,
                "evil.example": 403,
            },
        )

    def test_every_answer_carries_the_content_policy_and_the_list_the_reading_schedule(self) -> None:
        from tailor_dashboard.background import CHOICES_AT_ONCE, JEV_AT_ONCE, MAPPING_BATCH, READINGS_AT_ONCE

        for path in ("/", "/api/jobs", "/static/app.js"):
            policy = self.client.get(path).headers.get("content-security-policy", "")
            self.assertIn("script-src 'self';", policy)
            self.assertIn("frame-ancestors 'none'", policy)
            self.assertNotIn("https:", policy)  # nothing is loaded from another site
        for path in ("/", "/static/app.js", "/static/app.css"):
            self.assertEqual(self.client.get(path).headers.get("cache-control"), "no-cache")
        # Call 2 on Speed: a posting's call 2 is about four Jev requests, so the postings whose requests fit in
        # Jev's six in flight.
        schedule = self.client.get("/api/jobs").json()["schedule"]
        self.assertEqual(
            schedule,
            {"call1": READINGS_AT_ONCE, "batch": max(1, JEV_AT_ONCE // 4), "call3": JEV_AT_ONCE, "classifier": "jev"},
        )
        # On Accuracy, Opus's batch; call 3 stays on Jev.
        self.client.post("/api/modes", json={"call2": "opus"}, headers=CHANGE)
        schedule = self.client.get("/api/jobs").json()["schedule"]
        self.assertEqual(
            (schedule["batch"], schedule["call3"], schedule["classifier"]), (MAPPING_BATCH, JEV_AT_ONCE, "jev")
        )
        # Opus as the classifier answers both calls, whatever the mode says.
        self.client.post("/api/modes", json={"call2": "jev"}, headers=CHANGE)
        self.app.state.jobs.classifier = "opus"
        schedule = self.client.get("/api/jobs").json()["schedule"]
        self.assertEqual(
            (schedule["batch"], schedule["call3"], schedule["classifier"]), (MAPPING_BATCH, CHOICES_AT_ONCE, "opus")
        )

    def test_an_id_that_could_leave_the_postings_folder_is_refused(self) -> None:
        self.assertEqual(self.client.get("/api/jobs/..%5Cx").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/x%5C..%5C..%5Cconfig").status_code, 404)

    def test_a_build_that_fails_is_not_tried_again_until_its_inputs_change(self) -> None:
        self.rebuilds.stop()  # the list's rebuilds are what this test watches
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        attempts: list[str] = []

        def failing_build(posting_id: str) -> None:
            attempts.append(posting_id)
            raise ValueError("the template changed")

        def failures_heard(count: int) -> None:
            deadline = time.monotonic() + 30
            while sum(e["type"] == "build-failed" and e["id"] == READ_POSTING for e in heard) < count:
                self.assertLess(time.monotonic(), deadline, "the build did not fail in time")
                time.sleep(0.05)

        # The failed builds are logged as errors by design; captured here so a passing run prints no traceback.
        with (
            self.assertLogs("tailor_dashboard.background", level="ERROR"),
            mock.patch.object(self.app.state.jobs, "build", failing_build),
        ):
            self.client.get("/api/jobs")  # the saved pages are not built yet, so each read job's page is queued
            failures_heard(1)
            self.client.get("/api/jobs")  # the page asks for the list after a failure; nothing is queued again
            time.sleep(0.5)
            self.assertEqual(attempts.count(READ_POSTING), 1)
            with mock.patch.object(self.app.state.jobs, "page_inputs", return_value="changed"):
                self.client.get("/api/jobs")
                failures_heard(2)
        self.assertEqual(attempts.count(READ_POSTING), 2)

    def test_a_rebuild_that_fails_says_why(self) -> None:
        with mock.patch.object(self.app.state.jobs, "build", side_effect=ValueError("the template changed")):
            response = self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        self.assertEqual((response.status_code, response.json()["detail"]), (400, "the template changed"))

    def test_a_page_file_held_open_by_a_reader_is_replaced_once_it_is_let_go(self) -> None:
        jobs = self.app.state.jobs
        jobs.build(READ_POSTING)
        names = (jobs.page_path(READ_POSTING).name, jobs.word_path(READ_POSTING).name)
        replaced: list[str] = []
        replace = os.replace

        def held_once(source: Any, target: Any) -> None:
            # As on Windows while another thread reads the file: the first rename over the page and the Word file is
            # refused.
            name = Path(target).name
            if name in names:
                replaced.append(name)
                if replaced.count(name) == 1:
                    raise PermissionError(13, "Access is denied", str(target))
            replace(source, target)

        with mock.patch.object(os, "replace", held_once):
            self.assertIsNotNone(jobs.build(READ_POSTING))
        self.assertEqual(replaced, [names[0], names[0], names[1], names[1]])

    def test_a_page_being_replaced_is_read_once_it_is_let_go(self) -> None:
        jobs = self.app.state.jobs
        jobs.build(READ_POSTING)
        page_path, word_path = jobs.page_path(READ_POSTING), jobs.word_path(READ_POSTING)
        refused: list[str] = []
        read_text, zip_file = Path.read_text, zipfile.ZipFile

        def page_held_once(path: Path, *arguments: Any, **options: Any) -> str:
            # As on Windows while a rebuild renames a new page over it: the first open is refused.
            if path == page_path and "page" not in refused:
                refused.append("page")
                raise PermissionError(13, "Permission denied", str(path))
            return read_text(path, *arguments, **options)

        def word_held_once(path: Any, *arguments: Any, **options: Any) -> Any:
            if Path(path) == word_path and "word" not in refused:
                refused.append("word")
                raise PermissionError(13, "Permission denied", str(path))
            return zip_file(path, *arguments, **options)

        with (
            mock.patch.object(Path, "read_text", page_held_once),
            mock.patch.object(zipfile, "ZipFile", word_held_once),
        ):
            page = jobs.page(READ_POSTING)
            paragraphs = printed_paragraphs(word_path)
        self.assertEqual(refused, ["page", "word"])
        self.assertIsNotNone(page)
        self.assertGreater(len(paragraphs), 0)

    def test_an_edit_that_breaks_the_page_rule_names_the_rule(self) -> None:
        from tailor_dashboard.edits import edit_problem

        library, _fingerprint = self.app.state.jobs.library_cache.get()
        # I1.4 and I1.5 exclude each other.
        self.assertEqual(edit_problem(library, ["I1.4"], [], ["I1.5"]), "I1.5 cannot be on a page beside I1.4")
        six = ["I1.1", "G1.1", "G2.1", "G4.1", "G6.1", "U1.1"]
        self.assertEqual(
            edit_problem(library, six, [], ["U3.1"]),
            f"the page would hold {len(six) + 1} projects, at most {layout.PAGE_PROJECTS}",
        )
        many = [
            bullet.id
            for bullet in library.usable_bullets
            if bullet.parent in ("I1", "G1", "G6", "I2") and bullet.id != "I1.5"
        ]
        problem = edit_problem(library, many[:1], [], many[1:])
        self.assertIsNotNone(problem)
        self.assertRegex(problem or "", rf"^the page would not fit: \d+ project lines of {layout.PROJECT_BODY_LINES}$")
        self.assertEqual(edit_problem(library, ["I1.4"], [], []), "it changes nothing")
        self.assertEqual(edit_problem(library, ["I1.4"], ["I1.4"], []), "it leaves the page empty")
        self.assertEqual(edit_problem(library, ["I1.4"], [], ["Z9.9"]), "Z9.9 is not a written bullet in the library")

    def test_the_judge_is_not_called_when_the_page_holds_an_identity_word(self) -> None:
        from tailor_engine import privacy

        jobs = self.app.state.jobs
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        page = jobs.page(READ_POSTING)
        library, _fingerprint = jobs.library_cache.get()
        first = next(bullet for bullet in library.usable_bullets if bullet.id == page["selected"][0])
        word = max(privacy.words_of(first.text), key=len)  # a word the page prints
        asked: list[str] = []

        def model(system_prompt: str, text: str) -> Any:
            asked.append(text)
            return {}

        listed = self.data / "identity-fingerprints.txt"
        listed.write_text(privacy.fingerprint(word) + "\n", encoding="utf-8")
        with mock.patch.object(privacy, "FINGERPRINTS_FILE", listed), self.assertRaises(privacy.PrivacyError):
            review.review_job(jobs.posting(READ_POSTING), page, library, "brief", call=model)
        self.assertEqual(asked, [])

    def test_a_failed_reading_leaves_the_job_unread(self) -> None:
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append

        def failing_read(text: str) -> Any:
            raise RuntimeError("the model did not answer")

        job_id = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
        with (
            mock.patch.object(requirements, "read_requirements", failing_read),
            self.jev_stand_in(),
            self.assertLogs("tailor_dashboard.background", "ERROR") as logged,
        ):
            self.client.post(f"/api/jobs/{job_id}/read", headers=CHANGE)
            end = self.wait_for_the_end(heard, job_id)
        self.assertEqual(end, {"type": "read-failed", "id": job_id, "message": "the model did not answer"})
        self.assertIn("the model did not answer", logged.output[0] + str(logged.records[0].exc_info))
        detail = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual((detail["read"], detail["status"]), (False, "added"))

    def test_the_judge_reads_the_page_and_its_answer_is_kept(self) -> None:
        heard: list[dict[str, Any]] = []
        self.app.state.background.events.publish = heard.append
        sent: list[str] = []

        def fake_call(model: str, system_prompt: str, user_prompt: str) -> Any:
            sent.append(user_prompt)
            too_many = [{"gap": f"gap {number}", "note": "n"} for number in range(4)]
            gaps = too_many if len(sent) == 1 else too_many[:3]  # the first answer breaks the brief limit
            answer = {"verdict": "moderate", "summary": "s", "relevance": "r", "alignment": "a", "gaps": gaps}
            return {
                "json": {**answer, "better_fits": [], "edits": []},
                "input_tokens": 10,
                "output_tokens": 5,
                "seconds": 1.0,
            }

        no_page = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
        refused = self.client.post(f"/api/jobs/{no_page}/judge", json={"level": "brief"}, headers=CHANGE)
        self.assertEqual(refused.status_code, 400)
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        with mock.patch.object(review, "call_json", fake_call):
            asked = self.client.post(f"/api/jobs/{READ_POSTING}/judge", json={"level": "brief"}, headers=CHANGE)
            self.assertTrue(asked.json()["started"])
            deadline = time.monotonic() + 30
            while not any(event["type"] == "judged" for event in heard) and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual([event["type"] for event in heard], ["judging", "judged"])
        self.assertEqual(len(sent), 2, "an answer over the length's limits is asked for once more")
        self.assertIn("EACH REQUIREMENT ON THE PAGE:", sent[0])
        self.assertIn("LENGTH: brief", sent[0])
        kept = self.client.get(f"/api/jobs/{READ_POSTING}").json()["judge"]
        self.assertEqual((kept["level"], kept["answer"]["verdict"], kept["attempts"]), ("brief", "moderate", 2))
        self.assertEqual(len(kept["answer"]["gaps"]), 3)
        self.assertFalse(kept["page_changed"])
        # Each length's time is its kept answers' median, the measured one before any is kept.
        seconds = self.client.get(f"/api/jobs/{READ_POSTING}").json()["judge_seconds"]
        self.assertEqual(seconds, {"brief": kept["seconds"], "detailed": JUDGE_SECONDS_BEFORE_ANY["detailed"]})

    def test_a_job_can_be_renamed_and_the_names_reach_the_file_name_and_the_judge(self) -> None:
        job = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]
        self.assertEqual((job["company"], job["company_known"]), ("Pasted posting", False))
        change = {"title": "  Security   Engineer ", "company": "Acme Security"}
        renamed = self.client.patch(f"/api/jobs/{job['id']}", json=change, headers=CHANGE).json()["job"]
        self.assertEqual(
            (renamed["title"], renamed["company"], renamed["company_known"]),
            ("Security Engineer", "Acme Security", True),
        )
        too_long = self.client.patch(f"/api/jobs/{job['id']}", json={"company": "x" * 101}, headers=CHANGE)
        self.assertEqual(too_long.status_code, 400)
        # A read job's Word file and the judge take the names given.
        self.client.patch(f"/api/jobs/{READ_POSTING}", json={"company": "Acme Security"}, headers=CHANGE)
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        word = self.client.get(f"/api/jobs/{READ_POSTING}/word")
        self.assertIn("Acme%20Security.docx", word.headers["content-disposition"])
        sent: list[str] = []

        def fake_call(model: str, system_prompt: str, user_prompt: str) -> Any:
            sent.append(user_prompt)
            answer = {"verdict": "weak", "summary": "s", "relevance": "r", "alignment": "a", "gaps": []}
            return {
                "json": {**answer, "better_fits": [], "edits": []},
                "input_tokens": 1,
                "output_tokens": 1,
                "seconds": 1.0,
            }

        with mock.patch.object(review, "call_json", fake_call):
            self.app.state.jobs.judge(READ_POSTING, "brief")
        self.assertIn(" at Acme Security\n", sent[0].splitlines(keepends=True)[0])
        # An empty field returns to the posting's own name.
        back = self.client.patch(f"/api/jobs/{READ_POSTING}", json={"company": ""}, headers=CHANGE).json()["job"]
        self.assertEqual(back["company"], "Copperline Outdoor Co.")

    def test_a_removed_job_stays_off_the_list_until_its_posting_is_added_again(self) -> None:
        job_id = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()["job"]["id"]
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/remove", headers=CHANGE).status_code, 200)
        self.assertNotIn(job_id, [job["id"] for job in self.client.get("/api/jobs").json()["jobs"]])
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").status_code, 404)
        self.assertTrue((self.data / "postings" / f"{job_id}.json").exists(), "the saved posting stays")
        # A new start tracks every saved posting it does not know; a removed one it knows, so it stays off.
        again = create_app(Paths.under(self.data, EXAMPLE_LIBRARY), hosts={"testserver"})
        with TestClient(again) as client:
            self.assertNotIn(job_id, [job["id"] for job in client.get("/api/jobs").json()["jobs"]])
        added = self.client.post("/api/jobs", json={"source": PASTED}, headers=CHANGE).json()
        self.assertEqual((added["job"]["id"], added["created"]), (job_id, True))
        # A job being read cannot be removed.
        self.app.state.background.stages[job_id] = "call1"
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/remove", headers=CHANGE).status_code, 400)
        del self.app.state.background.stages[job_id]

    def a_fitting_swap(self) -> tuple[list[str], list[str]]:
        """One bullet off READ_POSTING's page and one from the library on, that fits: found, not assumed."""
        from tailor_dashboard.edits import edit_problem

        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        library, _fingerprint = self.app.state.jobs.library_cache.get()
        page = self.app.state.jobs.page(READ_POSTING)
        on_page = page["selected"]
        for bullet in library.usable_bullets:
            if bullet.id not in on_page and edit_problem(library, on_page, [on_page[0]], [bullet.id]) is None:
                return [on_page[0]], [bullet.id]
        raise self.failureException("no fitting swap on this page")

    def judge_with(self, edits: list[list[Any]]) -> list[str]:
        """Ask the judge (stood in) for a brief answer proposing `edits`, one list per attempt; the prompts sent."""
        sent: list[str] = []

        def fake_call(model: str, system_prompt: str, user_prompt: str) -> Any:
            sent.append(user_prompt)
            answer = {"verdict": "moderate", "summary": "s", "relevance": "r", "alignment": "a", "gaps": []}
            proposed = edits[min(len(sent), len(edits)) - 1]
            return {"json": {**answer, "better_fits": [], "edits": proposed}, "seconds": 1.0}

        with mock.patch.object(review, "call_json", fake_call):
            self.app.state.jobs.judge(READ_POSTING, "brief")
        return sent

    def test_a_judge_proposal_that_does_not_fit_is_asked_for_again(self) -> None:
        remove, add = self.a_fitting_swap()
        library, _fingerprint = self.app.state.jobs.library_cache.get()
        on_page = self.app.state.jobs.page(READ_POSTING)["selected"]
        off_page = next(bullet.id for bullet in library.usable_bullets if bullet.id not in on_page)
        sent = self.judge_with(
            [[{"remove": [off_page], "add": [], "why": "w"}], [{"remove": remove, "add": add, "why": "w"}]]
        )
        self.assertEqual(len(sent), 2)
        self.assertIn(f"edit 1 cannot be applied: {off_page} is not on the page", sent[1])
        self.assertIn(f"PAGE LINES: The projects section holds {layout.PROJECT_BODY_LINES} lines", sent[0])
        proposals = self.client.get(f"/api/jobs/{READ_POSTING}").json()["judge"]["proposals"]
        self.assertEqual([proposal["state"] for proposal in proposals], ["open"])
        self.assertTrue(proposals[0]["remove"][0]["text"], "a proposal is shown in words, not IDs")

    def test_an_accepted_edit_changes_the_page_survives_a_rebuild_and_can_be_undone(self) -> None:
        remove, add = self.a_fitting_swap()
        engine_page = list(self.app.state.jobs.page(READ_POSTING)["selected"])
        self.judge_with([[{"remove": remove, "add": add, "why": "fits the role better"}]])
        detail = self.client.post(f"/api/jobs/{READ_POSTING}/proposals/0", json={"accept": True}, headers=CHANGE).json()
        edited = self.app.state.jobs.page(READ_POSTING)
        self.assertEqual(sorted(edited["selected"]), sorted([*(set(engine_page) - set(remove)), *add]))
        self.assertEqual(edited["engine"]["selected"], engine_page)
        self.assertEqual(detail["judge"]["proposals"][0]["state"], "applied")
        self.assertEqual(len(detail["edits"]), 1)
        self.assertTrue(detail["edits"][0]["applies"])
        # Printed: the added bullet's text is in the Word file.
        library, _fingerprint = self.app.state.jobs.library_cache.get()
        added_text = next(bullet.text for bullet in library.bullets if bullet.id == add[0])
        printed = " ".join(text for paragraph in detail["printed"] for text, _bold in paragraph["runs"])
        self.assertIn(added_text[:40], printed)
        # A rebuild keeps it; answering the same proposal twice is refused.
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        self.assertEqual(sorted(self.app.state.jobs.page(READ_POSTING)["selected"]), sorted(edited["selected"]))
        again = self.client.post(f"/api/jobs/{READ_POSTING}/proposals/0", json={"accept": True}, headers=CHANGE)
        self.assertEqual(again.status_code, 400)
        stats = self.client.get("/api/stats").json()["edits"]
        self.assertEqual((stats["decisions"]["accepted"], stats["accepted_scored"]), (1, 1))
        self.assertEqual([entry["id"] for entry in stats["pages"]], [READ_POSTING])
        # Undo: the engine's page again, and the proposal open again.
        undone = self.client.post(f"/api/jobs/{READ_POSTING}/edits/0/undo", headers=CHANGE).json()
        self.assertEqual(self.app.state.jobs.page(READ_POSTING)["selected"], engine_page)
        self.assertEqual((undone["edits"], undone["judge"]["proposals"][0]["state"]), ([], "open"))
        dismissed = self.client.post(f"/api/jobs/{READ_POSTING}/proposals/0", json={"accept": False}, headers=CHANGE)
        self.assertEqual(dismissed.json()["judge"]["proposals"][0]["state"], "dismissed")
        kinds = [entry["decision"] for entry in self.app.state.jobs.tracker.decisions()]
        self.assertEqual(kinds, ["accepted", "undone", "dismissed"])

    def test_an_accepted_edit_that_no_longer_fits_is_kept_and_marked(self) -> None:
        self.a_fitting_swap()
        library, _fingerprint = self.app.state.jobs.library_cache.get()
        on_page = self.app.state.jobs.page(READ_POSTING)["selected"]
        off_page = next(bullet.id for bullet in library.usable_bullets if bullet.id not in on_page)
        # As if the engine's page had changed since the edit was accepted: it removes a bullet no longer there.
        stale = [{"remove": [off_page], "add": [], "why": "w", "at": "2026-09-24T00:00:00"}]
        self.app.state.jobs.tracker.update(READ_POSTING, edits=json.dumps(stale))
        self.app.state.jobs.build(READ_POSTING)
        self.assertEqual(self.app.state.jobs.page(READ_POSTING)["selected"], on_page)
        edits = self.client.get(f"/api/jobs/{READ_POSTING}").json()["edits"]
        self.assertEqual((edits[0]["applies"], edits[0]["problem"]), (False, f"{off_page} is not on the page"))

    def test_a_tracker_made_before_the_names_and_removal_gains_their_columns(self) -> None:
        import sqlite3

        from tailor_dashboard.tracker import Tracker

        path = self.data / "old.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE jobs (posting_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'added', added TEXT "
                "NOT NULL, status_changed TEXT, notes TEXT NOT NULL DEFAULT '', page_fingerprint TEXT, "
                "page_built TEXT, page_seconds REAL, page_refused TEXT, judge TEXT)"
            )
            connection.execute("INSERT INTO jobs (posting_id, added) VALUES ('x', '2026-09-24')")
        connection.close()
        tracker = Tracker(path)
        tracker.update("x", company="Acme", removed="2026-09-25")
        row = tracker.row("x")
        assert row is not None
        self.assertEqual((row["status"], row["company"], row["removed"]), ("added", "Acme", "2026-09-25"))

    def test_profile_and_stats_read_the_library_and_the_saved_costs(self) -> None:
        self.client.post(f"/api/jobs/{READ_POSTING}/rebuild", headers=CHANGE)
        profile = self.client.get("/api/profile").json()
        self.assertGreater(len(profile["projects"]), 0)
        self.assertEqual(profile["read_jobs"], 1)
        self.assertGreater(sum(project["used"] for project in profile["projects"]), 0)
        self.assertTrue(
            set(profile["covered"]) <= {skill for domain in profile["domains"] for skill in domain["skills"]}
        )
        stats = self.client.get("/api/stats").json()["readings"]
        self.assertIn(READ_POSTING, stats)
        self.assertGreater(stats[READ_POSTING]["call2"]["seconds"], 0)

    def test_a_call_2_shared_by_several_postings_counts_its_share_of_the_time(self) -> None:
        jobs = self.app.state.jobs
        reading, mapping = jobs.records(jobs.posting(READ_POSTING))
        assert mapping is not None
        # The call answered four postings in 28.2 s; its tokens are saved as a quarter each, and so are its seconds.
        batched = {**mapping, "seconds": 28.2, "batch": 4}
        with mock.patch.object(jobs, "records", return_value=(reading, batched)):
            stats = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        self.assertAlmostEqual(stats["call2"]["seconds"], 7.05)

    def test_stats_count_call_3_and_show_none_where_it_was_not_made(self) -> None:
        jobs = self.app.state.jobs
        before = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        self.assertIsNone(before["call3"])
        self.assertAlmostEqual(before["seconds"], before["call1"]["seconds"] + before["call2"]["seconds"])
        store.save_choice(
            jobs.posting(READ_POSTING)["text"],
            {
                "projects": ["A1"],
                "valid": True,
                "seconds": 12.5,
                "input_tokens": 3000,
                "output_tokens": 400,
                "attempts": 2,
            },
            jobs.paths.choices,
        )
        after = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        self.assertEqual(
            after["call3"],
            {
                "seconds": 12.5,
                "input": 3000,
                "output": 400,
                "attempts": 2,
                "requests": 1,
                "unknown": False,
                "by": None,
                "fallback": None,
                "jev": False,
            },
        )
        self.assertAlmostEqual(after["seconds"], before["seconds"] + 12.5)
        self.assertEqual(
            (after["tokens"], after["retries"], after["unknown"]),
            (before["tokens"] + 3400, before["retries"] + 1, before["unknown"]),
        )

    def test_stats_count_jev_s_requests_apart_from_the_claude_plan_s_tokens(self) -> None:
        from tailor_dashboard.views import JEV_DOLLARS_PER_MILLION_INPUT

        jobs = self.app.state.jobs
        posting = jobs.posting(READ_POSTING)
        _reading, mapping = jobs.records(posting)
        assert mapping is not None
        library, _fingerprint = jobs.library_cache.get()
        # Call 2 on Jev in three requests at once, one of them asked again.
        by_jev: Any = {
            **mapping,
            "model": jev.MODEL,
            "answered_by": "jev-1.13.0",
            "descriptions_sha256_16": library.descriptions_hash,
            "seconds": 1.4,
            "input_tokens": 22000,
            "output_tokens": 10000,
            "requests": 3,
            "attempts": 4,
        }
        store.save_mapping(posting["text"], by_jev, jobs.paths.mappings)
        # A call 3 saved before the model was pinned is Jev's too.
        choice: Any = {
            "projects": ["A1"],
            "valid": True,
            "seconds": 0.5,
            "input_tokens": 5400,
            "output_tokens": 6,
            "model": "jev-latest",
        }
        store.save_choice(posting["text"], choice, jobs.paths.choices)
        stats = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        call_1 = stats["call1"]["input"] + stats["call1"]["output"]
        self.assertEqual(stats["tokens"], call_1)  # calls 2 and 3 went to Jev, off the plan
        self.assertEqual(
            stats["jev"],
            {"requests": 5, "input": 27400, "dollars": 27400 * JEV_DOLLARS_PER_MILLION_INPUT / 1e6},
        )
        # Only the request asked again counts as a retry, not the requests call 2 was split into.
        self.assertEqual(stats["retries"], stats["call1"]["attempts"] - 1 + 1)
        # Opus answering call 3 in Jev's place is the plan's.
        notice: Any = {"original_model": "jev-latest", "fallback_model": "claude-opus-5", "api_refusal_category": None}
        opus: Any = {**choice, "model": "claude-opus-5", "fallback": notice, "input_tokens": 4250, "output_tokens": 130}
        store.save_choice(posting["text"], opus, jobs.paths.choices)
        stats = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        self.assertEqual((stats["tokens"], stats["jev"]["requests"]), (call_1 + 4380, 4))

    def test_stats_say_which_model_answered_each_call_and_why_another_did(self) -> None:
        jobs = self.app.state.jobs
        posting = jobs.posting(READ_POSTING)
        _reading, mapping = jobs.records(posting)
        assert mapping is not None
        library, _fingerprint = jobs.library_cache.get()
        answered_by_jev: Any = {
            **mapping,
            "model": jev.MODEL,
            "answered_by": "jev-1.13.0",
            "descriptions_sha256_16": library.descriptions_hash,
        }
        store.save_mapping(posting["text"], answered_by_jev, jobs.paths.mappings)
        notice: Any = {"original_model": "jev-latest", "fallback_model": "claude-opus-5", "api_refusal_category": None}
        store.save_choice(
            posting["text"],
            {
                "projects": ["A1"],
                "valid": True,
                "seconds": 5.5,
                "model": "claude-opus-5",
                "answered_by": "claude-opus-5",
                "fallback": notice,
                "first_errors": ["Jev: no Jev key in the credential store"],
            },
            jobs.paths.choices,
        )
        stats = self.client.get("/api/stats").json()["readings"][READ_POSTING]
        self.assertEqual((stats["call2"]["by"], stats["call2"]["fallback"]), ("Jev", None))
        self.assertEqual(
            (stats["call3"]["by"], stats["call3"]["fallback"]),
            ("Opus 5", "Jev could not answer: no Jev key in the credential store"),
        )


@unittest.skipIf(TestClient is None, "the dashboard's dependencies (fastapi) are not installed")
class Shown(unittest.TestCase):
    """What the dashboard derives for display, found wrong in the tip-to-toe test (2026-09-24)."""

    def test_a_web_address_is_shown_as_the_company_and_a_site_suffix_is_cut(self) -> None:
        from tailor_dashboard.display import company_name, posting_title

        self.assertEqual(
            [company_name(name) for name in ("www.example.com", "careers.northwind.org", "acme-corp", "OpenCo", "")],
            ["Example", "Northwind", "Acme Corp", "OpenCo", "Pasted posting"],
        )

        def title(source: str, text: str) -> str:
            posting: Any = {"source": source, "text": "", "title": text}
            return posting_title(posting)

        self.assertEqual(
            title("html", "Software Engineer, Platform — Example Careers"),
            "Software Engineer, Platform",
        )
        # A dash inside a title is kept; only a site's name after it goes, and only for a page read without a board.
        self.assertEqual(title("html", "Security Engineer - Platform"), "Security Engineer - Platform")
        self.assertEqual(title("greenhouse", "A — Jobs"), "A — Jobs")

    def test_a_model_is_named_as_the_page_shows_it_with_why_another_answered(self) -> None:
        from tailor_dashboard.display import answered, model_name

        self.assertEqual(
            [model_name(model) for model in ("jev-1.13.0", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-4-6")],
            ["Jev", "Opus 5.5", "Opus 5", "Sonnet 4.6"],
        )
        self.assertEqual((model_name("some-model"), model_name(None)), ("some-model", None))
        self.assertIsNone(answered(None))
        self.assertEqual(answered({"model": "jev-latest", "fallback": None}), {"by": "Jev", "fallback": None})
        refused = {
            "original_model": "claude-opus-5-5",
            "fallback_model": "claude-opus-4-8",
            "api_refusal_category": "cyber",
        }
        self.assertEqual(
            answered({"model": "claude-opus-5-5", "answered_by": "claude-opus-4-8", "fallback": refused}),
            {"by": "Opus 4.8", "fallback": "Opus 5.5 could not answer: refused (cyber)"},
        )
        unexplained = {**refused, "original_model": "jev-latest", "api_refusal_category": None}
        self.assertEqual(
            answered({"answered_by": "claude-opus-5", "fallback": unexplained, "first_errors": ["a broken answer"]}),
            {"by": "Opus 5", "fallback": "Jev could not answer"},
        )

    def test_an_answer_naming_projects_by_their_ids_is_asked_for_again(self) -> None:
        answer: dict[str, Any] = {"verdict": "strong", "summary": "Lead with A1 and R.2.", "relevance": "r"}
        answer.update({"alignment": "a", "gaps": [], "better_fits": [], "edits": []})
        ids = frozenset({"A1", "R.2", "C1.2"})
        self.assertEqual(
            review.answer_problems(answer, "brief", ids),
            ["name projects by their titles, not by IDs; the answer uses A1, R.2"],
        )
        self.assertEqual(
            review.answer_problems({**answer, "summary": "Lead with the fuzzing project."}, "brief", ids), []
        )

    def test_the_reading_estimate_follows_the_latest_calls(self) -> None:
        from tailor_dashboard.jobs import RECENT_CALLS, Jobs

        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "mappings").mkdir(parents=True)
            (data / "readings").mkdir()
            # Older calls were slow (100 s), the latest RECENT_CALLS fast (20 s); the estimate follows the latest.
            for number in range(RECENT_CALLS + 5):
                seconds = 100.0 if number < 5 else 20.0
                record = {"seconds": seconds, "made": f"2026-09-{10 + number:02d}T12:00:00", "model": "claude-opus-5-5"}
                (data / "mappings" / f"{number:016x}.json").write_text(json.dumps(record), encoding="utf-8")
            jobs = Jobs(Paths.under(data, EXAMPLE_LIBRARY), classifier="opus")
            estimate = jobs.call_seconds()
            # Jev's calls are timed from Jev's records alone, and before any is saved from its own measure.
            jobs.classifier = "jev"
            jobs.modes.set(call2="jev")
            before_jev = jobs.call_seconds()
            jev_record = {"seconds": 0.4, "made": "2026-09-25T12:00:00", "model": "jev-latest"}
            (data / "mappings" / f"{50:016x}.json").write_text(json.dumps(jev_record), encoding="utf-8")
            with_jev = jobs.call_seconds()
            # On Accuracy, call 2 is timed from Opus's records and call 3 still from Jev's.
            jobs.modes.set(call2="opus")
            split = jobs.call_seconds()
            jobs.classifier = "opus"
            # The estimate is kept between requests; a newly saved record still reaches it.
            for number in range(RECENT_CALLS):
                record = {"seconds": 8.0, "made": f"2026-09-{26 + number // 5:02d}T{number:02d}:00:00"}
                (data / "mappings" / f"{100 + number:016x}.json").write_text(json.dumps(record), encoding="utf-8")
            later = jobs.call_seconds()
        self.assertEqual((estimate["mapping"], later["mapping"]), (20.0, 8.0))
        self.assertEqual((before_jev["mapping"], before_jev["projects"], with_jev["mapping"]), (1.45, 0.5, 0.4))
        self.assertEqual((split["mapping"], split["projects"]), (20.0, 0.5))

    def test_opus_answering_in_jev_s_place_counts_toward_jev_s_estimate(self) -> None:
        from tailor_dashboard.jobs import Jobs

        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "mappings").mkdir()
            (data / "choices").mkdir()
            # A copy with no key, or TypeSafe down: every call 2 and 3 on Jev was answered by Opus, in Opus's time.
            # Notices from before the pin name "jev-latest": Jev's all the same, by the prefix.
            notice = {"original_model": "jev-latest", "fallback_model": "claude-opus-5", "api_refusal_category": None}
            for number in range(3):
                made = f"2026-09-2{number}T12:00:00"
                mapping = {"seconds": 9.0, "made": made, "model": "claude-opus-5-5", "fallback": notice}
                choice = {"seconds": 5.5, "made": made, "model": "claude-opus-5", "fallback": notice}
                (data / "mappings" / f"{number:016x}.json").write_text(json.dumps(mapping), encoding="utf-8")
                (data / "choices" / f"{number:016x}.json").write_text(json.dumps(choice), encoding="utf-8")
            jobs = Jobs(Paths.under(data, EXAMPLE_LIBRARY), classifier="jev")
            jobs.modes.set(call2="jev")
            by_jev = jobs.call_seconds()
            jobs.classifier = "opus"
            by_opus = jobs.call_seconds()
        self.assertEqual((by_jev["mapping"], by_jev["projects"]), (9.0, 5.5))
        self.assertEqual((by_opus["mapping"], by_opus["projects"]), (9.0, 5.5))  # still Opus's answers


class TrackerAndModes(unittest.TestCase):
    """The tracker's status date and the page-modes file, each on a file made here."""

    def test_status_changed_is_set_with_each_status_and_never_by_hand(self) -> None:
        from tailor_dashboard.tracker import Tracker

        with tempfile.TemporaryDirectory() as folder:
            tracker = Tracker(Path(folder) / "tracker.sqlite")
            tracker.add("x")
            with self.assertRaises(ValueError):
                tracker.update("x", status_changed="2026-01-01T00:00:00")
            unchanged = tracker.row("x")
            tracker.update("x", status="applied")
            changed = tracker.row("x")
        assert unchanged is not None and changed is not None
        self.assertIsNone(unchanged["status_changed"])
        self.assertEqual(changed["status"], "applied")
        self.assertIsNotNone(changed["status_changed"])

    def test_a_bad_page_modes_file_is_logged_and_a_missing_one_is_not(self) -> None:
        from tailor_dashboard.page_modes import PageModes, PageModesFile

        with tempfile.TemporaryDirectory() as folder:
            modes = PageModesFile(Path(folder) / "page-modes.json")
            with self.assertNoLogs("tailor_dashboard.page_modes"):
                self.assertEqual(modes.get(), PageModes())
            for bad in ("not JSON", "[]", '{"pages": "always"}', '{"pages": "legacy", "call2": "gpt"}'):
                modes.path.write_text(bad, encoding="utf-8")
                with self.assertLogs("tailor_dashboard.page_modes", "WARNING"):
                    self.assertEqual(modes.get().call2, PageModes().call2)
            # A file saved before the call 2 choice loads with call 2 on Accuracy.
            modes.path.write_text('{"pages": "legacy"}', encoding="utf-8")
            with self.assertNoLogs("tailor_dashboard.page_modes"):
                self.assertEqual(modes.get(), PageModes("legacy", "opus"))
            # A file saved while the Efficiency or Speed priority existed still loads, its priority left unread.
            for saved in ('{"pages": "legacy", "priority": "speed"}', '{"pages": "hybrid", "priority": "efficiency"}'):
                modes.path.write_text(saved, encoding="utf-8")
                with self.assertNoLogs("tailor_dashboard.page_modes"):
                    self.assertEqual(modes.get(), PageModes(json.loads(saved)["pages"]))
            self.assertEqual(modes.set(pages="legacy"), PageModes("legacy"))
            self.assertEqual(json.loads(modes.path.read_text(encoding="utf-8")), {"pages": "legacy", "call2": "opus"})
            self.assertEqual(modes.set(call2="jev"), PageModes("legacy", "jev"))
            with self.assertRaisesRegex(ValueError, "call2 must be one of jev, opus"):
                modes.set(call2="gpt")
            # With no file, call 2 follows the setting (TAILOR_CALL2), Opus unless it says otherwise.
            modes.path.unlink()
            with mock.patch.object(settings, "CALL2", "jev"):
                self.assertEqual(modes.get(), PageModes("hybrid", "jev"))

    def test_a_page_modes_file_being_replaced_is_read_once_it_is_let_go(self) -> None:
        from tailor_dashboard.page_modes import PageModes, PageModesFile

        read_text = Path.read_text
        refused: list[Path] = []

        def replaced_once(path: Path, *arguments: Any, **options: Any) -> str:
            # As on Windows while a change on another thread renames the new file over it: the first open is refused.
            if not refused:
                refused.append(path)
                raise PermissionError(13, "Permission denied", str(path))
            return read_text(path, *arguments, **options)

        with tempfile.TemporaryDirectory() as folder:
            modes = PageModesFile(Path(folder) / "page-modes.json")
            modes.set(pages="legacy")
            with mock.patch.object(Path, "read_text", replaced_once), self.assertNoLogs("tailor_dashboard.page_modes"):
                self.assertEqual(modes.get(), PageModes("legacy"))
        self.assertEqual(len(refused), 1)


class Held:
    """A stand-in for the cap on Jev's requests in flight: how many hold it now."""

    def __init__(self) -> None:
        self.depth = 0

    def __enter__(self) -> None:
        self.depth += 1

    def __exit__(self, *details: object) -> None:
        self.depth -= 1


class Heard(list[dict[str, Any]]):
    """The events a reading sends, and `call3`, set once the page is told a call 3 is being waited for."""

    def __init__(self) -> None:
        super().__init__()
        self.call3 = threading.Event()

    def append(self, event: dict[str, Any]) -> None:
        super().append(event)
        if event.get("stage") == "call3":
            self.call3.set()


def _mapping_record(title: str) -> Any:
    return {"title": title, "requirements": [], "valid": True}


def _wait_until(condition: Any, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("waited too long")
        time.sleep(0.01)


@unittest.skipIf(TestClient is None, "the dashboard's dependencies (fastapi) are not installed")
class MappingBatches(unittest.TestCase):
    """Call 2 in batches: alone at once, five to a call, the rest when nothing else is coming; failures reach all."""

    def mapped_in_threads(self, batcher: Any, count: int) -> tuple[list[threading.Thread], list[Any], list[Exception]]:
        results: list[Any] = []
        errors: list[Exception] = []

        def one(number: int) -> None:
            try:
                results.append(batcher.map(f"Role {number}", [], {"c": "D"}))
            except Exception as error:  # noqa: BLE001  collected for the test to read
                errors.append(error)

        threads = [threading.Thread(target=one, args=(number,)) for number in range(count)]
        for thread in threads:
            thread.start()
        return threads, results, errors

    def test_a_posting_read_on_its_own_goes_at_once_as_a_single_call(self) -> None:
        from tailor_dashboard.background import MappingBatcher

        calls: list[str] = []

        def single(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            calls.append(title)
            return _mapping_record(title)

        with (
            mock.patch.object(capability_mapping, "map_requirements", single),
            mock.patch.object(capability_mapping, "map_many", mock.Mock(side_effect=AssertionError("no batch"))),
        ):
            record = MappingBatcher(lambda: 0).map("Role", [], {"c": "D"})
        self.assertEqual((calls, record["title"]), (["Role"], "Role"))

    def test_seven_waiting_postings_go_as_five_then_two_once_nothing_else_is_coming(self) -> None:
        from tailor_dashboard.background import MappingBatcher

        sizes: list[int] = []
        release = threading.Event()

        def many(postings: list[Any], vocabulary: dict[str, str]) -> list[Any]:
            sizes.append(len(postings))
            release.wait(10)
            return [_mapping_record(title) for title, _requirements in postings]

        coming = [1]
        batcher = MappingBatcher(lambda: coming[0])
        with mock.patch.object(capability_mapping, "map_many", many):
            threads, results, errors = self.mapped_in_threads(batcher, 7)
            _wait_until(lambda: sizes == [5] and batcher.waiting() == 2)
            release.set()
            time.sleep(0.2)
            self.assertEqual(sizes, [5])  # the two still wait: postings are still coming
            coming[0] = 0
            batcher.poke()
            for thread in threads:
                thread.join(10)
        self.assertEqual((sizes, errors), ([5, 2], []))
        self.assertEqual(sorted(record["title"] for record in results), [f"Role {number}" for number in range(7)])

    def test_a_posting_mapped_under_another_vocabulary_waits_for_a_call_of_its_own(self) -> None:
        from tailor_dashboard.background import MappingBatcher

        calls: list[tuple[list[str], dict[str, str]]] = []

        def many(postings: list[Any], vocabulary: dict[str, str]) -> list[Any]:
            calls.append(([title for title, _requirements in postings], vocabulary))
            return [_mapping_record(title) for title, _requirements in postings]

        def single(title: str, found: list[Any], vocabulary: dict[str, str]) -> Any:
            calls.append(([title], vocabulary))
            return _mapping_record(title)

        coming = [1]
        batcher = MappingBatcher(lambda: coming[0])
        before, after = {"c": "D"}, {"c": "D", "e": "F"}  # the library changed between two readings
        threads: list[threading.Thread] = []
        with (
            mock.patch.object(capability_mapping, "map_many", many),
            mock.patch.object(capability_mapping, "map_requirements", single),
        ):
            for title, vocabulary in (("A1", before), ("B1", after), ("A2", before)):
                threads.append(threading.Thread(target=batcher.map, args=(title, [], vocabulary)))
                threads[-1].start()
                waiting = len(threads)
                _wait_until(lambda waiting=waiting: batcher.waiting() == waiting)
            coming[0] = 0
            batcher.poke()
            for thread in threads:
                thread.join(10)
        self.assertEqual(calls, [(["A1", "A2"], before), (["B1"], after)])

    def test_a_failed_batch_fails_every_posting_in_it(self) -> None:
        from tailor_dashboard.background import MappingBatcher

        batcher = MappingBatcher(lambda: 1, size=2)
        with mock.patch.object(capability_mapping, "map_many", mock.Mock(side_effect=RuntimeError("no answer"))):
            threads, results, errors = self.mapped_in_threads(batcher, 2)
            for thread in threads:
                thread.join(10)
        self.assertEqual((results, [str(error) for error in errors]), ([], ["no answer", "no answer"]))

    def test_stopping_releases_postings_still_waiting(self) -> None:
        from tailor_dashboard.background import MappingBatcher

        batcher = MappingBatcher(lambda: 1)
        threads, results, errors = self.mapped_in_threads(batcher, 1)
        _wait_until(lambda: batcher.waiting() == 1)
        batcher.close()
        threads[0].join(10)
        self.assertEqual((results, [str(error) for error in errors]), ([], ["the dashboard is stopping"]))


@unittest.skipIf(TestClient is None, "the dashboard's dependencies (fastapi) are not installed")
class JudgePrompt(unittest.TestCase):
    def test_the_prompt_states_the_page_project_limit_the_engine_uses(self) -> None:
        words = NUMBER_WORDS
        self.assertIn(f"at most {words[layout.PAGE_PROJECTS]} projects", " ".join(review.SYSTEM_PROMPT.split()))

    def test_each_length_states_every_limit_the_answer_is_checked_against(self) -> None:
        words = NUMBER_WORDS
        for level, (gaps, fits, edits) in review.LIMITS.items():
            for stated in (f"at most {words[gaps]} gaps", f"at most {words[fits]} better-fitting roles"):
                self.assertIn(stated, review.LENGTH[level])
            self.assertIn(f"at most {words[edits]} page edits", review.LENGTH[level])


if __name__ == "__main__":
    unittest.main()
