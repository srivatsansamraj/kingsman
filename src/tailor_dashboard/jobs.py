"""Jobs as the dashboard shows them: the engine's saved records joined with the tracker.

A job is read when it has a saved reading and a usable mapping made under the current capability vocabulary. Its page is
built from those with no model call and saved in the command line's form at `data/pages/<id>.json` (the command line
writes its own to `data/pages/cli/`), with the Word file beside it (`<id>.docx`), which the page preview is taken from
so it shows exactly what prints. In hybrid mode (`page_modes`) a page whose engine match is low is built from the
projects call 3 chose, saved in `data/choices/`. A saved page is built again when the library, the reading, the
mapping, the mode or the choice it came from has changed. Call 3 is answered by the classifier the dashboard was
started with (`settings.CLASSIFIER`): Jev, with Opus when it cannot answer, or Opus alone. Call 2 is answered as the
page mode says when a reading starts (Accuracy: Opus; Speed: Jev, with Opus when it cannot answer), or by Opus when it
is the classifier.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast

from tailor_engine import settings
from tailor_engine.library import Library
from tailor_engine.privacy import PrivacyError
from tailor_engine.reading import jev, jev_choice, project_choice, store
from tailor_engine.reading.jev import JevUnavailableError
from tailor_engine.reading.jev_choice import Chooser
from tailor_engine.reading.model_call import ModelAnswerError
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.records import CapabilityMapping, PageResult, Posting, ProjectChoice, Reading, Requirement
from tailor_engine.rendering.document import document_from_page
from tailor_engine.rendering.word import write_tailored_docx
from tailor_engine.selection.capability_matching import capabilities_of
from tailor_engine.selection.page import Refused, build_hybrid_page, build_page
from tailor_engine.selection.weights import DEFAULT_WEIGHTS

from .display import (
    STATES,
    WORK_MODES,
    answered,
    blockers_of,
    facts_of,
    pay_text,
    places,
    printed_paragraphs,
    shown_names,
    state_of,
)
from .edits import applied, bullet_entries, described, edit_problem
from .page_modes import PageModes, PageModesFile
from .review import review_job
from .tracker import Tracker, now

LOG = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Paths:
    postings: Path
    readings: Path
    mappings: Path
    choices: Path
    pages: Path
    library: Path
    template: Path
    tracker: Path
    modes: Path

    @classmethod
    def default(cls) -> Paths:
        return cls.under(settings.DATA, settings.LIBRARY)

    @classmethod
    def under(cls, data: Path, library: Path) -> Paths:
        return cls(
            postings=data / "postings",
            readings=data / "readings",
            mappings=data / "mappings",
            choices=data / "choices",
            pages=data / "pages",
            library=library,
            template=library / "resume-template.docx",
            tracker=data / "tracker.sqlite",
            modes=data / "page-modes.json",
        )


# ─────────────────────────────────────────────────────────────
# The library, loaded again when it changes
# ─────────────────────────────────────────────────────────────


class LibraryCache:
    """The library and a fingerprint of the files a page depends on.

    The files are checked by size and modification time on each use (a few `stat` calls); the library is loaded again
    when one changed, and once a day, since projects' ages are measured from today.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.Lock()
        self._stamp: tuple[object, ...] | None = None
        self._library: Library | None = None
        self._fingerprint = ""

    def _files(self) -> list[Path]:
        return sorted([*self.directory.glob("*.toml"), self.directory / "resume-template.docx"])

    def get(self) -> tuple[Library, str]:
        """(the library, the fingerprint of its files), loaded again when a file or the date has changed."""
        files = self._files()
        stats = [(path.name, path.stat()) for path in files]
        stamp = (dt.date.today(), *((name, stat.st_size, stat.st_mtime_ns) for name, stat in stats))
        with self._lock:
            if stamp != self._stamp or self._library is None:
                self._library = Library.load(self.directory)
                digest = hashlib.sha256()
                for path in files:
                    digest.update(path.name.encode() + b"\0" + path.read_bytes())
                self._fingerprint = digest.hexdigest()[:16]
                self._stamp = stamp
            return self._library, self._fingerprint


# ─────────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────────


# Pasted text shorter than this is taken for a mistake (a word typed into the bar), not a posting. Set by hand.
SHORTEST_POSTING = 200
# The reading-time estimate is the median of this many most recent calls of each kind. Set by hand.
RECENT_CALLS = 10
# The estimate for a model call before any has been timed, by the model that answers it. Set by hand; Opus's call 3 is
# its measured median, Jev's call 3 its measured median of 0.5 s a request, and Jev's call 2 a posting's requests
# sent at once, 1.45 s (measured medians of 1.36 to 1.55 s).
SECONDS_BEFORE_ANY_CALL = {
    "opus": {"requirements": 30.0, "mapping": 30.0, "projects": 10.8},
    "jev": {"requirements": 30.0, "mapping": 1.45, "projects": 0.5},
}
# The judge's time for each length before an answer of that length is kept: the medians of a test on
# Opus 5.5 (nine brief answers, three detailed).
JUDGE_SECONDS_BEFORE_ANY = {"brief": 37.5, "detailed": 67.0}

Mapper = Callable[[str, list[Requirement], dict[str, str]], CapabilityMapping]


class Jobs:
    """Every job the dashboard tracks: adding, reading, building, judging and editing one, and what the page reads."""

    def __init__(self, paths: Paths, *, classifier: str | None = None):
        classifier = classifier or settings.CLASSIFIER
        # Call 2's setting is the page mode's default, so a mistyped TAILOR_CALL2 stops the start as a classifier does.
        for model in (classifier, settings.CALL2):
            if model not in settings.CLASSIFIERS:
                raise ValueError(
                    f"calls 2 and 3 are answered by one of {', '.join(settings.CLASSIFIERS)}, not {model!r}"
                )
        self.paths = paths
        # Who answers call 3: "jev" (Jev, and Opus where it cannot answer) or "opus" (Opus, and call 2 too, in batches).
        self.classifier = classifier
        self.tracker = Tracker(paths.tracker)
        self.library_cache = LibraryCache(paths.library)
        self.modes = PageModesFile(paths.modes)
        # The library the project list's hash was taken from, and the hash: the list is the same until it is reloaded.
        self._listing: tuple[Library, str] | None = None
        # One lock per job, held by every change to it (build, decide, undo), so two changes cannot lose one; reentrant,
        # since decide and undo build the page again while holding it.
        self._job_locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()
        # The call-time estimate, kept until a file is added to or replaced in the readings, mappings or choices folder
        # (the store writes beside a file and renames it over, which changes the folder's modification time), or the
        # model answering call 2 or 3 changes.
        self._seconds: tuple[tuple[object, ...], dict[str, float]] | None = None

    def call2_model(self, modes: PageModes | None = None) -> str:
        """Who answers call 2 under these modes, by default the saved ones: Opus as the classifier, else the mode's.

        The page mode's choice is Opus for Accuracy and Jev for Speed.
        """
        # The rule of `pipeline.call2_model`, not imported here: building pages needs no httpx, which the pipeline does.
        return "opus" if self.classifier == "opus" else (modes or self.modes.get()).call2

    # Records ----------------------------------------------------

    def posting(self, posting_id: str) -> Posting | None:
        return store.load_posting(posting_id, self.paths.postings)

    def records(self, posting: Posting) -> tuple[Reading | None, CapabilityMapping | None]:
        reading = store.load_reading(posting["text"], posting_id=posting["id"], directory=self.paths.readings)
        mapping = store.load_mapping(posting["text"], directory=self.paths.mappings)
        return reading, mapping

    def current_choice(self, posting: Posting, library: Library) -> ProjectChoice | None:
        """Call 3's saved answer for this posting when it was made from the library's project list as it is now."""
        choice = store.load_choice(posting["text"], self.paths.choices)
        return choice if store.choice_is_current(choice, self._listing_hash(library)) else None

    def _listing_hash(self, library: Library) -> str:
        kept = self._listing
        if kept is None or kept[0] is not library:
            kept = (library, projects_hash(library))
            self._listing = kept
        return kept[1]

    def calls_needed(self, posting: Posting) -> list[str]:
        """The model calls reading this posting would make: "requirements", "mapping", both, or none."""
        library, _fingerprint = self.library_cache.get()
        return calls_needed_for(*self.records(posting), library)

    def add(self, source: str) -> tuple[str, bool]:
        """Fetch a posting by URL, or take pasted text, save it and track it; (its id, whether it is newly tracked).

        No model call. A posting already saved under the same id is kept as it is, so its reading still matches its
        text. Raises ValueError on text too short to be a posting, and the fetcher's FetchError.
        """
        # Imported here: fetching needs httpx, which building pages from saved readings does not.
        from tailor_engine.reading.pipeline import posting_from_text, posting_from_url

        source = source.strip()
        if re.match(r"https?://", source, re.IGNORECASE):
            posting = posting_from_url(source)
        elif len(source) < SHORTEST_POSTING:
            raise ValueError("that looks too short for a posting; paste its URL or the whole text")
        else:
            posting = posting_from_text(source)
        if self.posting(posting["id"]) is None:
            store.save_posting(posting, self.paths.postings)
        row = self.tracker.row(posting["id"])
        if row is not None and row["removed"]:
            # Added again after it was removed from the list: it comes back as it was.
            self.tracker.update(posting["id"], removed=None)
            return posting["id"], True
        return posting["id"], self.tracker.add(posting["id"])

    def read(
        self,
        posting_id: str,
        on_stage: Callable[[str], None],
        *,
        refresh: bool = False,
        call1_slot: AbstractContextManager[Any],
        mappers: dict[str, Mapper],
        choices: Executor,
        jev_choices: Executor,
        jev_slot: AbstractContextManager[Any],
    ) -> list[str]:
        """Make whatever reading, mapping or project choice the posting lacks, then build its page; the calls made.

        With `refresh` every call is made again. `on_stage` hears "call1", "call2" and "call3" as each model call starts
        and "building" before the page is built. A job still at "added" moves to "read". Call 1 waits for `call1_slot`,
        call 2 goes through `mappers[model]`, the model the modes name as the reading starts (`call2_model`: Jev, or
        Opus's batches), and Opus's call 3 runs on `choices`. In hybrid mode with Jev as the classifier, Jev's call 3
        starts beside call 1 on every posting, on `jev_choices`, holding `jev_slot` while its request is in flight, and
        its answer is saved whether this page uses it or not. Opus's call 3 is made only for a page whose match is under
        the threshold: when Opus is the classifier, or when Jev could not answer.
        """
        # Imported here: the pipeline imports the fetcher, which needs httpx; building from saved readings does not.
        from tailor_engine.reading.pipeline import project_choice_for, read_posting
        from tailor_engine.reading.requirements import RequirementReading, read_requirements

        posting = self.posting(posting_id)
        if posting is None:
            raise LookupError(f"no posting {posting_id}")

        def reader(text: str) -> RequirementReading:
            with call1_slot:
                on_stage("call1")
                return read_requirements(text)

        modes = self.modes.get()
        # Decided once for the reading: a switch while it runs applies to the next one.
        mapper = mappers[self.call2_model(modes)]

        def map_with_stage(
            title: str, requirements: list[Requirement], vocabulary: dict[str, str]
        ) -> CapabilityMapping:
            on_stage("call2")
            return mapper(title, requirements, vocabulary)

        library, _fingerprint = self.library_cache.get()

        def choose(chooser: Chooser | None) -> bool:
            # No chooser is Opus's call 3 (`project_choice.choose_projects`, looked up when it is made).
            return project_choice_for(posting, library, refresh=refresh, choices=self.paths.choices, chooser=chooser)[1]

        def jev_chooses(title: str, text: str, listed: Library) -> ProjectChoice:
            # Looked up on its module when called, so the dev server and the tests can stand in.
            return jev_choice.ask(title, text, listed, slot=jev_slot)

        # Jev's call 3 costs about 0.5 s and a fraction of a cent, so it starts on every posting and a low match waits
        # for nothing. It is asked without its fallback: Opus is asked only for a page that needs it.
        jev_first = modes.hybrid and self.classifier == "jev"
        early_choice = jev_choices.submit(choose, jev_chooses) if jev_first else None
        try:
            result = read_posting(
                posting,
                refresh=refresh,
                vocabulary=library.capability_vocabulary,
                descriptions=library.capability_descriptions,
                readings=self.paths.readings,
                mappings=self.paths.mappings,
                reader=reader,
                mapper=map_with_stage,
            )
            calls = list(result["calls"])
            on_stage("building")
            page = self.build(posting_id)
        except BaseException:
            # The reading failed; an early call 3 still running is logged if it fails.
            if early_choice is not None:
                early_choice.add_done_callback(_log_early_failure)
            raise
        if modes.hybrid and page is not None and is_low(page):
            made = _low_match_choice(early_choice, choose, choices, on_stage)
            calls += ["projects"] if made else []
            # Jev's early call 3 usually answers during call 1, and the first page was then built from its choice.
            if made and not self.page_is_current(posting_id):
                on_stage("building")
                self.build(posting_id)
        elif early_choice is not None:
            early_choice.add_done_callback(_log_early_failure)  # its answer is kept for later; nothing waits for it
        row = self.tracker.row(posting_id)
        if row is not None and row["status"] == "added":
            self.tracker.update(posting_id, status="read")
        return calls

    def judge(self, posting_id: str, level: str) -> None:
        """Ask the judge about this job's page and keep the answer (the last one only).

        Raises LookupError when the job has no page, and ModelAnswerError when the answer is still unusable after it
        was asked for again (the previous answer is kept then).
        """
        posting = self.posting(posting_id)
        row = self.tracker.row(posting_id)
        page = self.page(posting_id)
        if posting is None or row is None or page is None or row["page_refused"]:
            raise LookupError(f"job {posting_id} has no page to judge")
        library, _fingerprint = self.library_cache.get()
        title, company, _known = shown_names(posting, row)
        role = (title, company)
        review = review_job(posting, page, library, level, role=role)
        if review["problems"]:
            raise ModelAnswerError("the judge's answer could not be read: " + "; ".join(review["problems"]))
        record = {**review, "at": now(), "page_fingerprint": row["page_fingerprint"]}
        self.tracker.update(posting_id, judge=json.dumps(record))

    def judge_seconds(self) -> dict[str, float]:
        """The median time of the judge's kept answers of each length, for the time shown before asking."""
        kept: dict[str, list[float]] = {level: [] for level in JUDGE_SECONDS_BEFORE_ANY}
        for row in self.tracker.rows().values():
            if row["judge"]:
                answer = json.loads(row["judge"])
                if answer["level"] in kept:
                    kept[answer["level"]].append(answer["seconds"])
        return {
            level: sorted(seconds)[len(seconds) // 2] if seconds else JUDGE_SECONDS_BEFORE_ANY[level]
            for level, seconds in kept.items()
        }

    def call_seconds(self) -> dict[str, float]:
        """The median seconds of each model call over its most recent saved records, for the cost shown first.

        Only the latest `RECENT_CALLS` count: the calls' cost changes with their prompts (call 2 halved between
        2026-09-22 and 09-23, from a median of 60 s to 28 s), and older records would keep the estimate high. Calls 2
        and 3 each count only the records of the model that answers that call now (`call2_model`, and the classifier
        for call 3): Jev's take about a second, Opus's several. Opus's answers in Jev's place count for Jev as well.
        """
        stamp = (
            self.classifier,
            self.call2_model(),
            *(
                directory.stat().st_mtime_ns if directory.exists() else 0
                for directory in (self.paths.readings, self.paths.mappings, self.paths.choices)
            ),
        )
        kept = self._seconds
        if kept is not None and kept[0] == stamp:
            return dict(kept[1])
        estimate = self._call_seconds()
        self._seconds = (stamp, estimate)
        return dict(estimate)

    def _call_seconds(self) -> dict[str, float]:
        seconds: dict[str, list[float]] = {}
        # The model answering calls 2 and 3 now; call 1's records all count, whoever answered them.
        models = {"requirements": self.classifier, "mapping": self.call2_model(), "projects": self.classifier}
        for kind, directory in (
            ("requirements", self.paths.readings),
            ("mapping", self.paths.mappings),
            ("projects", self.paths.choices),
        ):
            by_jev = models[kind] == "jev"
            timed: list[tuple[str, float]] = []
            for path in directory.glob("*.json"):
                record = json.loads(store.retried(path.read_bytes))  # a reading may be replacing it
                if not isinstance(record, dict):
                    continue  # not a saved reading, mapping or choice (the benchmark keeps other files beside them)
                # Opus answering in Jev's place counts for Jev too: with no key, or while TypeSafe is down, that is how
                # long Jev's calls take. Jev is known by its prefix: the pinned version and the older "jev-latest".
                fallback = record.get("fallback")
                asked = fallback.get("original_model") if isinstance(fallback, dict) else None
                in_jev_place = str(asked or "").startswith("jev")
                if kind != "requirements" and not (
                    str(record.get("model") or "").startswith("jev") == by_jev or in_jev_place
                ):
                    continue  # answered by the other model
                value = (record.get("info") or {}).get("seconds") if kind == "requirements" else record.get("seconds")
                if isinstance(value, int | float) and value > 0:
                    # A mapping records when it was made; a reading only has its file's date.
                    made = record.get("made") or dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                    timed.append((str(made), float(value)))
            seconds[kind] = sorted(value for _made, value in sorted(timed)[-RECENT_CALLS:])
        return {
            kind: values[len(values) // 2] if values else SECONDS_BEFORE_ANY_CALL[models[kind]][kind]
            for kind, values in seconds.items()
        }

    def sync(self) -> None:
        """Track every saved posting not tracked yet, dated by its file, at "read" if it is read."""
        tracked = self.tracker.rows()
        for path in sorted(self.paths.postings.glob("*.json")):
            if path.stem in tracked:
                continue
            posting = self.posting(path.stem)
            status = "read" if posting is not None and not self.calls_needed(posting) else "added"
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            self.tracker.add(path.stem, added=stamp, status=status)

    # Pages ------------------------------------------------------

    def page_path(self, posting_id: str) -> Path:
        return self.paths.pages / f"{posting_id}.json"

    def word_path(self, posting_id: str) -> Path:
        return self.paths.pages / f"{posting_id}.docx"

    def fingerprint(self, posting: Posting) -> str | None:
        """What a page for this posting is built from, or None when the posting is not read."""
        library, library_print = self.library_cache.get()
        modes = self.modes.get()
        choice = self.current_choice(posting, library) if modes.hybrid else None
        return self._fingerprint(
            *self.records(posting), library, library_print, edits=self.edits(posting["id"]), modes=modes, choice=choice
        )

    def _fingerprint(
        self,
        reading: Reading | None,
        mapping: CapabilityMapping | None,
        library: Library,
        library_print: str,
        *,
        edits: list[dict[str, Any]],
        modes: PageModes,
        choice: ProjectChoice | None,
    ) -> str | None:
        """The fingerprint of these inputs; `choice` is call 3's current answer in hybrid mode, else None."""
        if (
            reading is None
            or mapping is None
            or not store.is_current(mapping, library.vocabulary_hash, library.descriptions_hash)
        ):
            return None
        digest = hashlib.sha256(library_print.encode())
        # Accepted edits are part of what a page is built from: accepting or undoing one builds it again.
        changes = [[edit["remove"], edit["add"]] for edit in edits]
        # So are the mode and, in hybrid mode, call 3's current answer: switching or a new answer builds it again.
        record = [
            reading.get("requirements"),
            mapping.get("requirements"),
            changes,
            modes.pages,
            (choice or {}).get("projects"),
        ]
        digest.update(json.dumps(record, sort_keys=True).encode())
        return digest.hexdigest()[:16]

    def page_inputs(self, posting_id: str) -> str | None:
        """`fingerprint` for the posting with this id; None when the posting is missing or not read."""
        posting = self.posting(posting_id)
        return self.fingerprint(posting) if posting is not None else None

    def page_is_current(self, posting_id: str) -> bool:
        posting = self.posting(posting_id)
        row = self.tracker.row(posting_id)
        if posting is None or row is None:
            return False
        fingerprint = self.fingerprint(posting)
        return fingerprint is not None and row["page_fingerprint"] == fingerprint

    def _lock_for(self, posting_id: str) -> threading.RLock:
        with self._locks_lock:
            return self._job_locks.setdefault(posting_id, threading.RLock())

    def build(self, posting_id: str) -> PageResult | None:
        """Build and save the page and its Word file, and return the page; a refusal is recorded in the tracker instead.

        Returns None on a refusal: a page file on disk is then an older page's. Raises LookupError when the posting is
        missing or not read.
        """
        with self._lock_for(posting_id):
            posting = self.posting(posting_id)
            if posting is None:
                raise LookupError(f"no posting {posting_id}")
            reading, mapping = self.records(posting)
            # Each input is read once, so the fingerprint saved is that of the inputs the page is built from.
            library, library_print = self.library_cache.get()
            modes = self.modes.get()
            choice = self.current_choice(posting, library) if modes.hybrid else None
            edits = self.edits(posting_id)
            fingerprint = self._fingerprint(
                reading, mapping, library, library_print, edits=edits, modes=modes, choice=choice
            )
            if reading is None or mapping is None or fingerprint is None:
                raise LookupError(f"posting {posting_id} is not read")
            started = time.perf_counter()
            try:
                if modes.hybrid:
                    result = build_hybrid_page(posting, reading, library, mapping["requirements"], choice)
                else:
                    result = build_page(posting, reading, library, mapping["requirements"])
            except Refused as refusal:
                self.tracker.update(
                    posting_id,
                    page_fingerprint=fingerprint,
                    page_built=now(),
                    page_seconds=round(time.perf_counter() - started, 2),
                    page_refused=str(refusal),
                )
                return None
            record: dict[str, Any] = dict(result)
            if edits:
                record, edits = self._with_edits(posting, reading, mapping, library, result=result, edits=edits)
                self.tracker.update(posting_id, edits=json.dumps(edits))
            seconds = round(time.perf_counter() - started, 2)
            # Both are written beside their file and renamed over it, so a reader never sees half a file; a rename a
            # reader holds up is tried again (`store.retried`).
            store.write_json(self.page_path(posting_id), record)
            document = document_from_page(record)  # type: ignore[arg-type]  # a page result with its edit record
            store.retried(
                lambda: write_tailored_docx(self.paths.template, self.word_path(posting_id), document, overwrite=True)
            )
            self.tracker.update(
                posting_id, page_fingerprint=fingerprint, page_built=now(), page_seconds=seconds, page_refused=None
            )
            return cast(PageResult, record)

    # Accepted edits -------------------------------------------

    def edits(self, posting_id: str) -> list[dict[str, Any]]:
        row = self.tracker.row(posting_id)
        return list(json.loads(row["edits"])) if row and row.get("edits") else []

    def _with_edits(
        self,
        posting: Posting,
        reading: Reading,
        mapping: CapabilityMapping,
        library: Library,
        *,
        result: PageResult,
        edits: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The engine's page with the accepted edits applied in order, and the edits, each marked whether it applies.

        The edited page is built from its bullets with no selection (`build_page(chosen=...)`), so its match and
        requirement credit are its own; the engine's page is kept beside it as `engine` (its bullets and measures), and
        so is whose projects it started from.
        """
        chosen = list(result["selected"])
        marked: list[dict[str, Any]] = []
        for edit in edits:
            problem = edit_problem(library, chosen, edit["remove"], edit["add"])
            marked.append({**edit, "applies": problem is None, "problem": problem})
            if problem is None:
                chosen = applied(chosen, edit["remove"], edit["add"])
        if chosen == result["selected"]:
            return dict(result), marked
        edited = build_page(posting, reading, library, mapping["requirements"], chosen=chosen)
        started_from: dict[str, Any] = dict(result)
        hybrid: dict[str, Any] = {
            key: started_from[key] for key in ("projects_by", "choice", "engine_match") if key in started_from
        }
        return {**edited, **hybrid, "engine": measures(result)}, marked

    def decide(self, posting_id: str, index: int, *, accept: bool) -> None:
        """Accept or dismiss the judge's proposal `index`, recording the decision with both pages' measures.

        An accepted proposal is kept with the job's edits and the page built again. Raises LookupError when there is no
        such proposal, ValueError when it was decided already or no longer fits the page.
        """
        with self._lock_for(posting_id):
            self._decide(posting_id, index, accept=accept)

    def _decide(self, posting_id: str, index: int, *, accept: bool) -> None:
        row = self.tracker.row(posting_id)
        page = self.page(posting_id)
        judge = json.loads(row["judge"]) if row and row["judge"] else None
        proposals = (judge or {}).get("answer", {}).get("edits") or []
        if judge is None or page is None or not 0 <= index < len(proposals):
            raise LookupError(f"no proposal {index} for job {posting_id}")
        decisions = judge.setdefault("decisions", {})
        if str(index) in decisions:
            raise ValueError(f"this change was {decisions[str(index)]} already")
        proposal = proposals[index]
        edit = {"remove": proposal["remove"], "add": proposal["add"], "why": proposal.get("why", "")}
        edit |= {"judge_at": judge.get("at"), "proposal": index}
        on_page = list(page.get("selected") or [])
        library, _fingerprint = self.library_cache.get()
        problem = edit_problem(library, on_page, edit["remove"], edit["add"])
        if accept and problem:
            raise ValueError(f"this change cannot be applied to the page now: {problem}")
        after = self._measured(posting_id, applied(on_page, edit["remove"], edit["add"])) if problem is None else None
        if accept:
            self.tracker.update(posting_id, edits=json.dumps([*self.edits(posting_id), {**edit, "at": now()}]))
        decisions[str(index)] = "applied" if accept else "dismissed"
        self.tracker.update(posting_id, judge=json.dumps(judge))
        self.tracker.decide(posting_id, "accepted" if accept else "dismissed", edit, measures(page), after)
        if accept:
            self.build(posting_id)

    def _measured(self, posting_id: str, chosen: list[str]) -> dict[str, Any] | None:
        """The measures of this job's page with exactly these bullets; None when the posting cannot be built."""
        posting = self.posting(posting_id)
        reading, mapping = self.records(posting) if posting else (None, None)
        if posting is None or reading is None or mapping is None:
            return None
        library, _fingerprint = self.library_cache.get()
        return measures(build_page(posting, reading, library, mapping["requirements"], chosen=chosen))

    def undo(self, posting_id: str, index: int) -> None:
        """Take back accepted edit `index`; its proposal, if from the judge's last answer, is open again."""
        with self._lock_for(posting_id):
            self._undo(posting_id, index)

    def _undo(self, posting_id: str, index: int) -> None:
        edits = self.edits(posting_id)
        if not 0 <= index < len(edits):
            raise LookupError(f"no edit {index} for job {posting_id}")
        edit = edits.pop(index)
        self.tracker.update(posting_id, edits=json.dumps(edits) if edits else None)
        row = self.tracker.row(posting_id)
        judge = json.loads(row["judge"]) if row and row["judge"] else None
        if judge and judge.get("at") == edit.get("judge_at"):
            judge.get("decisions", {}).pop(str(edit.get("proposal")), None)
            self.tracker.update(posting_id, judge=json.dumps(judge))
        page = self.page(posting_id)
        self.tracker.decide(posting_id, "undone", edit, measures(page) if page else None, None)
        self.build(posting_id)

    def page(self, posting_id: str) -> PageResult | None:
        path = self.page_path(posting_id)
        if not path.exists():
            return None
        result: PageResult = json.loads(store.retried(lambda: path.read_text(encoding="utf-8")))
        return result

    # What the front end reads -----------------------------------

    def summary(self, posting: Posting, row: dict[str, Any]) -> dict[str, Any]:
        """A job as the list shows it: names, facts, status, whether it is read, and each requirement's state."""
        facts = facts_of(posting)
        title, company, company_known = shown_names(posting, row)
        place, place_full = places(facts.get("location"))
        years = facts.get("years_required")
        reading, mapping = self.records(posting)
        library, library_print = self.library_cache.get()
        needs = calls_needed_for(reading, mapping, library)
        read = not needs
        # Read once here for the fingerprint and whether call 3 is wanted: the list asks for every job's summary.
        modes = self.modes.get()
        choice = self.current_choice(posting, library) if modes.hybrid and read else None
        edits = json.loads(row["edits"]) if row.get("edits") else []
        current = read and row["page_fingerprint"] == self._fingerprint(
            reading, mapping, library, library_print, edits=edits, modes=modes, choice=choice
        )
        page = self.page(posting["id"]) if read and not row["page_refused"] else None
        credits = (page or {}).get("requirement_credit") or []
        capabilities = {entry["name"]: entry for entry in (page or {}).get("requirements") or []}
        required = [entry for entry in credits if entry["required"]]
        return {
            "id": posting["id"],
            "title": title,
            "company": company,
            "company_known": company_known,
            "url": posting.get("url"),
            "place": place,
            "place_full": place_full,
            "work_mode": WORK_MODES.get(str(facts.get("work_mode")), "Not stated"),
            "pay": pay_text(facts.get("salary")),
            "spons": facts.get("sponsorship") or "unstated",
            "level": str(facts.get("level") or "unstated").replace("unstated", "Not stated").capitalize(),
            "years": f"{years}+" if years is not None else "Not stated",
            "degree": str(facts.get("degree_required") or "Not stated"),
            "blockers": blockers_of(facts, reading),
            "added": row["added"][:10],
            "status": row["status"],
            "read": read,
            "needs": needs,
            "page_current": current,
            "refused": row["page_refused"] if read else None,
            "match": page.get("coverage") if page else None,
            "projects_by": (page or {}).get("projects_by"),
            # Hybrid mode wants call 3 for a page whose engine match is low while no current answer is saved.
            "choice_needed": modes.hybrid and page is not None and is_low(page) and choice is None,
            "tally": {state: sum(state_of(entry["credit"]) == state for entry in required) for state in STATES},
            "reqs": [
                {"state": state_of(entry["credit"]), "caps": capabilities_of(capabilities.get(entry["name"]) or {})}
                for entry in credits
            ],
        }

    def summaries(self) -> tuple[list[dict[str, Any]], list[str]]:
        """(each tracked job's summary, a line for each tracked job that could not be shown and why)."""
        found, problems = [], []
        for posting_id, row in self.tracker.rows().items():
            if row["removed"]:
                continue
            try:
                posting = self.posting(posting_id)
                if posting is None:
                    problems.append(f"{posting_id}: its posting file is missing from {self.paths.postings}")
                    continue
                found.append(self.summary(posting, row))
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                problems.append(f"{posting_id}: {type(error).__name__}: {error}")
        return found, problems

    def detail(self, posting_id: str) -> dict[str, Any] | None:
        """A job as its own page shows it; None when it is not on the list.

        The summary, notes, pay tiers, the judge's answer and proposals, the accepted edits, each requirement with the
        bullets that meet it, and the printed page.
        """
        posting = self.posting(posting_id)
        row = self.tracker.row(posting_id)
        if posting is None or row is None or row["removed"]:
            return None
        summary = self.summary(posting, row)
        facts = facts_of(posting)
        summary["notes"] = row["notes"]
        summary["pay_tiers"] = [
            {"label": tier.get("label"), "pay": pay_text(dict(tier))}
            for tier in (facts.get("salary") or {}).get("tiers") or []
        ]
        judge = json.loads(row["judge"]) if row["judge"] else None
        if judge is not None:
            judge["page_changed"] = judge.get("page_fingerprint") != row["page_fingerprint"]
        summary["judge_seconds"] = self.judge_seconds()
        page = self.page(posting_id) if summary["read"] and not summary["refused"] else None
        library, _fingerprint = self.library_cache.get()
        on_page = list((page or {}).get("selected") or [])
        if judge is not None:
            # Each proposed change in words, and where it stands: applied, dismissed, open, or unfit for the page now.
            decided = judge.get("decisions") or {}
            proposals = []
            for number, proposal in enumerate((judge.get("answer") or {}).get("edits") or []):
                problem = edit_problem(library, on_page, proposal["remove"], proposal["add"]) if page else "no page"
                state = decided.get(str(number)) or ("open" if problem is None else "unfit")
                proposals.append({**described(library, proposal), "state": state, "problem": problem})
            judge["proposals"] = proposals
        summary["judge"] = judge
        summary["edits"] = [
            {**described(library, edit), "applies": edit.get("applies", True), "problem": edit.get("problem")}
            for edit in self.edits(posting_id)
        ]
        summary["engine"] = (page or {}).get("engine")
        summary["choice"] = (page or {}).get("choice")
        # Which model chose the projects the page uses, and why when another answered in place of the one asked.
        model_chose = page is not None and page.get("projects_by") == "model"
        summary["choice_by"] = answered(store.load_choice(posting["text"], self.paths.choices)) if model_chose else None
        summary["engine_match"] = engine_match(page) if page else None
        summary["requirements"] = self._requirements(page) if page else []
        summary["unmapped"] = (page or {}).get("unmapped") or []
        summary["printed"] = printed_paragraphs(self.word_path(posting_id)) if page else []
        return summary

    def _requirements(self, page: PageResult) -> list[dict[str, Any]]:
        """Each requirement's credit on the page, its capabilities and conditions, and the bullets that earn it."""
        library, _fingerprint = self.library_cache.get()
        capabilities = {entry["name"]: entry for entry in page.get("requirements") or []}
        found = []
        for entry in page.get("requirement_credit") or []:
            mapped = capabilities.get(entry["name"]) or {}
            found.append(
                {
                    "name": entry["name"],
                    "required": entry["required"],
                    "state": state_of(entry["credit"]),
                    "caps": capabilities_of(mapped),
                    "conditions": mapped.get("conditions") or [],
                    "by": bullet_entries(library, entry["bullets"]),
                }
            )
        return found


def calls_needed_for(reading: Reading | None, mapping: CapabilityMapping | None, library: Library) -> list[str]:
    """The model calls reading a posting with this saved reading and mapping would make (`Jobs.calls_needed`)."""
    if reading is None:
        return ["requirements", "mapping"]
    if store.mapping_needs_call(mapping, library.vocabulary_hash, library.descriptions_hash):
        return ["mapping"]
    return []


def measures(page: PageResult | dict[str, Any]) -> dict[str, Any]:
    """A page's measures, to compare two pages of one posting: its bullets, match, score and the score's terms."""
    credits = page.get("requirement_credit") or []
    required = [entry for entry in credits if entry["required"]]
    return {
        "selected": list(page.get("selected") or []),
        "coverage": page.get("coverage"),
        "score": page.get("score"),
        "terms": {name: page.get(name) for name in ("emphasis", "standing", "incoherence", "cost")},
        "required_met": sum(entry["credit"] >= 1.0 for entry in required),
        "required": len(required),
    }


def engine_match(page: PageResult) -> float | None:
    """The engine's own match for a page: kept beside a page built from call 3's projects or edited by hand."""
    found: dict[str, Any] = dict(page)
    if "engine_match" in found:
        return float(found["engine_match"])
    if "engine" in found:
        return float(found["engine"]["coverage"])
    coverage = found.get("coverage")
    return float(coverage) if coverage is not None else None


def is_low(page: PageResult) -> bool:
    """Whether the engine's own match for this page is under hybrid mode's threshold."""
    match = engine_match(page)
    return match is not None and match < DEFAULT_WEIGHTS.model_projects_below


def _low_match_choice(
    early: Future[bool] | None,
    choose: Callable[[Chooser | None], bool],
    choices: Executor,
    on_stage: Callable[[str], None],
) -> bool:
    """Whether a model call made the choice a low-match page is built from now: Jev's, begun early, else Opus's.

    Opus is asked when it is the classifier (`early` is None), or when Jev could not answer. `on_stage` hears "call3"
    only while a call 3 is to be waited for: Jev's still running, or Opus's.
    """
    waiting = early is None or not early.done()
    if waiting:
        on_stage("call3")
    if early is None:
        return choices.submit(choose, None).result()
    try:
        return early.result()
    except JevUnavailableError as error:
        if not waiting:
            on_stage("call3")
        return choices.submit(choose, _opus_in_place_of_jev(error)).result()


def _opus_in_place_of_jev(error: JevUnavailableError) -> Chooser:
    """Call 3 on Opus 5 for a page Jev could not choose for; its record says Jev was asked and why."""

    def choose(title: str, text: str, library: Library) -> ProjectChoice:
        record = project_choice.choose_projects(title, text, library)
        # Jev was asked beside call 1, so the posting did not wait on it: none of Jev's time is added to Opus's.
        jev.fell_back(record, error, seconds=0.0)
        return record

    return choose


def _log_early_failure(done: Future[bool]) -> None:
    error = None if done.cancelled() else done.exception()
    if isinstance(error, JevUnavailableError):
        # Expected while TypeSafe is down or no key is stored, and nothing waited on it: said without a traceback.
        LOG.warning("Jev's early call 3 was not answered: %s", error)
    elif isinstance(error, PrivacyError):
        # Expected on a copy with no fingerprints file, which refuses every call 3: said the same way.
        LOG.warning("Jev's early call 3 was not sent: %s", error)
    elif error is not None:
        LOG.error("an early call 3 failed", exc_info=error)
