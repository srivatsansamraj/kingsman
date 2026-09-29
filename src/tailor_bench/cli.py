"""The benchmark's command line.

python -m tailor_bench run URL --label NAME [--data FOLDER] [--no-judge] [--classifier jev|opus]
      one posting from URL to judged hybrid page, every step timed (model calls: 2 if new, call 3 if the engine's
      match is low, and the judge); the posting and its records are saved in FOLDER, by default
      benchmark/runs/NAME/data/, never in data/; --classifier names who answers call 3, and call 2 too when Opus, as
      the engine's `read` (default TAILOR_CLASSIFIER, else jev); call 2 is otherwise answered as TAILOR_CALL2 says,
      else by Opus
python -m tailor_bench benchmark [--hybrid] [--weights name=value ...] [--label NAME]
      the benchmark's postings against the advisors, by band, from saved readings (no model call); --hybrid uses
      call 3's saved project choices (benchmark/choices) where the engine's match is low; the record is
      benchmark/runs/benchmark-NAME.json, and a run with --weights must be named
python -m tailor_bench read [--choices] [--classifier jev|opus]
      read and map any benchmark posting whose reading or mapping is missing or stale, three at a time; --choices also
      makes call 3 for any whose project choice is missing or stale; --classifier as for `run`
python -m tailor_bench judge LABEL [--data FOLDER]
      judge the page a `run` saved again
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tailor_engine import settings
from tailor_engine.cli import calls_answered_by, show_library_warnings, show_unmapped
from tailor_engine.library import Library
from tailor_engine.privacy import PrivacyError
from tailor_engine.reading import pipeline, store
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.records import Posting, Requirement
from tailor_engine.rendering.judge_input import PageIds, page_of
from tailor_engine.selection.page import Refused, build_hybrid_page, build_page
from tailor_engine.selection.weights import with_changes

from . import dataset
from .agreement import Agreement, summarise
from .end_to_end import run
from .judge import judge_page

# At most three model calls at once: the command line's rate limits and the machine both prefer it.
PARALLEL_CALLS = 3


def _benchmark(arguments: argparse.Namespace) -> int:
    """Agreement with the advisors on the benchmark postings, by band, from saved readings.

    No model is called: a posting without a saved reading or a current mapping stops the run and names the command that
    makes them. A run with other weights must be named, so its record does not replace the plain run's.
    """
    if arguments.weights and not arguments.label:
        raise SystemExit("name a run with other weights with --label")
    library = Library.load()
    show_library_warnings(library)
    weights = with_changes(arguments.weights)
    listing_hash = projects_hash(library)
    by_model = 0
    pages: dict[str, PageIds | None] = {}
    bands: dict[str, str] = {}
    refusals: dict[str, str] = {}
    for entry in dataset.index():
        posting_id = entry["id"]
        posting = dataset.posting(posting_id)
        reading = dataset.reading(posting["text"], posting_id)
        if reading is None:
            raise SystemExit(f"{posting_id} has no saved reading; run `python -m tailor_bench read` first")
        mapping = _mapped_requirements(posting_id, posting, library)
        bands[posting_id] = dataset.band_of(entry)
        try:
            if arguments.hybrid:
                choice = dataset.choice(posting["text"])
                if not store.choice_is_current(choice, listing_hash):
                    raise SystemExit(
                        f"{posting_id} has no current project choice in {dataset.CHOICES}; "
                        "run `python -m tailor_bench read --choices` first"
                    )
                result = build_hybrid_page(posting, reading, library, mapping, choice, weights=weights)
                by_model += result["projects_by"] == "model"
            else:
                result = build_page(posting, reading, library, mapping, weights=weights)
            show_unmapped(result)
            pages[posting_id] = page_of(result)
        except Refused as refusal:
            pages[posting_id], refusals[posting_id] = None, str(refusal)
    agreement_by_band: dict[str, Agreement] = {"all": summarise(pages)}
    for band in dataset.BANDS:
        agreement_by_band[band] = summarise(
            {posting_id: page for posting_id, page in pages.items() if bands[posting_id] == band}
        )
    _print_agreement(agreement_by_band)
    if arguments.hybrid:
        print(f"hybrid: the model's projects make {by_model} of {len(pages) - len(refusals)} pages")
    _save_benchmark(arguments, agreement_by_band, refusals, pages)
    return 0


def _mapped_requirements(posting_id: str, posting: Posting, library: Library) -> list[Requirement]:
    """The posting's saved capability mapping, when it is current and passed its check."""
    mapping_record = dataset.mapping(posting["text"])
    if mapping_record is None or store.mapping_needs_call(
        mapping_record, library.vocabulary_hash, library.descriptions_hash
    ):
        raise SystemExit(f"{posting_id} has no current, valid mapping; run `python -m tailor_bench read` first")
    return mapping_record["requirements"]


def _print_agreement(agreement_by_band: dict[str, Agreement]) -> None:
    print(f"{'':10s} {'projects':>9s} {'bullets':>8s} {'pages':>6s} {'refused':>8s}")
    for band, agreement in agreement_by_band.items():
        print(
            f"{band:10s} {_figure(agreement['projects']):>9s} {_figure(agreement['bullets']):>8s} "
            f"{agreement['pages']:6d} {agreement['refused']:8d}"
        )


def _save_benchmark(
    arguments: argparse.Namespace,
    agreement_by_band: dict[str, Agreement],
    refusals: dict[str, str],
    pages: dict[str, PageIds | None],
) -> None:
    # Without a label the record keeps the name it had when the benchmark ran in two modes; a hybrid run has a name of
    # its own, so it does not replace the plain run's record.
    label = arguments.label or ("capabilities-hybrid" if arguments.hybrid else "capabilities")
    destination = dataset.RUNS / f"benchmark-{label}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "options": vars(arguments) | {"handler": None},
                "summary": agreement_by_band,
                "refused": refusals,
                "pages": pages,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\nwritten: {destination}")


def _figure(value: float | None) -> str:
    # A band with no page built has no agreement figure; "-" keeps it apart from a real 0.
    return "-" if value is None else f"{value:.3f}"


def _read(arguments: argparse.Namespace) -> int:
    # Call 3 is made for every posting, whatever the engine's match: the benchmark tries other thresholds on them.
    library = Library.load() if arguments.choices else None
    mapper, chooser = calls_answered_by(arguments.classifier)

    def read_one(entry: dict[str, str]) -> tuple[str, list[str], list[str]]:
        """(the posting's id, the model calls made, why its call 3 answer cannot be used)."""
        posting = dataset.posting(entry["id"])
        found = pipeline.read_posting(posting, readings=dataset.READINGS, mappings=dataset.MAPPINGS, mapper=mapper)
        calls = found["calls"]
        if library is None:
            return entry["id"], calls, []
        choice, made = pipeline.project_choice_for(posting, library, choices=dataset.CHOICES, chooser=chooser)
        unusable = [] if choice.get("valid") else choice.get("errors") or ["the answer is not valid"]
        return entry["id"], calls + (["projects"] if made else []), unusable

    try:
        with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
            for posting_id, calls, unusable in pool.map(read_one, dataset.index()):
                print(f"  {posting_id:12s} {', '.join(calls) or 'already read'}", flush=True)
                if unusable:
                    print(f"  {posting_id:12s} call 3's answer cannot be used: {'; '.join(unusable)}", file=sys.stderr)
    except PrivacyError as error:
        raise SystemExit(str(error)) from error
    return 0


def _run(arguments: argparse.Namespace) -> int:
    mapper, chooser = calls_answered_by(arguments.classifier)
    folder = run(
        arguments.url,
        arguments.label,
        with_judge=not arguments.no_judge,
        data=arguments.data,
        mapper=mapper,
        chooser=chooser,
    )
    print(f"\nwritten: {folder}")
    return 0


def _judge(arguments: argparse.Namespace) -> int:
    folder = dataset.RUNS / arguments.label
    result = json.loads((folder / dataset.PAGE_FILE).read_text(encoding="utf-8"))
    postings = (arguments.data or dataset.run_data(arguments.label)) / "postings"
    posting = store.load_posting(result["posting_id"], postings) if result.get("posting_id") else None
    if posting is None:
        raise SystemExit(f"the run's posting is not in {postings}")
    judged = judge_page(posting, page_of(result), Library.load())
    (folder / dataset.JUDGE_FILE).write_text(json.dumps(judged, indent=1), encoding="utf-8")
    print(json.dumps(judged["verdict"], indent=1))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tailor_bench", description="Measure the engine against the benchmark.")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="one posting from URL to judged page, timed")
    run_parser.add_argument("url")
    run_parser.add_argument("--label", required=True, help="folder name under benchmark/runs/")
    run_parser.add_argument(
        "--data", type=Path, help="where the posting and its records are saved (default benchmark/runs/<label>/data/)"
    )
    run_parser.add_argument("--no-judge", action="store_true")
    _classifier_option(run_parser)
    run_parser.set_defaults(handler=_run)

    benchmark_parser = commands.add_parser("benchmark", help="agreement with the advisors on the benchmark's postings")
    benchmark_parser.add_argument(
        "--weights",
        nargs="*",
        default=[],
        metavar="NAME=VALUE",
        help="try other weights, e.g. family_boost=0 (names in selection/weights.py)",
    )
    benchmark_parser.add_argument(
        "--hybrid", action="store_true", help="call 3's saved projects where the engine's match is low"
    )
    benchmark_parser.add_argument("--label", help="names the output benchmark/runs/benchmark-<label>.json")
    benchmark_parser.set_defaults(handler=_benchmark)

    read_parser = commands.add_parser("read", help="read and map benchmark postings missing a current reading")
    read_parser.add_argument(
        "--choices", action="store_true", help="also make call 3 for postings whose project choice is missing or stale"
    )
    _classifier_option(read_parser)
    read_parser.set_defaults(handler=_read)

    judge_parser = commands.add_parser("judge", help="judge the page a run saved")
    judge_parser.add_argument("label")
    judge_parser.add_argument("--data", type=Path, help="the run's data folder, if not the default")
    judge_parser.set_defaults(handler=_judge)

    arguments = parser.parse_args(argv)
    code: int = arguments.handler(arguments)
    return code


def _classifier_option(command: argparse.ArgumentParser) -> None:
    # The engine's `read` option, with its default.
    command.add_argument(
        "--classifier",
        choices=settings.CLASSIFIERS,
        default=settings.CLASSIFIER,
        help="who answers call 3: Jev, with Opus when it cannot, or Opus alone, which answers call 2 too (default "
        "TAILOR_CLASSIFIER, else jev); call 2 is otherwise answered as TAILOR_CALL2 says, else by Opus",
    )
