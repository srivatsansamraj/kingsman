"""The command line.

  python -m tailor_engine read URL | FILE    fetch (or take a pasted .txt, or a posting .json), show its
                                             facts, and make its reading and mapping (model calls, once);
                                             --hybrid also makes call 3 when the engine's match is low;
                                             --call2 jev has Jev answer call 2, not Opus; --classifier opus
                                             has Opus answer calls 2 and 3, not Jev
  python -m tailor_engine page POSTING       build the page from the saved reading; no model call; --hybrid
                                             uses call 3's saved projects where the engine's match is low
  python -m tailor_engine word PAGE --output resume.docx
                                             write a built page into the Word template
  python -m tailor_engine guard              add a word that must never reach a model provider (asked for
                                             without echo; kept as a fingerprint in local/)

POSTING is a posting id under data/postings/ or a posting .json file; PAGE is a page .json file, such as one `page`
wrote to data/pages/cli/.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from . import privacy, settings
from .layout import PROJECT_BODY_LINES
from .library import Library
from .privacy import PrivacyError
from .reading import store
from .reading.facts import posting_facts
from .reading.project_choice import projects_hash
from .records import CallRecord, PageResult, Posting, ProjectChoice
from .selection.page import Refused, build_hybrid_page, build_page
from .selection.selector import MAX_PROJECTS
from .selection.weights import DEFAULT_WEIGHTS

RULE = "=" * 96

if TYPE_CHECKING:
    from .reading.pipeline import Chooser, Mapper


def _load_posting(argument: str) -> Posting:
    path = Path(argument)
    if path.suffix == ".json" and path.exists():
        from_file: Posting = json.loads(path.read_text(encoding="utf-8"))
        return from_file
    posting = store.load_posting(argument)
    if posting is None:
        raise SystemExit(f"no posting '{argument}' in {settings.POSTINGS}, and no such .json file")
    return posting


def _read(arguments: argparse.Namespace) -> int:
    # Imported here: the reading modules need httpx and the Claude Agent SDK, which `page` and `word` do not.
    from .reading.fetch import FetchError
    from .reading.model_call import ModelAnswerError
    from .reading.pipeline import posting_from_text, posting_from_url, project_choice_for, read_posting

    source = arguments.source
    pasted = not source.startswith(("http://", "https://")) and not source.endswith(".json")
    if (arguments.title or arguments.company) and not pasted:
        raise SystemExit("--title and --company apply to a pasted .txt posting")
    mapper, chooser = calls_answered_by(arguments.classifier, arguments.call2)
    try:
        if source.startswith(("http://", "https://")):
            posting = posting_from_url(source)
        elif source.endswith(".json"):
            posting = json.loads(Path(source).read_text(encoding="utf-8"))
        else:
            posting = posting_from_text(Path(source).read_text(encoding="utf-8"), arguments.title, arguments.company)
    except (FetchError, OSError) as error:
        raise SystemExit(str(error)) from error
    store.save_posting(posting)
    print(f"posting {posting['id']}: {posting['title']} ({posting['company']})")
    print(json.dumps(posting_facts(posting), indent=1))
    try:
        read_result = read_posting(posting, refresh=arguments.refresh, mapper=mapper)
    except (ModelAnswerError, PrivacyError) as error:  # call 2's request passes the privacy guard, as call 3's does
        raise SystemExit(str(error)) from error
    calls = list(read_result["calls"])
    show_fallback("call 2", read_result["mapping"], made="mapping" in calls)
    if arguments.hybrid:
        library = Library.load()
        try:
            engine = build_page(posting, read_result["reading"], library, read_result["mapping"]["requirements"])
        except Refused as refusal:
            raise SystemExit(str(refusal)) from refusal
        if engine["coverage"] < DEFAULT_WEIGHTS.model_projects_below:
            try:
                choice, made = project_choice_for(posting, library, refresh=arguments.refresh, chooser=chooser)
            except PrivacyError as error:
                raise SystemExit(str(error)) from error
            calls += ["projects"] if made else []
            show_fallback("call 3", choice, made=made)
            if not choice.get("valid"):
                # Saved all the same, but `page --hybrid` and the dashboard pass it over for the engine's projects.
                print("call 3's answer cannot be used: " + "; ".join(choice.get("errors") or []), file=sys.stderr)
    print(f"model calls: {', '.join(calls) or 'none, already read'}")
    return 0


def calls_answered_by(classifier: str, call2: str | None = None) -> tuple[Mapper, Chooser]:
    """Calls 2 and 3 as the command line has them answered.

    Call 3 by `classifier`; call 2 by `call2` (by default `settings.CALL2`), or by Opus when the classifier is Opus. An
    unknown model (a mistyped TAILOR_CLASSIFIER or TAILOR_CALL2) stops the command.
    """
    from .reading.pipeline import call2_mapper, call2_model, call3_chooser  # imported here, as in `_read`

    try:
        return call2_mapper(call2_model(classifier, call2)), call3_chooser(classifier)
    except ValueError as error:
        raise SystemExit(str(error)) from error


def show_fallback(call: str, record: CallRecord, *, made: bool) -> None:
    """Print to stderr that another model answered in place of the one asked, and why, for a record made now."""
    notice = record.get("fallback")
    if made and notice:
        print(
            f"{call} was answered by {notice['fallback_model']} in place of {notice['original_model']}: "
            + "; ".join(record.get("first_errors") or [notice.get("api_refusal_category") or "no reason recorded"]),
            file=sys.stderr,
        )


def show_library_warnings(library: Library) -> None:
    """Print the library's warnings to stderr.

    A warning names what the library loaded with but may not do as meant, such as a link to a bullet that does not
    exist.
    """
    for warning in library.warnings:
        print(f"library warning: {warning}", file=sys.stderr)


def show_unmapped(result: PageResult) -> None:
    """Print to stderr the requirements the posting's mapping does not name, which only their words could meet."""
    if result.get("unmapped"):
        print(
            f"{result.get('posting_id')}: not in the posting's mapping, so met by words alone: "
            f"{'; '.join(result['unmapped'])}"
            " (read the posting again with --refresh)",
            file=sys.stderr,
        )


def _print_page(result: PageResult) -> None:
    print(RULE)
    print("POSTING:", result["posting"])
    print(RULE)
    extraction = result["extraction"]
    print(
        f"{extraction['requirements']} requirements ({extraction['required']} required), "
        f"{extraction['keywords']} keywords"
    )
    print(f"mode {result['mode']}, reach {result['reach']:.2f}, unmet word demand {result['unmet']:.0%}")
    print("demand:")
    for tag, weight in sorted(result["demand"].items(), key=lambda item: -item[1])[:8]:
        print(f"    {tag:22s} {weight:.2f}  {'#' * int(weight * 44)}")
    print(
        f"\n{result['bullets']} bullets in {len(result['page'])} projects, "
        f"{result['project_lines']}/{PROJECT_BODY_LINES} project lines"
    )
    print(
        f"  coverage {result['coverage']:.2f}   emphasis {result['emphasis']:.2f}   standing {result['standing']:.2f}"
        f"   incoherence {result['incoherence']:.2f}   cost {result['cost']:.2f}   depth {result['depth']:.2f}"
        f"   score {result['score']:.3f}"
    )
    for project in result["page"]:
        print(f"\n  {project['title']}")
        for bullet in project["bullets"]:
            print("      " + bullet)
    if result["missing_required"]:
        print("\n  required, not fully met:", ", ".join(result["missing_required"]))
    if result.get("projects_by") == "model":
        print(f"\n  projects chosen by call 3 (the engine's match was {result['engine_match']:.2f}):")
        print("  " + result["choice"]["why"])


def _page(arguments: argparse.Namespace) -> int:
    """Build and print the page for a posting, and save it.

    The page needs the posting's mapping made under the current vocabulary and passing its check; a missing, stale or
    not valid one stops the command rather than building on the wrong capabilities. The page is saved in
    data/pages/cli/ by default, apart from the dashboard's own pages in data/pages/, which it edits and serves.
    """
    posting = _load_posting(arguments.posting)
    reading = store.load_reading(posting["text"], posting_id=posting.get("id"))
    if reading is None:
        raise SystemExit("this posting has no saved reading; run `read` on it first")
    library = Library.load()
    show_library_warnings(library)
    mapping_record = store.load_mapping(posting["text"])
    if mapping_record is None or store.mapping_needs_call(
        mapping_record, library.vocabulary_hash, library.descriptions_hash
    ):
        raise SystemExit("this posting's capability mapping is missing, stale or not valid; run `read` on it again")
    mapped_requirements = mapping_record["requirements"]
    current: ProjectChoice | None = None
    try:
        if arguments.hybrid:
            choice = store.load_choice(posting["text"])
            current = choice if store.choice_is_current(choice, projects_hash(library)) else None
            result = build_hybrid_page(
                posting, reading, library, mapped_requirements, current, max_projects=arguments.projects
            )
        else:
            result = build_page(posting, reading, library, mapped_requirements, max_projects=arguments.projects)
    except Refused as refusal:
        raise SystemExit(str(refusal)) from refusal
    _print_page(result)
    show_unmapped(result)
    if arguments.hybrid and current is None and result["coverage"] < DEFAULT_WEIGHTS.model_projects_below:
        print(
            f"{posting.get('id')}: the engine's match is {result['coverage']:.2f} and no current call 3 answer is "
            "saved, so the engine's page was built; run `read --hybrid` on it",
            file=sys.stderr,
        )
    destination = arguments.output or settings.PAGES / "cli" / f"{posting.get('id', 'posting')}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"\nwritten: {destination}")
    return 0


def _word(arguments: argparse.Namespace) -> int:
    # Imported here, so `page` and `read` start without loading the Word writer and lxml.
    from .rendering.document import document_from_page
    from .rendering.word import write_tailored_docx

    try:
        result = json.loads(arguments.page.read_text(encoding="utf-8"))
        document = document_from_page(result)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    written = write_tailored_docx(settings.WORD_TEMPLATE, arguments.output, document, overwrite=arguments.overwrite)
    print(f"written: {written}")
    return 0


def _guard(_arguments: argparse.Namespace) -> int:
    # Asked for without echo, so the word never lands in the terminal or the shell's history.
    privacy.add_identity_word(getpass.getpass("word to keep from every model call (not shown): "))
    print(f"fingerprint added to {privacy.FINGERPRINTS_FILE}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tailor_engine", description="Choose resume content for a job posting.")
    commands = parser.add_subparsers(dest="command", required=True)

    read_parser = commands.add_parser("read", help="fetch or paste a posting and read it with the models, once")
    read_parser.add_argument("source", help="a posting URL, a pasted posting .txt, or a posting .json")
    read_parser.add_argument("--title", default="", help="the role title, for a pasted posting")
    read_parser.add_argument("--company", default="", help="the company, for a pasted posting")
    read_parser.add_argument("--refresh", action="store_true", help="read it again even if a reading is saved")
    read_parser.add_argument("--hybrid", action="store_true", help="also make call 3 when the engine's match is low")
    read_parser.add_argument(
        "--classifier",
        choices=settings.CLASSIFIERS,
        default=settings.CLASSIFIER,
        help="who answers call 3: Jev, with Opus when it cannot, or Opus alone, which answers call 2 too (default "
        "TAILOR_CLASSIFIER, else jev)",
    )
    read_parser.add_argument(
        "--call2",
        choices=settings.CLASSIFIERS,
        default=settings.CALL2,
        help="who answers call 2 when the classifier is Jev: Opus, or Jev with Opus when it cannot (default "
        "TAILOR_CALL2, else opus)",
    )
    read_parser.set_defaults(handler=_read)

    page_parser = commands.add_parser("page", help="build the page from a posting's saved reading")
    page_parser.add_argument("posting", help="a posting id under data/postings/, or a posting .json")
    page_parser.add_argument("--projects", type=int, default=MAX_PROJECTS, help="most projects on the page")
    page_parser.add_argument(
        "--hybrid", action="store_true", help="call 3's saved projects where the engine's match is low"
    )
    page_parser.add_argument("--output", type=Path, help="where to write the page (default data/pages/cli/<id>.json)")
    page_parser.set_defaults(handler=_page)

    word_parser = commands.add_parser("word", help="write a built page into the Word template")
    word_parser.add_argument("page", type=Path, help="a page .json written by `page`")
    word_parser.add_argument("--output", type=Path, required=True, help="the .docx to write")
    word_parser.add_argument("--overwrite", action="store_true", help="replace an existing file")
    word_parser.set_defaults(handler=_word)

    guard_parser = commands.add_parser("guard", help="add a word that must never reach a model provider")
    guard_parser.set_defaults(handler=_guard)

    arguments = parser.parse_args(argv)
    code: int = arguments.handler(arguments)
    return code
