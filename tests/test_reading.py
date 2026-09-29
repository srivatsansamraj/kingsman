"""Reading a posting, offline and without the candidate's library.

Fetching, board facts, text facts, the three model calls' parsing and retries, the privacy guard before calls 2 and 3,
the saved-readings store, and the make-once pipeline. The network, the models and the vocabulary are all stand-ins
built here.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import httpx

from tailor_engine import privacy, records, settings
from tailor_engine.library import Library
from tailor_engine.library.capabilities import load_capability_vocabulary, vocabulary_hash
from tailor_engine.library.capability_descriptions import descriptions_hash, load_capability_descriptions
from tailor_engine.library.projects import ProjectProfile
from tailor_engine.reading import (
    board_facts,
    capability_mapping,
    fetch,
    model_call,
    pipeline,
    project_choice,
    requirements,
    store,
)
from tailor_engine.reading.facts import level_of, posting_facts
from tailor_engine.reading.model_call import Effort, JsonAnswer, TextAnswer
from tailor_engine.records import Pay, Reading, Requirement

POSTING_TEXT = "Security Engineer\nRequirements:\n- Threat modelling for cloud systems\n- Python"
VOCABULARY = {"threat modelling": "Application and platform security", "penetration testing": "Offensive security"}
CAPABILITIES_FILE = """
[[domain]]
name = "Application and platform security"
tag = "sec.appsec"
families = ["Security"]
capabilities = ["threat modelling"]

[[domain]]
name = "Offensive security"
tag = "sec.offensive"
families = ["Security"]
capabilities = ["penetration testing"]
"""
DESCRIPTIONS_FILE = """
["threat modelling"]
domain = "Application and platform security"
what = "finding how a design can be attacked"
not_for = "penetration testing"
examples = ["Threat modelling with STRIDE"]

["penetration testing"]
domain = "Offensive security"
what = "attacking a running system"
not_for = "threat modelling"
examples = ["Web application penetration testing"]

["none of these"]
what = "a condition only"
not_for = "an ability"
examples = ["5+ years of experience"]
"""


def pay_of(facts: records.BoardFacts) -> Pay:
    pay = facts.get("pay")
    assert pay is not None
    return pay


def first_requirement_name(reading: Reading | None) -> str:
    assert reading is not None
    return reading["requirements"][0]["name"]


def board_api(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    """A stand-in network answering each URL prefix with (status, JSON body)."""

    def answer(request: httpx.Request) -> httpx.Response:
        for prefix, (status, body) in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})

    return httpx.MockTransport(answer)


class Fetching(unittest.TestCase):
    GREENHOUSE = "https://job-boards.greenhouse.io/example/jobs/123"

    def test_greenhouse_gives_text_and_facts_in_one_request(self) -> None:
        job = {
            "title": "Security Engineer",
            "location": {"name": "Remote, US"},
            "content": "&lt;p&gt;Threat modelling&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
            "pay_input_ranges": [
                {"min_cents": 15000000, "max_cents": 20000000, "currency_type": "USD", "title": "Base"}
            ],
            "metadata": [{"name": "Location Type", "value": "Remote"}],
        }
        posting = fetch.fetch_posting(
            self.GREENHOUSE,
            check_addresses=False,
            transport=board_api({"https://boards-api.greenhouse.io/": (200, job)}),
        )
        # The layout the saved readings were keyed on: title, location line, blank line, body.
        self.assertEqual(posting["text"], "Security Engineer\nLocation: Remote, US\n\nThreat modelling\n Python")
        self.assertEqual((posting["id"], posting["source"], posting["company"]), ("123", "greenhouse", "example"))
        self.assertEqual((pay_of(posting["board"])["min"], posting["board"]["work_mode"]), (150000, "remote"))

    def test_a_board_that_no_longer_lists_the_posting_says_so(self) -> None:
        with self.assertRaisesRegex(fetch.FetchError, "may have closed"):
            fetch.fetch_posting(
                self.GREENHOUSE,
                check_addresses=False,
                transport=board_api({"https://boards-api.greenhouse.io/": (404, {})}),
            )

    def test_ashby_finds_its_posting_on_the_board(self) -> None:
        url = "https://jobs.ashbyhq.com/example/3b9e2c41-7d0a-4f6e-9a15-c28e6b0d4f73"
        board = {
            "jobs": [
                {
                    "id": "3b9e2c41-7d0a-4f6e-9a15-c28e6b0d4f73",
                    "title": "Researcher",
                    "location": "London",
                    "secondaryLocations": [{"location": "New York"}],
                    "descriptionPlain": "Do research.",
                    "workplaceType": "Hybrid",
                }
            ]
        }
        posting = fetch.fetch_posting(
            url, check_addresses=False, transport=board_api({"https://api.ashbyhq.com/": (200, board)})
        )
        self.assertEqual(posting["text"], "Researcher\nLocation: London\n\nDo research.")
        self.assertEqual((posting["board"]["location"], posting["board"]["work_mode"]), ("London; New York", "hybrid"))

    # The text each board builds is the key its saved readings are stored under, so these pin it exactly.
    def test_lever_text_joins_description_lists_and_additional(self) -> None:
        url = "https://jobs.lever.co/example/a6c1e0f2-94b8-4d37-8e5a-1f0b7c2d9e46"
        job = {
            "text": "Defensive Security Analyst",
            "categories": {"location": "Washington, D.C."},
            "description": "<p>Protect people and infrastructure.</p>",
            "lists": [{"text": "What you will do", "content": "<li>Hunt threats</li><li>Write detections</li>"}],
            "additional": "<p>Benefits apply.</p>",
            "workplaceType": "onsite",
        }
        posting = fetch.fetch_posting(
            url, check_addresses=False, transport=board_api({"https://api.lever.co/": (200, job)})
        )
        self.assertEqual(
            posting["text"],
            "Defensive Security Analyst\nLocation: Washington, D.C.\n\nProtect people and "
            "infrastructure.\n\nWhat you will do\nHunt threats\n Write detections\n\n"
            "Benefits apply.",
        )
        self.assertEqual((posting["id"], posting["source"], posting["company"]), ("a6c1e0f2", "lever", "example"))

    def test_amazon_text_from_its_search_listing(self) -> None:
        url = "https://www.amazon.jobs/en/jobs/2971234/security-engineer"
        listing = {
            "jobs": [
                {
                    "id_icims": "2971234",
                    "title": "Security Engineer",
                    "normalized_location": "Seattle, WA, USA",
                    "description": "<p>Secure the services.</p>",
                    "basic_qualifications": "- 3+ years of Python",
                    "preferred_qualifications": "- Threat modelling",
                }
            ]
        }
        posting = fetch.fetch_posting(
            url, check_addresses=False, transport=board_api({"https://www.amazon.jobs/": (200, listing)})
        )
        self.assertEqual(
            posting["text"],
            "Security Engineer\n\nLocation: Seattle, WA, USA\n\nDESCRIPTION\nSecure the "
            "services.\n\nBASIC QUALIFICATIONS\n- 3+ years of Python\n\nPREFERRED "
            "QUALIFICATIONS\n- Threat modelling",
        )
        self.assertEqual((posting["id"], posting["source"], posting["company"]), ("2971234", "amazon", "Amazon"))

    def test_amazon_and_workday_say_when_they_no_longer_list_the_posting(self) -> None:
        with self.assertRaisesRegex(fetch.FetchError, "Amazon does not list"):
            fetch.fetch_posting(
                "https://www.amazon.jobs/en/jobs/2971234/security-engineer",
                check_addresses=False,
                transport=board_api({"https://www.amazon.jobs/": (200, {"jobs": []})}),
            )
        with self.assertRaisesRegex(fetch.FetchError, "Workday does not list"):
            fetch.fetch_posting(
                "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/Security-Engineer_R123",
                check_addresses=False,
                transport=board_api({}),
            )

    def test_workday_with_almost_no_text_is_read_from_its_page(self) -> None:
        url = "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/Security-Engineer_R123"

        def answer(request: httpx.Request) -> httpx.Response:
            if "/wday/cxs/" in request.url.path:
                return httpx.Response(200, json={"jobPostingInfo": {"title": "Security Engineer"}})
            page = "<p>" + "Threat model new services. " * 20 + "</p>"
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        posting = fetch.fetch_posting(url, check_addresses=False, transport=httpx.MockTransport(answer))
        self.assertEqual((posting["source"], posting["company"]), ("html", "example.wd5.myworkdayjobs.com"))

    def test_workday_text_from_its_posting_api(self) -> None:
        url = "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/Security-Engineer_R123"
        body = "Build detection pipelines and review designs. " * 10
        info = {
            "jobPostingInfo": {
                "title": "Security Engineer",
                "location": "Remote",
                "additionalLocations": ["Austin"],
                "jobDescription": f"<p>{body}</p>",
            }
        }
        posting = fetch.fetch_posting(
            url, check_addresses=False, transport=board_api({"https://example.wd5.myworkdayjobs.com/": (200, info)})
        )
        self.assertEqual(posting["text"], "Security Engineer\nLocation: Remote; Austin\n\n" + body.strip())
        self.assertEqual((posting["source"], posting["company"]), ("workday", "example"))
        # The URL carries no job number, so the posting is named by its text.
        self.assertRegex(posting["id"], r"^workday-[0-9a-f]{8}$")

    def test_any_other_page_is_read_as_html(self) -> None:
        page = (
            "<html><head><title>Security Engineer | Example</title></head><body><nav>Home</nav>"
            "<h1>Security Engineer</h1><p>"
            + "You will threat model new services and review their designs. " * 8
            + "</p><ul><li>Python</li><li>Threat modelling</li></ul></body></html>"
        )

        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

        posting = fetch.fetch_posting(
            "https://careers.example.org/jobs/42", check_addresses=False, transport=httpx.MockTransport(answer)
        )
        self.assertEqual(
            posting["text"],
            "Security Engineer | Example Home Security Engineer\n"
            + " You will threat model new services and review their designs." * 8
            + " \n Python\n Threat modelling",
        )
        self.assertEqual((posting["source"], posting["title"]), ("html", "Security Engineer | Example"))
        # A company page's job number is unique only on its own site, so the posting is named by its text.
        self.assertEqual(posting["id"], "html-" + store.text_key(posting["text"])[:8])

    def test_postings_whose_urls_carry_no_id_are_named_by_their_text(self) -> None:
        def page(text: str) -> httpx.MockTransport:
            def answer(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, text=f"<p>{text}</p>", headers={"content-type": "text/html"})

            return httpx.MockTransport(answer)

        first = fetch.fetch_posting(
            "https://careers.example.org/security", check_addresses=False, transport=page("Threat model. " * 30)
        )
        second = fetch.fetch_posting(
            "https://careers.example.org/security", check_addresses=False, transport=page("Review designs. " * 30)
        )
        self.assertRegex(first["id"], r"^html-[0-9a-f]{8}$")
        self.assertNotEqual(first["id"], second["id"])

    def test_network_and_answer_failures_come_out_as_fetch_errors(self) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        greenhouse = "https://job-boards.greenhouse.io/example/jobs/1"
        with self.assertRaisesRegex(fetch.FetchError, "could not be reached"):
            fetch.fetch_posting(greenhouse, check_addresses=False, transport=httpx.MockTransport(unreachable))
        not_json = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
        with self.assertRaisesRegex(fetch.FetchError, "does not know"):
            fetch.fetch_posting(greenhouse, check_addresses=False, transport=not_json)
        other_shape = httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": "none"}))
        with self.assertRaisesRegex(fetch.FetchError, "does not know"):
            fetch.fetch_posting(
                "https://jobs.ashbyhq.com/example/" + "0" * 36, check_addresses=False, transport=other_shape
            )

    def test_a_port_in_the_url_does_not_become_the_company(self) -> None:
        page = httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="<p>" + "Threat model new services. " * 20 + "</p>", headers={"content-type": "text/html"}
            )
        )
        posting = fetch.fetch_posting("https://careers.example.org:8443/jobs/42", check_addresses=False, transport=page)
        self.assertEqual((posting["source"], posting["company"]), ("html", "careers.example.org"))

    def test_linkedin_is_refused_before_any_request(self) -> None:
        with self.assertRaisesRegex(fetch.FetchError, "paste"):
            fetch.fetch_posting(
                "https://www.linkedin.com/jobs/view/123", check_addresses=False, transport=board_api({})
            )

    def test_a_redirect_to_a_local_address_is_refused(self) -> None:
        public = [(2, 1, 6, "", ("93.184.216.34", 0))]

        def resolve(host: str, *args: Any) -> Any:
            return public if host == "careers.example.org" else [(2, 1, 6, "", (host, 0))]

        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/moved":
                return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"})
            return httpx.Response(
                200, text="<p>" + "Threat model new services. " * 20 + "</p>", headers={"content-type": "text/html"}
            )

        network = httpx.MockTransport(answer)
        with mock.patch("socket.getaddrinfo", resolve):
            posting = fetch.fetch_posting("https://careers.example.org/jobs/1", transport=network, check_addresses=True)
            self.assertEqual(posting["source"], "html")
            with self.assertRaisesRegex(fetch.FetchError, "private or local"):
                fetch.fetch_posting("https://careers.example.org/moved", transport=network, check_addresses=True)

    def test_a_local_address_is_refused(self) -> None:
        with self.assertRaisesRegex(fetch.FetchError, "private or local"):
            fetch.fetch_posting("http://127.0.0.1:8000/job")


class PastedPostings(unittest.TestCase):
    def test_pasted_text_is_tidied_as_fetched_text_is(self) -> None:
        posting = pipeline.posting_from_text("R&amp;D   security team\r\n\n\n\nPython&nbsp;and Rust  ", "Engineer")
        self.assertEqual(posting["text"], "R&D security team\n\nPython\xa0and Rust")
        # Plain text without entities keeps the key it had before pasted text was decoded.
        self.assertEqual(pipeline.posting_from_text(POSTING_TEXT)["id"], "paste-" + store.text_key(POSTING_TEXT)[:8])


class BoardFactsTests(unittest.TestCase):
    def test_greenhouse_pay_and_location_type(self) -> None:
        facts = board_facts.greenhouse_facts(
            {
                "pay_input_ranges": [
                    {"min_cents": 30000000, "max_cents": 40500000, "currency_type": "USD", "title": "Annual Salary:"}
                ],
                "metadata": [{"name": "Location Type", "value": "On-Site"}],
                "location": {"name": "San Francisco, CA"},
            }
        )
        self.assertEqual((pay_of(facts)["min"], pay_of(facts)["max"], facts["work_mode"]), (300000, 405000, "onsite"))

    def test_lever_salary_and_work_mode(self) -> None:
        facts = board_facts.lever_facts(
            {
                "workplaceType": "hybrid",
                "salaryRange": {"min": 150000, "max": 190000, "currency": "USD", "interval": "per-year-salary"},
                "lists": [{"text": "What We Require", "content": "<li>Python</li>\n<li>Threat modelling</li>"}],
            }
        )
        self.assertEqual((pay_of(facts)["min"], facts["work_mode"]), (150000, "hybrid"))

    def test_ashby_salary_component_not_equity(self) -> None:
        facts = board_facts.ashby_facts(
            {
                "compensation": {
                    "compensationTiers": [
                        {
                            "components": [
                                {"compensationType": "EquityCashValue", "minValue": None},
                                {
                                    "compensationType": "Salary",
                                    "interval": "1 YEAR",
                                    "currencyCode": "USD",
                                    "minValue": 257000,
                                    "maxValue": 335000,
                                },
                            ]
                        }
                    ]
                },
                "isRemote": True,
            }
        )
        self.assertEqual((pay_of(facts)["min"], pay_of(facts)["max"], facts["work_mode"]), (257000, 335000, "remote"))

    def test_ashby_tiers_span_in_one_currency_and_are_all_kept(self) -> None:
        # Four location tiers: the first alone gave CA$295K where the board's own summary starts at 250K.
        def tier(title: str, currency: str, low: int, high: int) -> dict[str, Any]:
            return {
                "title": title,
                "components": [
                    {
                        "compensationType": "Salary",
                        "interval": "1 YEAR",
                        "currencyCode": currency,
                        "minValue": low,
                        "maxValue": high,
                    }
                ],
            }

        facts = board_facts.ashby_facts(
            {
                "compensation": {
                    "compensationTiers": [
                        tier("Toronto", "CAD", 295000, 535000),
                        tier("Canada", "CAD", 250000, 460000),
                        tier("US high cost", "USD", 205000, 380000),
                        tier("US other", "USD", 175000, 325000),
                    ]
                }
            }
        )
        self.assertEqual(
            (pay_of(facts)["min"], pay_of(facts)["max"], pay_of(facts)["currency"]), (250000, 535000, "CAD")
        )
        self.assertEqual(
            [tier["label"] for tier in pay_of(facts)["tiers"]], ["Toronto", "Canada", "US high cost", "US other"]
        )

    def test_ashby_secondary_locations_are_kept(self) -> None:
        facts = board_facts.ashby_facts(
            {"location": "London", "secondaryLocations": [{"location": "New York"}, {"location": "Toronto"}]}
        )
        self.assertEqual(facts["location"], "London; New York; Toronto")


class TextFacts(unittest.TestCase):
    def test_member_of_technical_staff_is_a_title_not_a_level(self) -> None:
        self.assertEqual(level_of("Senior Member of Technical Staff, Security"), "senior")
        self.assertEqual(level_of("Member of Technical Staff, Security"), "unstated")
        self.assertEqual(level_of("Staff Security Engineer"), "staff")

    def test_board_pay_wins_over_the_text(self) -> None:
        posting = pipeline.posting_from_text(
            POSTING_TEXT + "\nBase salary $100,000 - $120,000", "Security Engineer", "X"
        )
        self.assertEqual(posting_facts(posting)["salary"]["from"], "posting text")
        posting["board"] = {"source": "greenhouse", "pay": {"min": 1, "max": 2, "currency": "USD", "period": "year"}}
        self.assertEqual(posting_facts(posting)["salary"]["from"], "greenhouse")
        # Neither the board nor the text gives pay.
        self.assertIsNone(posting_facts(pipeline.posting_from_text(POSTING_TEXT))["salary"])


class StoreTests(unittest.TestCase):
    def test_reading_round_trip_prefers_the_postings_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store.save_reading("b", POSTING_TEXT, [{"name": "from b"}], [], directory=directory)
            store.save_reading("a", POSTING_TEXT, [{"name": "from a"}], [], directory=directory)
            self.assertEqual(first_requirement_name(store.load_reading(POSTING_TEXT, "b", directory)), "from b")
            # Without an id, or with an id that has no file: the first file for the text in name order.
            self.assertEqual(first_requirement_name(store.load_reading(POSTING_TEXT, None, directory)), "from a")
            self.assertEqual(first_requirement_name(store.load_reading(POSTING_TEXT, "zz", directory)), "from a")
            self.assertIsNone(store.load_reading(POSTING_TEXT + " edited", None, directory))

    def test_a_save_replaces_the_file_whole_and_leaves_no_temporary_file(self) -> None:
        first, second = (
            pipeline.posting_from_text(POSTING_TEXT, "first"),
            pipeline.posting_from_text(POSTING_TEXT, "second"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store.save_posting(first, directory)
            store.save_posting(second, directory)
            self.assertEqual([path.name for path in directory.iterdir()], [f"{first['id']}.json"])
            self.assertEqual(store.load_posting(first["id"], directory), second)

    def test_mapping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store.save_mapping(POSTING_TEXT, {"requirements": [], "valid": True}, Path(temporary))
            saved = store.load_mapping(POSTING_TEXT, Path(temporary))
            self.assertTrue(saved is not None and saved["valid"])
            self.assertIsNone(store.load_mapping("other", Path(temporary)))

    def test_call_2_is_made_again_for_a_missing_stale_or_invalid_mapping(self) -> None:
        current: records.CapabilityMapping = {"vocabulary_sha256_16": "abc", "format": store.MAPPING_FORMAT}
        self.assertFalse(store.mapping_needs_call({**current, "valid": True}, "abc", "described"))
        self.assertTrue(store.mapping_needs_call({**current, "valid": False}, "abc", "described"))
        self.assertTrue(store.mapping_needs_call({**current, "valid": True}, "another vocabulary", "described"))
        self.assertTrue(store.mapping_needs_call(None, "abc", "described"))

    def test_a_jev_mapping_is_stale_once_the_descriptions_change_and_an_opus_one_is_not(self) -> None:
        opus: records.CapabilityMapping = {
            "vocabulary_sha256_16": "abc",
            "format": store.MAPPING_FORMAT,
            "valid": True,
            "model": "claude-opus-5-5",
        }
        pinned: records.CapabilityMapping = {**opus, "model": "jev-1.13.0", "descriptions_sha256_16": "described"}
        # Jev's records of before the pin are Jev's too; one made before the descriptions were sent is stale.
        latest: records.CapabilityMapping = {**pinned, "model": "jev-latest"}
        undescribed: records.CapabilityMapping = {**opus, "model": "jev-latest"}
        for mapping, current_now, current_after_an_edit in (
            (opus, True, True),
            (pinned, True, False),
            (latest, True, False),
            (undescribed, False, False),
        ):
            with self.subTest(model=mapping["model"], described="descriptions_sha256_16" in mapping):
                self.assertEqual(store.is_current(mapping, "abc", "described"), current_now)
                self.assertEqual(store.is_current(mapping, "abc", "edited"), current_after_an_edit)
                self.assertEqual(store.mapping_needs_call(mapping, "abc", "edited"), not current_after_an_edit)

    def test_a_save_over_a_file_a_reader_holds_is_made_once_it_is_let_go(self) -> None:
        replace = os.replace
        refused: list[str] = []

        def held_once(source: Any, target: Any) -> None:
            # As on Windows while the dashboard reads the file on another thread: the first rename over it is refused.
            if not refused:
                refused.append(str(target))
                raise PermissionError(13, "Access is denied", str(target))
            replace(source, target)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store.save_mapping(POSTING_TEXT, {"requirements": [], "valid": False}, directory)
            with mock.patch.object(os, "replace", held_once):
                store.save_mapping(POSTING_TEXT, {"requirements": [], "valid": True}, directory)
            saved = store.load_mapping(POSTING_TEXT, directory)
            files = [path.name for path in directory.iterdir()]
        self.assertEqual((len(refused), saved is not None and saved["valid"]), (1, True))
        self.assertEqual(files, [store.mapping_path(POSTING_TEXT, directory).name])  # no temporary file is left

    def test_a_file_being_replaced_is_read_once_it_is_let_go(self) -> None:
        read_text = Path.read_text
        refused: list[Path] = []

        def replaced_once(path: Path, *arguments: Any, **options: Any) -> str:
            # As on Windows while another thread renames a new file over it: the first open is refused.
            if not refused:
                refused.append(path)
                raise PermissionError(13, "Permission denied", str(path))
            return read_text(path, *arguments, **options)

        with tempfile.TemporaryDirectory() as temporary:
            store.save_choice(POSTING_TEXT, {"projects": ["A1"], "valid": True}, Path(temporary))
            with mock.patch.object(Path, "read_text", replaced_once):
                saved = store.load_choice(POSTING_TEXT, Path(temporary))
        self.assertEqual((len(refused), saved), (1, {"projects": ["A1"], "valid": True}))

    def test_a_file_that_stays_locked_is_still_an_error(self) -> None:
        attempts: list[int] = []

        def locked() -> str:
            attempts.append(1)
            raise PermissionError(13, "Access is denied")

        with mock.patch("tailor_engine.reading.store.time.sleep"), self.assertRaises(PermissionError):
            store.retried(locked)
        self.assertEqual(len(attempts), store.REPLACE_ATTEMPTS)


class RequirementReader(unittest.TestCase):
    ANSWER = "\n".join(
        [
            "FAMILY: security",  # a slot the template no longer asks for: ignored
            "REQ: R | Threat modelling | threat modelling; STRIDE | 2",
            "REQ: P | Python | Python | 3",
            "REQ: R | Strong communication | communication | 4",  # soft item: dropped
            "REQ: R | Cloud security | AWS; GCP | 99",  # line out of range: kept, no source
            "REQ: broken line without fields",  # unreadable: skipped
            "KEYWORDS: threat modelling; Python; AWS",
        ]
    )

    def test_filled_template_becomes_requirements_with_their_posting_lines(self) -> None:
        lines = requirements.posting_lines(POSTING_TEXT + "\nStrong communication skills")
        found, keywords, dropped = requirements.parse_answer(self.ANSWER, lines)
        self.assertEqual([item["name"] for item in found], ["Threat modelling", "Python", "Cloud security"])
        self.assertEqual(dropped, ["Strong communication"])
        self.assertEqual([item["required"] for item in found], [True, False, True])
        self.assertEqual(found[0]["tokens"], ["threat modelling", "STRIDE"])
        self.assertEqual(found[0]["source"], "- Threat modelling for cloud systems")
        self.assertIsNone(found[2]["source"])
        self.assertEqual(keywords, ["threat modelling", "Python", "AWS"])
        # No words for related work: the template no longer asks for them, since no code read them.
        self.assertEqual({key for item in found for key in item}, {"name", "tokens", "required", "line", "source"})
        self.assertIn("\nREQ: <R or P> | <label> | <evidence> | <line>\n", requirements.USER_PROMPT_TEMPLATE)
        self.assertNotIn("adjacent", requirements.USER_PROMPT_TEMPLATE)

    def test_unreadable_answer_is_asked_once_more(self) -> None:
        answers: Iterator[TextAnswer] = iter(
            [
                {"text": "Sure! Here are the requirements.", "output_tokens": 10, "seconds": 100.0},
                {"text": self.ANSWER, "output_tokens": 100, "seconds": 100.0, "answered_by": "claude-sonnet-4-6"},
            ]
        )
        found, _keywords, info = requirements.read_requirements(
            POSTING_TEXT, call=lambda system_prompt, user_prompt: next(answers)
        )
        self.assertEqual((len(found), info["attempts"], info["output_tokens"]), (3, 2, 110))
        self.assertEqual(info["reader"], requirements.READER)
        self.assertEqual(info["dropped"], ["Strong communication"])
        self.assertEqual((info["answered_by"], info["fallback"]), ("claude-sonnet-4-6", None))
        # The call's time is measured around both attempts, not summed from what each answer says.
        self.assertLess(info["seconds"], 100.0)

    def test_soft_items_are_dropped_and_technical_work_worded_like_them_is_kept(self) -> None:
        soft = [
            "Availability to start full-time",
            "Available to work full-time",
            "Start date in January",
            "Internship availability",
            "Prior internship experience",
            "Willingness to relocate",
            "Relocation to London",
            "Work authorization in the US, UK or Canada",
            "Visa sponsorship not available",
            "Strong written and verbal communication skills",
            "Cross-functional collaboration",
            "Collaboration with product and engineering teams",
            "Teamwork",
            "Self-starter",
            "Culture fit",
            "Interpersonal skills",
        ]
        technical = [
            "High availability",
            "ELF relocations",
            "Secure communications",
            "Communicate evaluation results",
            "Purple teaming collaboration with the Blue Team",
            "Communicating actionable threat intelligence",
            "Passion for CTFs",
            "Technical leadership in cross-functional teams",
            "Collaborative filtering",
        ]
        self.assertEqual([label for label in soft if not requirements.is_soft(label)], [])
        self.assertEqual([label for label in technical if requirements.is_soft(label)], [])

    def test_two_unreadable_answers_stop_the_reading(self) -> None:
        asked: list[str] = []

        def call(system_prompt: str, user_prompt: str) -> TextAnswer:
            asked.append(user_prompt)
            return {"text": "Sure! Here are the requirements."}

        with self.assertRaises(model_call.ModelAnswerError):
            requirements.read_requirements(POSTING_TEXT, call=call)
        self.assertEqual(len(asked), 2)
        self.assertTrue(asked[1].endswith(requirements.RETRY_NOTE))


class CapabilityMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        # Call 2's request passes the privacy guard; it checks against a word of its own, not the user's file.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        fingerprints = Path(folder.name) / "identity-fingerprints.txt"
        fingerprints.write_text(privacy.fingerprint("secretname") + "\n", encoding="utf-8")
        patch = mock.patch.object(privacy, "FINGERPRINTS_FILE", fingerprints)
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_identity_word_in_the_request_stops_call_2_before_any_call(self) -> None:
        call = mock.Mock(side_effect=AssertionError("a call was made"))
        named: list[Requirement] = [{"name": "x", "tokens": ["x", "SecretName"], "required": True}]
        plain: list[Requirement] = [{"name": "x", "tokens": ["x"], "required": True}]
        for case, title, found, vocabulary in (
            ("a requirement's word", "Role", named, VOCABULARY),
            ("a capability", "Role", plain, {**VOCABULARY, "SecretName tooling": "Offensive security"}),
            ("the role", "SecretName Engineer", plain, VOCABULARY),
        ):
            with self.subTest(case=case):
                with self.assertRaises(privacy.PrivacyError):
                    capability_mapping.map_requirements(title, found, vocabulary, call=call)
                with self.assertRaises(privacy.PrivacyError):
                    capability_mapping.map_many([(title, found), ("Other", plain)], vocabulary, call=call)
        call.assert_not_called()

    def test_invalid_answer_is_retried_and_names_canonicalised(self) -> None:
        answers: Iterator[JsonAnswer] = iter(
            [
                {"json": {"items": [{"id": 0, "capabilities": ["not a capability"], "conditions": []}]}},
                {"json": {"items": [{"id": 0, "capabilities": ["Threat Modeling"], "conditions": ["Python"]}]}},
            ]
        )
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: next(answers),
        )
        self.assertEqual((record["attempts"], record["valid"]), (2, True))
        self.assertEqual(record["requirements"][0]["capability"], "threat modelling")

    def test_a_requirement_names_up_to_three_alternatives(self) -> None:
        answers: Iterator[JsonAnswer] = iter(
            [
                {"json": {"items": [{"id": 0, "capabilities": ["threat modelling"] * 4, "conditions": []}]}},
                {"json": {"items": [{"id": 0, "capabilities": ["Penetration Testing", "threat modelling"]}]}},
            ]
        )
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: next(answers),
        )
        self.assertEqual(record["first_errors"], ["item 0: capabilities must be a list of at most 3 names"])
        found = record["requirements"][0]
        self.assertEqual(
            (found["capabilities"], found["capability"]),
            (["penetration testing", "threat modelling"], "penetration testing"),
        )
        self.assertEqual(record["format"], store.MAPPING_FORMAT)
        # The role's domains are no longer asked for, checked or saved.
        self.assertNotIn("role_domains", record)

    def test_an_answer_in_the_one_capability_form_is_asked_for_again(self) -> None:
        # An older one-capability form; the current prompt asks for a list.
        self.assertEqual(
            capability_mapping.answer_problems({"items": [{"id": 0, "capability": "threat modelling"}]}, 1, VOCABULARY),
            ["item 0: capabilities must be a list of at most 3 names"],
        )

    def test_a_mapping_from_before_several_capabilities_is_stale(self) -> None:
        current: records.CapabilityMapping = {"vocabulary_sha256_16": "abc", "format": store.MAPPING_FORMAT}
        self.assertTrue(store.is_current(current, "abc", "described"))
        self.assertFalse(store.is_current({"vocabulary_sha256_16": "abc"}, "abc", "described"))

    def test_an_answer_that_is_not_json_is_asked_for_again(self) -> None:
        # call_json gives None when the answer holds no JSON object; the check names it and the retry runs.
        answers: Iterator[JsonAnswer] = iter(
            [{"json": None}, {"json": {"items": [{"id": 0, "capabilities": [], "conditions": []}]}}]
        )
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: next(answers),
        )
        self.assertEqual((record["attempts"], record["valid"]), (2, True))
        self.assertEqual(record["first_errors"], ['the answer must be one JSON object with an "items" list'])

    def test_malformed_items_are_named_and_a_still_broken_answer_is_kept_as_not_valid(self) -> None:
        answer: JsonAnswer = {
            "json": {
                "items": [
                    "threat modelling",
                    {"id": 0, "capabilities": []},
                    {"id": 0, "capabilities": []},
                    {"id": 7},
                ]
            }
        }
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: answer,
        )
        self.assertEqual((record["attempts"], record["valid"]), (2, False))
        self.assertEqual(
            record["errors"],
            [
                "every item must be an object; found 'threat modelling'",
                "item 0 appears more than once",
                "item id 7 is not a requirement number from 0 to 0",
            ],
        )
        self.assertIsNone(record["requirements"][0]["capability"])

    def test_a_still_broken_answer_with_a_list_for_an_id_is_kept_as_not_valid(self) -> None:
        # The record indexes the answer's items by id, which a list cannot be.
        answer: JsonAnswer = {"json": {"items": [{"id": [0], "capabilities": ["threat modelling"]}]}}
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: answer,
        )
        self.assertEqual((record["attempts"], record["valid"]), (2, False))
        self.assertEqual(record["requirements"][0]["capabilities"], [])

    def test_the_model_that_answered_and_its_fallback_are_saved(self) -> None:
        notice: records.Fallback = {
            "original_model": "claude-opus-5-5",
            "fallback_model": "claude-opus-4-8",
            "api_refusal_category": "cyber",
        }
        fallen_back: JsonAnswer = {
            "json": {"items": [{"id": 0, "capabilities": [], "conditions": []}]},
            "answered_by": "claude-opus-4-8",
            "fallback": notice,
        }
        record = capability_mapping.map_requirements(
            "Role",
            [{"name": "x", "tokens": ["x"], "required": True}],
            VOCABULARY,
            call=lambda system_prompt, user_prompt: fallen_back,
        )
        self.assertEqual(
            (record["model"], record["answered_by"], record["fallback"]), ("claude-opus-5-5", "claude-opus-4-8", notice)
        )

    def test_the_vocabulary_is_in_the_system_prompt_only(self) -> None:
        # The system prompt must be the same text for every posting, so the command line's cache write is read back.
        prompts: list[tuple[str, str]] = []

        def call(system_prompt: str, user_prompt: str) -> JsonAnswer:
            prompts.append((system_prompt, user_prompt))
            return {"json": {"items": [{"id": 0, "capabilities": [], "conditions": []}]}}

        for title in ("Role one", "Role two"):
            capability_mapping.map_requirements(
                title, [{"name": title, "tokens": ["x"], "required": True}], VOCABULARY, call=call
            )
        (first_system, first_user), (second_system, second_user) = prompts
        self.assertEqual(first_system, second_system)
        for capability in VOCABULARY:
            self.assertIn(capability, first_system)
            self.assertNotIn(capability, first_user)
        self.assertIn("Role two", second_user)

    def test_the_batch_prompt_rewords_the_opening_and_the_answer_format(self) -> None:
        # str.replace fails silently; a changed SYSTEM_PROMPT would leave the batch prompt asking for one posting.
        batch = capability_mapping.BATCH_SYSTEM_PROMPT
        self.assertIn("several job postings", batch)
        self.assertIn('{"postings": [{"posting": <int>, "items"', batch)
        self.assertNotIn("a job posting onto", batch)
        self.assertNotIn("role_domains", capability_mapping.system_prompt(VOCABULARY) + batch)

    def test_a_batched_answer_gives_each_posting_its_record_from_one_call(self) -> None:
        prompts: list[str] = []

        def call(system_prompt: str, user_prompt: str) -> JsonAnswer:
            prompts.append(system_prompt)
            return {
                "json": {
                    "postings": [
                        {
                            "posting": 0,
                            "items": [{"id": 0, "capabilities": ["Threat Modeling"], "conditions": ["STRIDE"]}],
                        },
                        {"posting": 1, "items": [{"id": 0, "capabilities": [], "conditions": []}]},
                    ]
                },
                "input_tokens": 100,
                "output_tokens": 40,
                "answered_by": "claude-opus-5-5",
                "fallback": None,
            }

        records = capability_mapping.map_many(
            [
                ("Role one", [{"name": "x", "tokens": ["x"], "required": True}]),
                ("Role two", [{"name": "y", "tokens": ["y"], "required": False}]),
            ],
            VOCABULARY,
            call=call,
        )
        self.assertEqual(prompts, [capability_mapping.batch_system_prompt(VOCABULARY)])
        self.assertEqual([record["requirements"][0]["capability"] for record in records], ["threat modelling", None])
        self.assertEqual(
            [(record["valid"], record["batch"], record["output_tokens"]) for record in records],
            [(True, 2, 20), (True, 2, 20)],
        )
        self.assertEqual(records[1]["title"], "Role two")
        self.assertEqual(
            [(record["answered_by"], record["fallback"]) for record in records], [("claude-opus-5-5", None)] * 2
        )

    def test_a_posting_the_batch_leaves_out_is_asked_for_on_its_own(self) -> None:
        prompts: list[str] = []

        def call(system_prompt: str, user_prompt: str) -> JsonAnswer:
            prompts.append(system_prompt)
            if system_prompt == capability_mapping.batch_system_prompt(VOCABULARY):
                return {"json": {"postings": [{"posting": 0, "items": [{"id": 0, "capabilities": []}]}]}}
            return {"json": {"items": [{"id": 0, "capabilities": ["penetration testing"], "conditions": []}]}}

        records = capability_mapping.map_many(
            [
                ("Role one", [{"name": "x", "tokens": ["x"], "required": True}]),
                ("Role two", [{"name": "y", "tokens": ["y"], "required": True}]),
            ],
            VOCABULARY,
            call=call,
        )
        self.assertEqual(
            prompts,
            [capability_mapping.batch_system_prompt(VOCABULARY), capability_mapping.system_prompt(VOCABULARY)],
        )
        self.assertEqual(records[0]["batch"], 2)
        self.assertNotIn("batch", records[1])
        self.assertEqual(records[1]["requirements"][0]["capability"], "penetration testing")

    def test_a_posting_numbered_with_a_list_is_asked_for_on_its_own(self) -> None:
        # The batch's entries are looked up by posting number, which a list cannot be.
        prompts: list[str] = []

        def call(system_prompt: str, user_prompt: str) -> JsonAnswer:
            prompts.append(system_prompt)
            if system_prompt == capability_mapping.batch_system_prompt(VOCABULARY):
                item = {"id": 0, "capabilities": [], "conditions": []}
                return {"json": {"postings": [{"posting": [0], "items": [item]}, {"posting": 1, "items": [item]}]}}
            return {"json": {"items": [{"id": 0, "capabilities": ["penetration testing"], "conditions": []}]}}

        records = capability_mapping.map_many(
            [
                ("Role one", [{"name": "x", "tokens": ["x"], "required": True}]),
                ("Role two", [{"name": "y", "tokens": ["y"], "required": True}]),
            ],
            VOCABULARY,
            call=call,
        )
        self.assertEqual(
            prompts,
            [capability_mapping.batch_system_prompt(VOCABULARY), capability_mapping.system_prompt(VOCABULARY)],
        )
        self.assertEqual([record.get("batch") for record in records], [None, 2])
        self.assertEqual(records[0]["requirements"][0]["capabilities"], ["penetration testing"])

    def test_one_posting_goes_as_a_single_call(self) -> None:
        prompts: list[str] = []

        def call(system_prompt: str, user_prompt: str) -> JsonAnswer:
            prompts.append(system_prompt)
            return {"json": {"items": [{"id": 0, "capabilities": [], "conditions": []}]}}

        records = capability_mapping.map_many(
            [("Role", [{"name": "x", "tokens": ["x"], "required": True}])], VOCABULARY, call=call
        )
        self.assertEqual(prompts, [capability_mapping.system_prompt(VOCABULARY)])
        self.assertNotIn("batch", records[0])

    def test_default_call_asks_opus_5_5_at_medium_effort(self) -> None:
        seen: list[tuple[str, Effort | None]] = []

        def fake_call_json(
            model: str, system_prompt: str, user_prompt: str, effort: Effort | None = None, timeout: float = 600
        ) -> JsonAnswer:
            seen.append((model, effort))
            return {
                "json": {"items": [{"id": 0, "capabilities": [], "conditions": []}]},
                "input_tokens": 0,
                "output_tokens": 0,
            }

        with mock.patch.object(model_call, "call_json", fake_call_json):
            capability_mapping.map_requirements("Role", [{"name": "x", "tokens": ["x"], "required": True}], VOCABULARY)
        self.assertEqual(seen, [("claude-opus-5-5", "medium")])


class SpawnedEnvironment(unittest.TestCase):
    def test_parent_session_variables_are_blanked_and_sign_in_routing_is_kept(self) -> None:
        parent = {
            "CLAUDE_CODE_SESSION_ID": "s",
            "CLAUDE_CODE_MESSAGING_TOKEN": "t",
            "CLAUDE_EFFORT": "high",
            "ANTHROPIC_BASE_URL": "http://localhost:1",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "desktop",
            "PATH": "/bin",
        }
        with mock.patch.dict("os.environ", parent, clear=True):
            overrides = model_call.parent_session_overrides()
        self.assertEqual(
            overrides, {"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_CODE_MESSAGING_TOKEN": "", "CLAUDE_EFFORT": ""}
        )

    def test_a_plain_terminal_overrides_nothing(self) -> None:
        with mock.patch.dict("os.environ", {"PATH": "/bin"}, clear=True):
            self.assertEqual(model_call.parent_session_overrides(), {})


@unittest.skipUnless(importlib.util.find_spec("claude_agent_sdk"), "the Claude Agent SDK is not installed")
class AnsweringModel(unittest.TestCase):
    """The model that wrote an answer, read from the command line's messages; the SDK's query is a stand-in."""

    @staticmethod
    def ask(*messages: object) -> JsonAnswer:
        import claude_agent_sdk

        async def query(*, prompt: str, options: object) -> AsyncIterator[object]:
            for message in messages:
                yield message

        with mock.patch.object(claude_agent_sdk, "query", query):
            return model_call.call_json("claude-opus-5-5", "system", "user")

    def test_a_fallback_is_recorded_with_the_model_that_answered(self) -> None:
        from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock

        notice = {
            "original_model": "claude-opus-5-5",
            "fallback_model": "claude-opus-4-8",
            "api_refusal_category": "cyber",
        }
        answer = self.ask(
            SystemMessage("model_refusal_fallback", {"type": "system", "subtype": "model_refusal_fallback", **notice}),
            AssistantMessage([TextBlock('{"projects": ["P1"]}')], "claude-opus-4-8"),
        )
        self.assertEqual(
            (answer["model"], answer["answered_by"], answer["fallback"], answer["json"]),
            ("claude-opus-5-5", "claude-opus-4-8", notice, {"projects": ["P1"]}),
        )

    def test_an_answer_from_the_model_asked_for_has_no_fallback(self) -> None:
        from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock

        answer = self.ask(
            SystemMessage("init", {"type": "system", "subtype": "init"}),
            AssistantMessage([TextBlock("{}")], "claude-opus-5-5"),
        )
        self.assertEqual((answer["answered_by"], answer["fallback"]), ("claude-opus-5-5", None))


class ReadOnce(unittest.TestCase):
    def test_models_are_called_only_when_the_reading_is_missing_or_stale(self) -> None:
        calls: list[str] = []

        def fake_reader(text: str) -> requirements.RequirementReading:
            calls.append("requirements")
            return requirements.RequirementReading(
                [{"name": "Threat modelling", "tokens": ["threat modelling"], "partial": [], "required": True}],
                ["Python"],
                {"reader": "fake"},
            )

        def fake_mapper(title: str, found: list[Requirement], vocabulary: dict[str, str]) -> records.CapabilityMapping:
            calls.append("mapping")
            return {
                "requirements": [{**found[0], "capability": None, "conditions": []}],
                "valid": True,
                "vocabulary_sha256_16": vocabulary_hash(vocabulary),
                "format": store.MAPPING_FORMAT,
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "library").mkdir()
            (root / "library" / "capabilities.toml").write_text(CAPABILITIES_FILE, encoding="utf-8")
            posting = pipeline.posting_from_text(POSTING_TEXT, "Security Engineer", "X")

            def read_posting(refresh: bool = False) -> pipeline.ReadResult:
                return pipeline.read_posting(
                    posting,
                    refresh=refresh,
                    vocabulary=load_capability_vocabulary(root / "library"),
                    descriptions={},  # the mapper's records are not Jev's, so no description makes them stale
                    readings=root / "readings",
                    mappings=root / "mappings",
                    reader=fake_reader,
                    mapper=fake_mapper,
                )

            first = read_posting()
            second = read_posting()
            # A renamed capability makes the saved mapping stale; the reading is still good.
            (root / "library" / "capabilities.toml").write_text(
                CAPABILITIES_FILE.replace("threat modelling", "threat modeling"), encoding="utf-8"
            )
            third = read_posting()
            fourth = read_posting(refresh=True)
            # A reading file deleted by hand is made again, and its saved mapping, found by the text alone, with it.
            for reading_file in (root / "readings").iterdir():
                reading_file.unlink()
            fifth = read_posting()
        self.assertEqual(first["reading"]["info"]["reader"], "fake")
        self.assertEqual(
            (first["calls"], second["calls"], third["calls"], fourth["calls"], fifth["calls"]),
            (["requirements", "mapping"], [], ["mapping"], ["requirements", "mapping"], ["requirements", "mapping"]),
        )

    def test_a_stand_in_on_the_calls_modules_reaches_the_pipeline(self) -> None:
        # As the dev server stands them in; a real call would reach model_call, which fails here instead.
        def fake_reader(text: str) -> requirements.RequirementReading:
            return requirements.RequirementReading(
                [{"name": "Threat modelling", "tokens": ["threat modelling"], "partial": [], "required": True}],
                [],
                {"reader": "stand-in"},
            )

        def fake_mapper(title: str, found: list[Requirement], vocabulary: dict[str, str]) -> records.CapabilityMapping:
            return {"requirements": [], "valid": True, "vocabulary_sha256_16": vocabulary_hash(vocabulary)}

        no_model = mock.Mock(side_effect=AssertionError("a model call was made"))
        posting = pipeline.posting_from_text(POSTING_TEXT, "Security Engineer", "X")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(model_call, "call_text", no_model),
            mock.patch.object(model_call, "call_json", no_model),
            mock.patch.object(requirements, "read_requirements", fake_reader),
            mock.patch.object(capability_mapping, "map_requirements", fake_mapper),
        ):
            result = pipeline.read_posting(
                posting,
                vocabulary=VOCABULARY,
                descriptions={},
                readings=Path(temporary) / "readings",
                mappings=Path(temporary) / "mappings",
            )
        self.assertEqual(
            (result["reading"]["info"]["reader"], result["calls"]), ("stand-in", ["requirements", "mapping"])
        )

    def test_a_saved_jev_mapping_is_made_again_once_a_description_changes_and_opus_s_is_not(self) -> None:
        def fake_reader(text: str) -> requirements.RequirementReading:
            return requirements.RequirementReading(
                [{"name": "Threat modelling", "tokens": ["threat modelling"], "partial": [], "required": True}],
                [],
                {"reader": "fake"},
            )

        def mapper_as(model: str, library: Path) -> pipeline.Mapper:
            def mapper(title: str, found: list[Requirement], vocabulary: dict[str, str]) -> records.CapabilityMapping:
                # As each call 2 saves it: Jev's with a hash of the library's descriptions it was asked with.
                record: records.CapabilityMapping = {
                    "requirements": [{**found[0], "capability": None, "conditions": []}],
                    "valid": True,
                    "vocabulary_sha256_16": vocabulary_hash(vocabulary),
                    "format": store.MAPPING_FORMAT,
                    "model": model,
                }
                if model.startswith("jev"):
                    record["descriptions_sha256_16"] = descriptions_hash(
                        load_capability_descriptions(library, vocabulary)
                    )
                return record

            return mapper

        posting = pipeline.posting_from_text(POSTING_TEXT, "Security Engineer", "X")
        for model, after_the_edit in (("jev-1.13.0", ["mapping"]), ("claude-opus-5-5", [])):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                library = root / "library"
                library.mkdir()
                (library / "capabilities.toml").write_text(CAPABILITIES_FILE, encoding="utf-8")
                (library / "capability-descriptions.toml").write_text(DESCRIPTIONS_FILE, encoding="utf-8")
                calls = []
                # No vocabulary or descriptions given: the library's are read, as the command line reads them.
                with mock.patch.object(settings, "LIBRARY", library):
                    for edited in (False, False, True):
                        if edited:
                            (library / "capability-descriptions.toml").write_text(
                                DESCRIPTIONS_FILE.replace("STRIDE", "PASTA"), encoding="utf-8"
                            )
                        found = pipeline.read_posting(
                            posting,
                            readings=root / "readings",
                            mappings=root / "mappings",
                            reader=fake_reader,
                            mapper=mapper_as(model, library),
                        )
                        calls.append(found["calls"])
                self.assertEqual(calls, [["requirements", "mapping"], [], after_the_edit])


# ─────────────────────────────────────────────────────────────
# Call 3: the projects that make the strongest case
# ─────────────────────────────────────────────────────────────

CHOICE_PROJECTS = ("P1", "P2", "P3", "P4", "P5", "P6")


def choice_library(summary: str = "Built and measured a detector") -> Library:
    """A stand-in with what call 3 reads: the usable bullets' projects and each project's profile."""
    profiles = {
        project_id: ProjectProfile(f"Project {project_id}", "shipped", "2026-05", "", (), summary)
        for project_id in CHOICE_PROJECTS
    }
    bullets = [SimpleNamespace(parent=project_id) for project_id in CHOICE_PROJECTS for _ in range(2)]
    return cast(Library, SimpleNamespace(usable_bullets=bullets, profiles=profiles))


def choice_answer(projects: list[str], why: str = "Closest to the role.") -> JsonAnswer:
    return {"json": {"projects": projects, "why": why}, "input_tokens": 10, "output_tokens": 5}


class ProjectChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        fingerprints = self.folder / "identity-fingerprints.txt"
        fingerprints.write_text(privacy.fingerprint("secretname") + "\n", encoding="utf-8")
        patch = mock.patch.object(privacy, "FINGERPRINTS_FILE", fingerprints)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_project_list_is_in_the_system_prompt_and_the_posting_in_the_user_text(self) -> None:
        seen: list[tuple[str, str]] = []

        def call(system: str, user: str) -> JsonAnswer:
            seen.append((system, user))
            return choice_answer(["P2", "P1", "P3", "P4"])

        long_posting = "Detection engineer. " + "x" * 9000
        record = project_choice.choose_projects("Detection Engineer", long_posting, choice_library(), call=call)
        system, user = seen[0]
        self.assertIn("PROJECTS (id; title; what it is; status; ended):", system)
        self.assertIn("P3; Project P3; Built and measured a detector; status: shipped; ended: 2026-05", system)
        self.assertNotIn("Detection engineer", system)
        self.assertTrue(user.startswith("POSTING (Detection Engineer):\nDetection engineer."))
        self.assertEqual(len(user), len("POSTING (Detection Engineer):\n") + project_choice.POSTING_CHARACTERS)
        self.assertEqual((record["projects"], record["valid"], record["attempts"]), (["P2", "P1", "P3", "P4"], True, 1))
        self.assertEqual(record["projects_sha256_16"], project_choice.projects_hash(choice_library()))

    def test_a_broken_answer_is_asked_for_again_and_the_second_kept(self) -> None:
        answers = iter([choice_answer(["P1", "P9", "P1"]), choice_answer(["P5", "P1", "P2", "P3", "P4"])])
        users: list[str] = []

        def call(system: str, user: str) -> JsonAnswer:
            users.append(user)
            return next(answers)

        record = project_choice.choose_projects("Role", "posting", choice_library(), call=call)
        self.assertEqual(
            record["first_errors"],
            [
                "'P9' is not a project id in the list",
                "'P1' is chosen twice",
                "choose 4 to 6 projects; the answer has 3",
            ],
        )
        self.assertIn("'P9' is not a project id in the list", users[1])
        self.assertEqual(
            (record["projects"], record["valid"], record["attempts"]), (["P5", "P1", "P2", "P3", "P4"], True, 2)
        )

    def test_an_answer_with_objects_for_ids_is_asked_for_again(self) -> None:
        # The duplicate check puts each id in a dictionary, which an object cannot be.
        objects: JsonAnswer = {"json": {"projects": [{"id": "P1"}, "P2", "P3", "P4"], "why": ""}}
        answers = iter([objects, choice_answer(["P1", "P2", "P3", "P4"])])
        record = project_choice.choose_projects("Role", "posting", choice_library(), call=lambda s, u: next(answers))
        self.assertEqual(record["first_errors"], ['"projects" must be a list of project id strings'])
        self.assertEqual((record["projects"], record["valid"], record["attempts"]), (["P1", "P2", "P3", "P4"], True, 2))

    def test_the_ids_allowed_are_the_projects_not_the_prompt_lines(self) -> None:
        # A line break in a summary starts a new prompt line; its first field is still not a project id.
        library = choice_library("Built a detector\nP9; and more")
        answers = iter([choice_answer(["P9", "P1", "P2", "P3"]), choice_answer(["P1", "P2", "P3", "P4"])])
        record = project_choice.choose_projects("Role", "posting", library, call=lambda s, u: next(answers))
        self.assertEqual(record["first_errors"], ["'P9' is not a project id in the list"])

    def test_the_model_that_answered_and_its_fallback_are_saved(self) -> None:
        notice: records.Fallback = {
            "original_model": "claude-opus-5-5",
            "fallback_model": "claude-opus-4-8",
            "api_refusal_category": "cyber",
        }
        answer: JsonAnswer = {
            **choice_answer(["P1", "P2", "P3", "P4"]),
            "answered_by": "claude-opus-4-8",
            "fallback": notice,
        }
        record = project_choice.choose_projects("Role", "posting", choice_library(), call=lambda s, u: answer)
        self.assertEqual(
            (record["model"], record["answered_by"], record["fallback"]),
            (project_choice.MODEL, "claude-opus-4-8", notice),
        )

    def test_the_projects_hash_is_of_their_content_not_the_list_layout(self) -> None:
        # So the prompt's layout can change (as " | " to "; " did) without making every saved choice stale.
        library = choice_library()
        before = project_choice.projects_hash(library)
        with mock.patch.object(project_choice, "project_list", lambda library: "another layout"):
            self.assertEqual(project_choice.projects_hash(library), before)
        self.assertNotEqual(project_choice.projects_hash(choice_library("Built a scanner")), before)

    def test_a_still_broken_answer_is_kept_as_not_valid_and_is_never_current(self) -> None:
        answers: Iterator[JsonAnswer] = iter(
            [{"json": None}, choice_answer(["P1", "P2", "P3", "P4", "P5", "P6", "P1"])]
        )
        library = choice_library()
        record = project_choice.choose_projects("Role", "posting", library, call=lambda system, user: next(answers))
        self.assertEqual(record["first_errors"], ['the answer must be one JSON object with a "projects" list'])
        self.assertFalse(record["valid"])
        self.assertEqual(record["projects"], ["P1", "P2", "P3", "P4", "P5", "P6"])
        self.assertFalse(store.choice_is_current(record, project_choice.projects_hash(library)))

    def test_an_identity_word_in_the_project_list_stops_the_call(self) -> None:
        calls: list[str] = []

        def call(system: str, user: str) -> JsonAnswer:
            calls.append(user)
            return choice_answer(["P1", "P2", "P3", "P4"])

        with self.assertRaises(privacy.PrivacyError):
            project_choice.choose_projects("Role", "posting", choice_library("Built for SecretName"), call=call)
        self.assertEqual(calls, [])

    def test_default_call_asks_opus_5_at_medium_effort(self) -> None:
        seen: list[tuple[str, Effort | None]] = []

        def fake_call_json(
            model: str, system_prompt: str, user_prompt: str, effort: Effort | None = None, timeout: float = 600
        ) -> JsonAnswer:
            seen.append((model, effort))
            return choice_answer(["P1", "P2", "P3", "P4"])

        with mock.patch.object(model_call, "call_json", fake_call_json):
            project_choice.choose_projects("Role", "posting", choice_library())
        self.assertEqual(seen, [("claude-opus-5", "medium")])

    def test_a_choice_is_made_once_and_again_when_the_project_list_changes(self) -> None:
        made: list[str] = []

        def chooser(title: str, text: str, library: Library) -> records.ProjectChoice:
            made.append(title)
            return project_choice.choose_projects(
                title, text, library, call=lambda system, user: choice_answer(["P1", "P2", "P3", "P4"])
            )

        posting = pipeline.posting_from_text(POSTING_TEXT, "Security Engineer", "X")
        choices = self.folder / "choices"
        rewritten = choice_library("Built, measured and shipped a detector")
        first, first_made = pipeline.project_choice_for(posting, choice_library(), choices=choices, chooser=chooser)
        second, second_made = pipeline.project_choice_for(posting, choice_library(), choices=choices, chooser=chooser)
        # A summary written again changes what the model would read, so the saved choice is stale.
        third, third_made = pipeline.project_choice_for(posting, rewritten, choices=choices, chooser=chooser)
        _fourth, fourth_made = pipeline.project_choice_for(
            posting, rewritten, refresh=True, choices=choices, chooser=chooser
        )
        self.assertEqual((first_made, second_made, third_made, fourth_made), (True, False, True, True))
        self.assertEqual(len(made), 3)
        self.assertEqual(second, first)
        self.assertEqual(first["posting"], posting["id"])
        self.assertNotEqual(third["projects_sha256_16"], first["projects_sha256_16"])


class JsonInAnswers(unittest.TestCase):
    def test_the_last_complete_object_is_taken(self) -> None:
        # The model sometimes writes a draft and then its revision; the two together are not one object.
        self.assertEqual(model_call._json_object('{"projects": ["A"]}\n{"projects": ["B"]}'), {"projects": ["B"]})
        self.assertEqual(model_call._json_object('Note {not json} then {"a": {"b": 1}}.'), {"a": {"b": 1}})
        with self.assertRaisesRegex(ValueError, "no JSON object"):
            model_call._json_object("no object here, [1, 2]")


if __name__ == "__main__":
    unittest.main()
