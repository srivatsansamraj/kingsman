"""The measurement tools, on generated data.

The engine's privacy guard (in front of calls 2 and 3 and both judges, its refusal naming the command that adds a
word), the benchmark judge never called with a listed word, the check on the judge's answer, and the agreement measure;
the benchmark's refusal of a mapping that is not valid and of a project choice that is not current, the names of its
records, and the missing index named when there is no benchmark; `read`, which makes call 3 only with --choices;
`read` and `run` answering calls 2 and 3 as the classifier and call 2 settings say, or as --classifier names. A timed
run from URL to judged page, with the fetch and the model calls stood in, runs on the example library and its
saved answers (examples/).
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from tailor_bench import agreement, dataset, end_to_end, judge
from tailor_bench import cli as bench_cli
from tailor_bench.judge import verdict_problems
from tailor_engine import privacy, settings
from tailor_engine.library import Library
from tailor_engine.reading import capability_mapping, jev_choice, jev_mapping, pipeline, project_choice, store
from tailor_engine.reading.requirements import RequirementReading
from tailor_engine.records import Bullet, Posting, ProjectChoice


class PrivacyGuard(unittest.TestCase):
    def patched_fingerprints_file(self, contents: str | None) -> AbstractContextManager[Any]:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "identity-fingerprints.txt"
        if contents is not None:
            path.write_text(contents, encoding="utf-8")
        return mock.patch.object(privacy, "FINGERPRINTS_FILE", path)

    def test_a_listed_word_stops_the_text_wherever_it_sits(self) -> None:
        with self.patched_fingerprints_file(None):
            privacy.add_identity_word("exampleaccount")
            self.assertNotIn("exampleaccount", privacy.FINGERPRINTS_FILE.read_text(encoding="utf-8"))
            privacy.check_outgoing("a project about agent containment")
            for text in (
                "published under ExampleAccount",
                "package org.exampleaccount.app",
                "author 12+EXAMPLEACCOUNT",
                "see github.com/someone",
                "write to someone@outlook.com",  # any email address, not only one provider's
            ):
                with self.subTest(text=text), self.assertRaises(privacy.PrivacyError):
                    privacy.check_outgoing(text)

    def test_only_whole_single_words_can_be_listed(self) -> None:
        with self.patched_fingerprints_file(None), self.assertRaises(ValueError):
            privacy.add_identity_word("two words")

    def test_nothing_is_sent_without_the_fingerprints_file(self) -> None:
        with self.patched_fingerprints_file(None), self.assertRaisesRegex(privacy.PrivacyError, "missing"):
            privacy.check_outgoing("anything")
        # The refusal names the command that adds a word, so a first run knows what to do.
        with (
            self.patched_fingerprints_file(None),
            self.assertRaisesRegex(privacy.PrivacyError, "with: python -m tailor_engine guard$"),
        ):
            privacy.check_outgoing("anything")
        with (
            self.patched_fingerprints_file("# only a comment\n"),
            self.assertRaisesRegex(privacy.PrivacyError, "no fingerprints"),
        ):
            privacy.check_outgoing("anything")

    def test_the_judge_is_never_called_with_a_listed_word_in_the_library(self) -> None:
        library: Any = SimpleNamespace(
            degrees={"D1": SimpleNamespace(award="MS", institution="Example University", dates="2026")},
            bullets=[
                Bullet(
                    id="A1.1", parent="A1", text="Built a scanner, published as ExampleAccount", tags={}, evidence=[]
                )
            ],
            profiles={"A1": SimpleNamespace(title="Scanner", facts="", ends="present", ends_note="", notes=())},
            projects={"A1": SimpleNamespace(title="Scanner")},
        )
        posting: Any = {"text": "A posting."}
        model = mock.Mock()
        with self.patched_fingerprints_file(None), mock.patch.object(judge, "call_json", model):
            privacy.add_identity_word("exampleaccount")
            with self.assertRaises(privacy.PrivacyError):
                judge.judge_page(posting, [("A1", ["A1.1"])], library)
        model.assert_not_called()

    def test_operational_notes_are_recognised(self) -> None:
        self.assertTrue(privacy.OPERATIONAL_NOTES.search("Publish this under a new package name"))
        self.assertFalse(privacy.OPERATIONAL_NOTES.search("Trained a detector on local data"))


class JudgeAnswer(unittest.TestCase):
    PAGE = [("A1", ["A1.1", "A1.2"]), ("B2", ["B2.1"])]

    def test_a_complete_answer_passes(self) -> None:
        verdict = {
            "projects": [{"project": "A1", "belongs": True}, {"project": "B2", "belongs": False}],
            "bullets": [
                {"id": "A1.1", "relevant": True},
                {"id": "A1.2", "relevant": True},
                {"id": "B2.1", "relevant": False},
            ],
            "missing": ["C3"],
            "why": "Fits.",
        }
        self.assertEqual(verdict_problems(verdict, self.PAGE, {"A1", "B2", "C3"}), [])

    def test_missing_verdicts_and_unknown_projects_are_named(self) -> None:
        verdict = {
            "projects": [{"project": "A1", "belongs": True}],
            "bullets": [{"id": "A1.1", "relevant": True}],
            "missing": ["Z9"],
        }
        problems = verdict_problems(verdict, self.PAGE, {"A1", "B2"})
        self.assertEqual(
            problems,
            [
                "give one project verdict for each of ['A1', 'B2']",
                "give one bullet verdict for each of ['A1.1', 'A1.2', 'B2.1']",
                "missing lists unknown projects ['Z9']",
            ],
        )

    def test_an_answer_that_is_not_an_object_is_named(self) -> None:
        self.assertEqual(
            verdict_problems(["not", "an", "object"], self.PAGE, {"A1", "B2"}), ["the answer must be one JSON object"]
        )

    def test_malformed_items_and_flags_are_named_not_raised(self) -> None:
        verdict = {"projects": ["A1", {"project": "B2", "belongs": "yes"}], "bullets": "B2.1", "missing": []}
        problems = verdict_problems(verdict, self.PAGE, {"A1", "B2"})
        self.assertIn('"bullets" must be a list', problems)
        self.assertIn("""every item of "projects" must be an object; found 'A1'""", problems)
        self.assertIn(""""belongs" must be true or false in {'project': 'B2', 'belongs': 'yes'}""", problems)


class AgreementTests(unittest.TestCase):
    def test_projects_and_shared_bullets_are_compared_separately(self) -> None:
        mine = [("A1", ["A1.1", "A1.2"]), ("B2", ["B2.1"])]
        theirs = [("A1", ["A1.1"]), ("C3", ["C3.1"])]
        projects, bullets = agreement.compare(mine, theirs)
        self.assertAlmostEqual(projects, 1 / 3)
        self.assertEqual(bullets, [0.5])

    def test_the_summary_averages_both_advisors_and_counts_refusals_apart(self) -> None:
        advisors = {
            ("p1", "labeller-1"): [("A1", ["A1.1"])],
            ("p1", "labeller-2"): [("A1", ["A1.1"]), ("B2", ["B2.1"])],
        }
        summary = agreement.summarise(
            {"p1": [("A1", ["A1.1"])], "p2": None}, lambda posting_id, advisor: advisors[(posting_id, advisor)]
        )
        self.assertEqual(summary, {"projects": 0.75, "bullets": 1.0, "pages": 1, "refused": 1})

    def test_projects_split_after_labelling_are_left_out_of_the_bullet_figure(self) -> None:
        with mock.patch.object(agreement, "PROJECTS_WITHOUT_COMPARABLE_BULLETS", frozenset({"B1"})):
            self.assertEqual(agreement.compare([("B1", ["B1.1"])], [("B1", ["B1.2"])]), (1.0, []))


class BenchmarkMapping(unittest.TestCase):
    def test_a_mapping_that_is_not_valid_stops_the_benchmark(self) -> None:
        library: Any = SimpleNamespace(vocabulary_hash="abc", descriptions_hash="described")
        posting: Any = {"id": "p1", "text": "a posting"}
        mapping = {"vocabulary_sha256_16": "abc", "format": store.MAPPING_FORMAT, "valid": True, "requirements": []}
        by_jev = {**mapping, "model": "jev-1.13.0", "descriptions_sha256_16": "described"}
        for current in (mapping, by_jev):
            with mock.patch.object(dataset, "mapping", return_value=current):
                self.assertEqual(bench_cli._mapped_requirements("p1", posting, library), [])
        # Not valid, or Jev's made with capability descriptions since edited.
        for stale in ({**mapping, "valid": False}, {**by_jev, "descriptions_sha256_16": "edited"}):
            with (
                mock.patch.object(dataset, "mapping", return_value=stale),
                self.assertRaisesRegex(SystemExit, "p1 has no current, valid mapping"),
            ):
                bench_cli._mapped_requirements("p1", posting, library)

    def test_a_hybrid_run_without_a_current_choice_names_the_command_that_makes_it(self) -> None:
        library: Any = SimpleNamespace(warnings=[], vocabulary_hash="abc", descriptions_hash="described")
        mapping = {"vocabulary_sha256_16": "abc", "format": store.MAPPING_FORMAT, "valid": True, "requirements": []}
        with (
            mock.patch.object(Library, "load", return_value=library),
            mock.patch.object(bench_cli, "projects_hash", return_value="0123"),
            mock.patch.object(dataset, "index", return_value=[{"id": "p1", "band": "lane:x"}]),
            mock.patch.object(dataset, "posting", return_value={"id": "p1", "text": "a posting"}),
            mock.patch.object(dataset, "reading", return_value={"requirements": [], "keywords": []}),
            mock.patch.object(dataset, "mapping", return_value=mapping),
            mock.patch.object(dataset, "choice", return_value=None),
            self.assertRaisesRegex(SystemExit, "p1 has no current project choice .*tailor_bench read --choices"),
        ):
            bench_cli.main(["benchmark", "--hybrid"])


class BenchmarkRecords(unittest.TestCase):
    def saved_name(self, hybrid: bool) -> list[str]:
        """The record files `benchmark` writes without a label."""
        arguments = argparse.Namespace(label=None, hybrid=hybrid, weights=[], handler=None)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(dataset, "RUNS", Path(temporary)):
            with redirect_stdout(io.StringIO()):
                bench_cli._save_benchmark(arguments, {}, {}, {})
            return sorted(path.name for path in Path(temporary).iterdir())

    def test_a_hybrid_run_does_not_replace_the_plain_runs_record(self) -> None:
        self.assertEqual(self.saved_name(hybrid=False), ["benchmark-capabilities.json"])
        self.assertEqual(self.saved_name(hybrid=True), ["benchmark-capabilities-hybrid.json"])

    def test_without_a_benchmark_the_tools_name_the_missing_index_and_the_example(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(dataset, "BENCHMARK_FOLDER", Path(temporary)),
            self.assertRaisesRegex(SystemExit, "index.json is missing.*TAILOR_BENCHMARK=examples/benchmark"),
        ):
            dataset.index()

    def test_a_run_with_other_weights_must_be_named(self) -> None:
        with (
            mock.patch.object(Library, "load", mock.Mock(side_effect=AssertionError("the library was loaded"))),
            self.assertRaisesRegex(SystemExit, "name a run with other weights with --label"),
        ):
            bench_cli.main(["benchmark", "--weights", "family_boost=0"])


class ReadCommand(unittest.TestCase):
    """`read` on two postings, with calls 1, 2 and 3 stood in."""

    def read(self, choose: Any, *options: str, reader: Any = None) -> tuple[list[str], str]:
        """(each printed line with its spacing collapsed, stderr) of `read` with `choose` standing in for call 3.

        `reader`, when given, stands in for `pipeline.read_posting`.
        """

        def read_posting(posting: Posting, **options: Any) -> dict[str, list[str]]:
            self.assertEqual((options["readings"], options["mappings"]), (dataset.READINGS, dataset.MAPPINGS))
            return {"calls": []}

        output, errors = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(dataset, "index", return_value=[{"id": "p1"}, {"id": "p2"}]),
            mock.patch.object(
                dataset, "posting", side_effect=lambda posting_id: {"id": posting_id, "text": posting_id}
            ),
            mock.patch.object(pipeline, "read_posting", reader or read_posting),
            mock.patch.object(pipeline, "project_choice_for", choose),
            mock.patch.object(Library, "load", return_value=object()),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            bench_cli.main(["read", *options])
        return [" ".join(line.split()) for line in output.getvalue().splitlines()], errors.getvalue()

    def test_choices_makes_call_3_for_every_posting_whose_choice_is_missing_or_stale(self) -> None:
        chosen: list[str] = []

        def choose(posting: Posting, library: object, *, choices: Path, chooser: Any) -> tuple[ProjectChoice, bool]:
            self.assertEqual(choices, dataset.CHOICES)
            chosen.append(posting["id"])
            if posting["id"] == "p2":
                return {
                    "projects": ["A1"],
                    "valid": False,
                    "errors": ["choose 4 to 6 projects; the answer has 1"],
                }, True
            return {"projects": ["A1", "A2", "A3", "A4"], "valid": True}, True

        printed, errors = self.read(choose, "--choices")
        self.assertEqual((sorted(chosen), printed), (["p1", "p2"], ["p1 projects", "p2 projects"]))
        self.assertIn("p2 call 3's answer cannot be used: choose 4 to 6 projects", " ".join(errors.split()))
        chosen.clear()
        printed, errors = self.read(choose)
        self.assertEqual((chosen, printed, errors), ([], ["p1 already read", "p2 already read"], ""))

    def test_the_privacy_guard_stops_the_command_with_its_message(self) -> None:
        def choose(posting: Posting, library: object, *, choices: Path, chooser: Any) -> tuple[ProjectChoice, bool]:
            raise privacy.PrivacyError("identity-fingerprints.txt is missing; nothing is sent to a model without it")

        with self.assertRaisesRegex(SystemExit, "identity-fingerprints.txt is missing"):
            self.read(choose, "--choices")

    def test_read_and_run_answer_calls_2_and_3_as_the_setting_says_or_as_the_option_names(self) -> None:
        answered: list[str] = []

        def choose(posting: Posting, library: object, *, choices: Path, chooser: Any) -> tuple[ProjectChoice, bool]:
            answered.append(chooser("Role", "text", library)["model"])
            return {"projects": ["A1", "A2", "A3", "A4"], "valid": True}, False

        def mapped_by(posting: Posting, **options: Any) -> dict[str, list[str]]:
            answered.append(options["mapper"]("Role", [], {})["model"])
            return {"calls": []}

        def run(url: str, label: str, **options: Any) -> Path:
            answered.append(options["mapper"]("Role", [], {})["model"])
            answered.append(options["chooser"]("Role", "text", object())["model"])
            return Path(label)

        # (the classifier and call 2 settings, as TAILOR_CLASSIFIER and TAILOR_CALL2 give them, and the options)
        runs = {
            "default": ("jev", "opus", []),
            "TAILOR_CALL2=jev": ("jev", "jev", []),
            "--classifier opus": ("jev", "jev", ["--classifier", "opus"]),
            "TAILOR_CLASSIFIER=opus": ("opus", "jev", []),
        }
        found = {}
        with (
            mock.patch.object(jev_mapping, "map_requirements", return_value={"model": "jev-latest"}),
            mock.patch.object(capability_mapping, "map_requirements", return_value={"model": "claude-opus-5-5"}),
            mock.patch.object(jev_choice, "choose_projects", return_value={"model": "jev-latest"}),
            mock.patch.object(project_choice, "choose_projects", return_value={"model": "claude-opus-5"}),
            mock.patch.object(bench_cli, "run", run),
        ):
            for name, (classifier, call2, options) in runs.items():
                answered.clear()
                with (
                    mock.patch.multiple(settings, CLASSIFIER=classifier, CALL2=call2),
                    redirect_stdout(io.StringIO()),
                ):
                    self.read(choose, "--choices", *options, reader=mapped_by)
                    bench_cli.main(["run", "https://example.com/jobs/1", "--label", "x", *options])
                found[name] = sorted(answered)  # `read` works on its postings in threads
        jev, opus = ["jev-latest", "jev-latest"], ["claude-opus-5-5", "claude-opus-5"]
        split = ["claude-opus-5-5", "jev-latest"]  # call 2 on Opus, call 3 on Jev
        # Per run: `read` maps and chooses for its two postings, `run` for its one.
        self.assertEqual(
            found,
            {
                "default": sorted(split * 3),
                "TAILOR_CALL2=jev": sorted(jev * 3),
                "--classifier opus": sorted(opus * 3),
                "TAILOR_CLASSIFIER=opus": sorted(opus * 3),
            },
        )


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_LIBRARY = EXAMPLES / "library"
EXAMPLE_DATA = EXAMPLES / "data"
# The engine's match on this example posting is well under `model_projects_below` (example-hybrid-pages.json).
LOW_MATCH = "paste-f1f45dad"


@unittest.skipUnless(
    (EXAMPLE_LIBRARY / "projects.toml").exists() and EXAMPLE_DATA.exists(),
    "the example library or its saved answers are not present",
)
class TimedRun(unittest.TestCase):
    """`run` on a low-match posting, its fetch and calls 1, 2 and 3 answered from the example's saved records."""

    def test_a_low_match_makes_call_3_and_the_run_keeps_its_records_out_of_data(self) -> None:
        saved: Posting = json.loads((EXAMPLE_DATA / "postings" / f"{LOW_MATCH}.json").read_text(encoding="utf-8"))
        posting: Posting = {**saved, "board": {}}
        reading = store.load_reading(posting["text"], LOW_MATCH, EXAMPLE_DATA / "readings")
        mapping = store.load_mapping(posting["text"], EXAMPLE_DATA / "mappings")
        saved_choice = store.load_choice(posting["text"], EXAMPLE_DATA / "choices")
        assert reading is not None and mapping is not None and saved_choice is not None
        info = {"seconds": 1.0, "input_tokens": 1, "output_tokens": 1}
        asked: list[str] = []

        def answered_by(model: str) -> Any:
            def choose(title: str, text: str, library: Library) -> ProjectChoice:
                asked.append(f"call 3 on {model}")
                return saved_choice

            def map_requirements(*arguments: Any) -> Any:
                asked.append(f"call 2 on {model}")
                return mapping

            return map_requirements, choose

        judged: list[Posting] = []

        def judge_page(run_posting: Posting, page: Any, library: Library) -> dict[str, Any]:
            judged.append(run_posting)
            return {"verdict": {"why": "stood in"}}

        with tempfile.TemporaryDirectory() as temporary:
            runs, product = Path(temporary) / "runs", Path(temporary) / "product"
            stores: dict[str, Any] = {
                name: product / name.lower() for name in ("POSTINGS", "READINGS", "MAPPINGS", "CHOICES")
            }
            with (
                mock.patch.object(dataset, "RUNS", runs),
                mock.patch.object(end_to_end, "RUNS", runs),
                mock.patch.multiple(settings, **stores, CLASSIFIER="jev", CALL2="opus", LIBRARY=EXAMPLE_LIBRARY),
                mock.patch.object(pipeline, "posting_from_url", return_value=posting),
                mock.patch.object(
                    end_to_end,
                    "read_requirements",
                    return_value=RequirementReading(reading["requirements"], reading["keywords"], info),
                ),
                mock.patch.multiple(jev_mapping, map_requirements=answered_by("Jev")[0]),
                mock.patch.multiple(jev_choice, choose_projects=answered_by("Jev")[1]),
                mock.patch.multiple(capability_mapping, map_requirements=answered_by("Opus")[0]),
                mock.patch.multiple(project_choice, choose_projects=answered_by("Opus")[1]),
                redirect_stdout(io.StringIO()),
            ):
                folder = end_to_end.run("https://example.com/jobs/1", "check", with_judge=False)
                # A run given its own data folder keeps its records there; `judge` finds the posting in either.
                elsewhere = Path(temporary) / "elsewhere"
                bench_cli.main(
                    [
                        "run",
                        "https://example.com/jobs/1",
                        "--label",
                        "other",
                        "--data",
                        str(elsewhere),
                        "--no-judge",
                        "--classifier",
                        "opus",
                    ]
                )
                with mock.patch.object(bench_cli, "judge_page", judge_page):
                    bench_cli.main(["judge", "check"])
                    bench_cli.main(["judge", "other", "--data", str(elsewhere)])
            page = json.loads((folder / dataset.PAGE_FILE).read_text(encoding="utf-8"))
            steps = {
                step["step"]: step for step in json.loads((folder / dataset.TIMINGS_FILE).read_text(encoding="utf-8"))
            }
            kept = sorted(path.parent.name for path in (folder / "data").rglob("*.json"))
            kept_elsewhere = sorted(path.parent.name for path in elsewhere.rglob("*.json"))
            self.assertFalse((runs / "other" / "data").exists())
            self.assertFalse(product.exists(), "the run wrote into the product's store")
        # Each run asks calls 2 and 3, the first as the settings say (call 2 on Opus, call 3 on the classifier's Jev)
        # and the second on the Opus it names; the second finds nothing saved in its own folder.
        self.assertEqual(page["projects_by"], "model")
        self.assertEqual(asked, ["call 2 on Opus", "call 3 on Jev", "call 2 on Opus", "call 3 on Opus"])
        self.assertEqual(steps["call 3: projects"]["input_tokens"], saved_choice["input_tokens"])
        self.assertEqual(kept, ["choices", "mappings", "postings", "readings"])
        self.assertEqual(kept_elsewhere, kept)
        self.assertEqual([run_posting["id"] for run_posting in judged], [posting["id"], posting["id"]])


class TimedRunCallThree(unittest.TestCase):
    def test_call_3_is_charged_only_when_made_and_an_unusable_answer_is_named(self) -> None:
        choice: ProjectChoice = {
            "valid": False,
            "errors": ["choose 4 to 6 projects; the answer has 1"],
            "input_tokens": 9,
            "output_tokens": 2,
        }
        from_the_store, made_now = {"step": "call 3: projects"}, {"step": "call 3: projects"}
        printed = io.StringIO()
        with redirect_stdout(printed):
            end_to_end._note_call_3(from_the_store, {**choice, "valid": True}, made=False)
            end_to_end._note_call_3(made_now, choice, made=True)
        self.assertEqual(from_the_store, {"step": "call 3: projects"})
        self.assertEqual(made_now, {"step": "call 3: projects", "input_tokens": 9, "output_tokens": 2})
        self.assertEqual(
            printed.getvalue(), "  call 3's answer cannot be used: choose 4 to 6 projects; the answer has 1\n"
        )


if __name__ == "__main__":
    unittest.main()
