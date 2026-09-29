"""The command line, with the model calls replaced.

`read` saves the posting and reads it once, refuses a title or company for a posting that carries its own, and stops
with the privacy guard's message when it stops call 2; with --hybrid it makes call 3 only where the engine's match is
low, and says when that answer cannot be used or the privacy guard stops it. Call 2 goes to Opus by default and to Jev
with --call2 jev or TAILOR_CALL2=jev, call 3 to Jev; --classifier opus sends both to Opus; an unknown classifier or
call 2 model stops the command, and a call another model answered in place of the one asked is reported. `page`
builds from the saved reading into data/pages/cli/ and refuses a mapping that is not valid, or Jev's made with other
capability descriptions; with --hybrid it uses call 3's saved projects where the engine's match is low, and says when
none is saved. `word` writes a page into the template. `page` and `word` run on the example library, its
template and its saved answers (examples/). `guard` keeps a word, asked for without echo, as its fingerprint.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from tailor_engine import cli, privacy, settings
from tailor_engine.library import Library
from tailor_engine.privacy import PrivacyError
from tailor_engine.reading import capability_mapping, jev_choice, jev_mapping, pipeline, project_choice, store
from tailor_engine.records import Posting
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_LIBRARY = EXAMPLES / "library"
EXAMPLE_DATA = EXAMPLES / "data"
# Example postings well on either side of `model_projects_below` (example-hybrid-pages.json).
LOW_MATCH, HIGH_MATCH = "paste-f1f45dad", "paste-89f5dd84"


class Read(unittest.TestCase):
    def test_a_pasted_posting_is_saved_and_read(self) -> None:
        seen: list[str] = []

        def fake_read(posting: Posting, refresh: bool = False, **stand_ins: Any) -> dict[str, Any]:
            seen.append(posting["id"])
            return {"calls": [], "mapping": {}}

        with tempfile.TemporaryDirectory() as temporary:
            pasted = Path(temporary) / "posting.txt"
            pasted.write_text("Security Engineer\nRequirements:\n- Python", encoding="utf-8")
            with (
                mock.patch.object(settings, "POSTINGS", Path(temporary) / "postings"),
                mock.patch.object(pipeline, "read_posting", fake_read),
                redirect_stdout(io.StringIO()),
            ):
                cli.main(["read", str(pasted), "--title", "Security Engineer"])
            saved = list((Path(temporary) / "postings").glob("*.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(seen, [saved[0].stem])

    def test_the_privacy_guard_stopping_call_2_stops_the_command_with_its_message(self) -> None:
        def guarded(posting: Posting, refresh: bool = False, **stand_ins: Any) -> dict[str, Any]:
            raise PrivacyError("an identity word is in the text about to be sent; nothing was sent")

        with tempfile.TemporaryDirectory() as temporary:
            pasted = Path(temporary) / "posting.txt"
            pasted.write_text("Security Engineer\nRequirements:\n- Python", encoding="utf-8")
            with (
                mock.patch.object(settings, "POSTINGS", Path(temporary) / "postings"),
                mock.patch.object(pipeline, "read_posting", guarded),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(SystemExit, "an identity word is in the text about to be sent"),
            ):
                cli.main(["read", str(pasted)])

    def test_a_title_is_refused_for_a_posting_that_carries_its_own(self) -> None:
        for source in ("https://example.com/jobs/1", "posting.json"):
            with self.subTest(source=source), self.assertRaisesRegex(SystemExit, "apply to a pasted .txt posting"):
                cli.main(["read", source, "--company", "Example"])


class ReadHybrid(unittest.TestCase):
    """`read --hybrid` on a pasted posting, with calls 1 and 2, the engine's page and call 3 replaced."""

    def read(
        self, engine_match: float, choose: Any, *options: str, mapping: Any = None, reader: Any = None
    ) -> tuple[str, str]:
        """(stdout, stderr) of `read --hybrid` with these options.

        The engine's page matches `engine_match`, `choose` stands in for `pipeline.project_choice_for`, and `reader`
        for `pipeline.read_posting`; `mapping`, when given, is the mapping the stand-in reader's call 2 makes.
        """

        def fake_read(posting: Posting, refresh: bool = False, **stand_ins: Any) -> dict[str, Any]:
            made = mapping or {"requirements": []}
            return {"calls": ["requirements", "mapping"], "reading": {}, "mapping": made}

        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            pasted = Path(temporary) / "posting.txt"
            pasted.write_text("Security Engineer\nRequirements:\n- Python", encoding="utf-8")
            with (
                mock.patch.object(settings, "POSTINGS", Path(temporary) / "postings"),
                mock.patch.object(pipeline, "read_posting", reader or fake_read),
                mock.patch.object(Library, "load", return_value=object()),
                mock.patch.object(cli, "build_page", return_value={"coverage": engine_match}),
                mock.patch.object(pipeline, "project_choice_for", choose),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                cli.main(["read", str(pasted), "--hybrid", *options])
        return output.getvalue(), errors.getvalue()

    def test_call_3_is_made_only_where_the_engines_match_is_low(self) -> None:
        chosen: list[str] = []

        def choose(posting: Posting, library: object, refresh: bool = False, **stand_ins: Any) -> tuple[Any, bool]:
            chosen.append(posting["id"])
            return {"projects": ["A1"], "valid": True}, True

        threshold = DEFAULT_WEIGHTS.model_projects_below
        for engine_match, expected in ((threshold - 0.01, True), (threshold, False)):
            chosen.clear()
            with self.subTest(engine_match=engine_match):
                printed, _errors = self.read(engine_match, choose)
                self.assertEqual(bool(chosen), expected)
                self.assertEqual("projects" in printed.splitlines()[-1], expected)

    def test_an_unusable_call_3_answer_is_reported(self) -> None:
        def choose(posting: Posting, library: object, refresh: bool = False, **stand_ins: Any) -> tuple[Any, bool]:
            return {"projects": [], "valid": False, "errors": ["choose 4 to 6 projects; the answer has 0"]}, True

        printed, errors = self.read(0.1, choose)
        self.assertIn("projects", printed.splitlines()[-1])
        self.assertIn("call 3's answer cannot be used: choose 4 to 6 projects", errors)

    def test_the_privacy_guard_stops_the_command_with_its_message(self) -> None:
        def choose(posting: Posting, library: object, refresh: bool = False, **stand_ins: Any) -> tuple[Any, bool]:
            raise PrivacyError("identity-fingerprints.txt is missing; nothing is sent to a model without it")

        with self.assertRaisesRegex(SystemExit, "identity-fingerprints.txt is missing"):
            self.read(0.1, choose)

    def test_call_2_goes_to_opus_and_call_3_to_jev_by_default_and_each_as_asked(self) -> None:
        answered: list[str] = []

        def choose(posting: Posting, library: object, refresh: bool = False, **stand_ins: Any) -> tuple[Any, bool]:
            # The mapper and chooser the command passes, each asked as the pipeline would ask it.
            answered.append(stand_ins["chooser"]("Role", "text", library)["model"])
            return {"projects": ["A1"], "valid": True}, True

        def mapped_by(posting: Posting, refresh: bool = False, **stand_ins: Any) -> dict[str, Any]:
            answered.append(stand_ins["mapper"]("Role", [], {})["model"])
            return {"calls": ["mapping"], "reading": {}, "mapping": {"requirements": []}}

        # (the classifier and call 2 settings, as TAILOR_CLASSIFIER and TAILOR_CALL2 give them, and the options)
        runs = {
            "default": ("jev", "opus", []),
            "--call2 jev": ("jev", "opus", ["--call2", "jev"]),
            "TAILOR_CALL2=jev": ("jev", "jev", []),
            "--call2 opus": ("jev", "jev", ["--call2", "opus"]),
            "--classifier opus": ("jev", "jev", ["--classifier", "opus"]),
            "--classifier opus --call2 jev": ("jev", "opus", ["--classifier", "opus", "--call2", "jev"]),
            "TAILOR_CLASSIFIER=opus": ("opus", "jev", []),
            "--classifier jev": ("opus", "opus", ["--classifier", "jev"]),
        }
        found = {}
        with (
            mock.patch.object(jev_mapping, "map_requirements", return_value={"model": "jev-latest"}),
            mock.patch.object(capability_mapping, "map_requirements", return_value={"model": "claude-opus-5-5"}),
            mock.patch.object(jev_choice, "choose_projects", return_value={"model": "jev-latest"}),
            mock.patch.object(project_choice, "choose_projects", return_value={"model": "claude-opus-5"}),
        ):
            for name, (classifier, call2, options) in runs.items():
                answered.clear()
                with mock.patch.object(settings, "CLASSIFIER", classifier), mock.patch.object(settings, "CALL2", call2):
                    self.read(0.1, choose, *options, reader=mapped_by)
                found[name] = list(answered)
        # (who answered call 2, who answered call 3)
        jev, opus = ["jev-latest", "jev-latest"], ["claude-opus-5-5", "claude-opus-5"]
        split = ["claude-opus-5-5", "jev-latest"]
        self.assertEqual(
            found,
            {
                "default": split,
                "--call2 jev": jev,
                "TAILOR_CALL2=jev": jev,
                "--call2 opus": split,
                "--classifier opus": opus,
                "--classifier opus --call2 jev": opus,
                "TAILOR_CLASSIFIER=opus": opus,
                "--classifier jev": split,
            },
        )

    def test_an_unknown_classifier_or_call_2_setting_stops_the_command(self) -> None:
        choose = mock.Mock(side_effect=AssertionError("call 3 was made"))
        for setting in ("CLASSIFIER", "CALL2"):
            with (
                self.subTest(setting=setting),
                mock.patch.object(settings, setting, "gpt"),
                self.assertRaisesRegex(SystemExit, "calls 2 and 3 are answered by one of jev, opus, not 'gpt'"),
            ):
                self.read(0.1, choose)

    def test_a_call_answered_in_place_of_jev_is_reported(self) -> None:
        fell_back = {
            "requirements": [],
            "fallback": {
                "original_model": "jev-latest",
                "fallback_model": "claude-opus-5-5",
                "api_refusal_category": None,
            },
            "first_errors": ["Jev: TypeSafe answered HTTP 503", "Jev: TypeSafe answered HTTP 503"],
        }

        def choose(posting: Posting, library: object, refresh: bool = False, **stand_ins: Any) -> tuple[Any, bool]:
            notice = {"original_model": "jev-latest", "fallback_model": "claude-opus-5", "api_refusal_category": None}
            choice = {"projects": ["A1"], "valid": True, "fallback": notice, "first_errors": ["Jev: no Jev key"]}
            return choice, True

        _printed, errors = self.read(0.1, choose, mapping=fell_back)
        self.assertIn(
            "call 2 was answered by claude-opus-5-5 in place of jev-latest: Jev: TypeSafe answered HTTP 503; Jev: "
            "TypeSafe answered HTTP 503",
            errors,
        )
        self.assertIn("call 3 was answered by claude-opus-5 in place of jev-latest: Jev: no Jev key", errors)
        # A saved choice, not made now, is not reported again.
        _printed, errors = self.read(0.1, lambda *arguments, **options: (choose(*arguments, **options)[0], False))
        self.assertNotIn("call 3 was answered", errors)

    def test_the_command_lines_own_fallback_is_reported_by_its_category(self) -> None:
        # Opus 5.5 refused and Opus 4.8 answered: no first errors, a refusal category.
        refused = {"original_model": "claude-opus-5-5", "fallback_model": "claude-opus-4-8"}
        by_category: Any = {"fallback": {**refused, "api_refusal_category": "cyber"}}
        unexplained: Any = {"fallback": {**refused, "api_refusal_category": None}}
        errors = io.StringIO()
        with redirect_stderr(errors):
            cli.show_fallback("call 3", by_category, made=True)
            cli.show_fallback("call 3", unexplained, made=True)
        self.assertEqual(
            errors.getvalue().splitlines(),
            [
                "call 3 was answered by claude-opus-4-8 in place of claude-opus-5-5: cyber",
                "call 3 was answered by claude-opus-4-8 in place of claude-opus-5-5: no reason recorded",
            ],
        )


@unittest.skipUnless((EXAMPLE_LIBRARY / "projects.toml").exists(), "the example library is not present")
class Page(unittest.TestCase):
    def page(self, posting_id: str, *options: str, **folders: Path) -> tuple[dict[str, Any], str]:
        """(the written page, stderr) of `page` on an example posting, from its saved records.

        `folders` replaces settings' folders by name (PAGES, MAPPINGS, CHOICES); a page is written to a temporary
        folder unless `--output` is among `options`.
        """
        posting = EXAMPLE_DATA / "postings" / f"{posting_id}.json"
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            paths: dict[str, Any] = {
                "LIBRARY": EXAMPLE_LIBRARY,
                "WORD_TEMPLATE": EXAMPLE_LIBRARY / "resume-template.docx",
                "READINGS": EXAMPLE_DATA / "readings",
                "MAPPINGS": EXAMPLE_DATA / "mappings",
                "CHOICES": EXAMPLE_DATA / "choices",
                "PAGES": Path(temporary),
                **folders,
            }
            with (
                mock.patch.multiple(settings, **paths),
                redirect_stdout(io.StringIO()),
                redirect_stderr(errors),
            ):
                cli.main(["page", str(posting), *options])
            written = paths["PAGES"] / "cli" / f"{posting_id}.json"
            if "--output" in options:
                written = Path(options[options.index("--output") + 1])
            result: dict[str, Any] = json.loads(written.read_text(encoding="utf-8"))
        return result, errors.getvalue()

    def test_page_is_built_from_the_saved_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "page.json"
            result, _errors = self.page("paste-89f5dd84", "--output", str(output))
        self.assertTrue(result["page"])
        self.assertEqual(result["mode"], "capabilities")

    def test_by_default_the_page_is_kept_apart_from_the_dashboards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages = Path(temporary)
            self.page("paste-89f5dd84", PAGES=pages)
            self.assertEqual(
                sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*.json")),
                ["cli/paste-89f5dd84.json"],
            )

    def test_a_mapping_that_is_not_valid_is_refused(self) -> None:
        posting: Posting = json.loads((EXAMPLE_DATA / "postings" / "paste-89f5dd84.json").read_text(encoding="utf-8"))
        mapping = store.load_mapping(posting["text"], EXAMPLE_DATA / "mappings")
        assert mapping is not None
        with tempfile.TemporaryDirectory() as temporary:
            store.save_mapping(posting["text"], {**mapping, "valid": False}, Path(temporary))
            with self.assertRaisesRegex(SystemExit, "missing, stale or not valid"):
                self.page("paste-89f5dd84", MAPPINGS=Path(temporary))

    def test_a_jev_mapping_made_with_other_capability_descriptions_is_refused(self) -> None:
        posting: Posting = json.loads((EXAMPLE_DATA / "postings" / "paste-89f5dd84.json").read_text(encoding="utf-8"))
        mapping = store.load_mapping(posting["text"], EXAMPLE_DATA / "mappings")
        assert mapping is not None
        by_jev: Any = {
            **mapping,
            "model": "jev-1.13.0",
            "descriptions_sha256_16": Library.load(EXAMPLE_LIBRARY).descriptions_hash,
        }
        edited_since: Any = {**by_jev, "descriptions_sha256_16": "0" * 16}
        with tempfile.TemporaryDirectory() as temporary:
            store.save_mapping(posting["text"], by_jev, Path(temporary))
            self.assertTrue(self.page("paste-89f5dd84", MAPPINGS=Path(temporary))[0]["page"])
            store.save_mapping(posting["text"], edited_since, Path(temporary))
            with self.assertRaisesRegex(SystemExit, "missing, stale or not valid"):
                self.page("paste-89f5dd84", MAPPINGS=Path(temporary))

    def test_hybrid_uses_call_3s_projects_only_where_the_engines_match_is_low(self) -> None:
        for posting_id, expected in ((LOW_MATCH, "model"), (HIGH_MATCH, "engine")):
            with self.subTest(posting=posting_id):
                result, errors = self.page(posting_id, "--hybrid")
                self.assertEqual(result["projects_by"], expected)
                self.assertNotIn("no current call 3 answer", errors)

    def test_hybrid_without_a_current_choice_says_the_engines_page_was_built(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, errors = self.page(LOW_MATCH, "--hybrid", CHOICES=Path(temporary))
        self.assertEqual(result["projects_by"], "engine")
        self.assertIn(f"{LOW_MATCH}: the engine's match is", errors)
        self.assertIn("no current call 3 answer is saved", errors)

    @unittest.skipUnless((EXAMPLE_LIBRARY / "resume-template.docx").exists(), "the example template is not present")
    def test_word_writes_a_page_into_the_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "page.json"
            self.page(LOW_MATCH, "--hybrid", "--output", str(page))
            document = Path(temporary) / "resume.docx"
            printed = io.StringIO()
            template = mock.patch.object(settings, "WORD_TEMPLATE", EXAMPLE_LIBRARY / "resume-template.docx")
            with template, redirect_stdout(printed):
                cli.main(["word", str(page), "--output", str(document)])
            self.assertTrue(document.exists())
            self.assertEqual(printed.getvalue().strip(), f"written: {document.resolve()}")


class Guard(unittest.TestCase):
    def test_a_word_asked_for_without_echo_is_kept_as_its_fingerprint(self) -> None:
        printed = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            listed = Path(temporary) / "identity-fingerprints.txt"
            with (
                mock.patch.object(privacy, "FINGERPRINTS_FILE", listed),
                mock.patch("getpass.getpass", return_value="exampleaccount"),
                redirect_stdout(printed),
            ):
                cli.main(["guard"])
            kept = listed.read_text(encoding="utf-8")
        self.assertIn(privacy.fingerprint("exampleaccount"), kept)
        self.assertNotIn("exampleaccount", kept)
        self.assertEqual(printed.getvalue().strip(), f"fingerprint added to {listed}")


if __name__ == "__main__":
    unittest.main()
