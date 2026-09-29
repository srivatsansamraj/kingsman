"""Work that runs after a request has answered: readings (model calls) and page rebuilds.

Each step is pushed to every open page as a server-sent event. Call 1 runs for three postings at a time (the plan's
limit). Who answers call 2 is decided for each reading as it starts, by the page mode (`Jobs.read`): with Jev
(Speed), a posting's call 2 requests go to Jev the moment its call 1 is done. With Jev as the classifier (the default),
call 3 (hybrid mode) starts beside call 1 on a pool of its own; at most six Jev requests are in flight, calls 2
and 3 together. Where Jev cannot answer, Opus is asked: call 2 in Opus's batches, as below, and call 3 only when the
page's match is low. With Opus answering call 2 (Accuracy, the default, or Opus as the classifier), and with no Jev key
stored, when every posting falls back at once, call 2 goes in batches: postings whose call 1 is done wait until five
are waiting, or until no other posting is queued or at call 1, and then go as one call, one batch at a time
(five a call measured no worse than one, at about a fifth of the call time per posting). With Opus as the classifier
call 3 runs on the call 3 pool, three at a time, after a page whose match is low is built, so at most seven model calls
run at once for readings. The judge has its own three. A reading builds its own page on its own thread; the rebuilds of
pages whose library, reading, mapping, mode or choice has changed (no model call, about 1.4 s each) run one at a time.
Nothing runs while idle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import Any

from tailor_engine.reading import capability_mapping, jev_mapping
from tailor_engine.records import CapabilityMapping, Requirement

from .jobs import Jobs, Mapper

LOG = logging.getLogger(__name__)

READINGS_AT_ONCE = 3
MAPPING_BATCH = 5
CHOICES_AT_ONCE = 3
JUDGES_AT_ONCE = 3
# Jev requests in flight at once, calls 2 and 3 together: six at once ran 62 requests without an error.
JEV_AT_ONCE = 6
# A posting's call 2 on Jev, for the reading estimate: 3 choice requests at the median and the word request.
JEV_REQUESTS_A_POSTING = 4
# A reading's thread waits while its posting is in a batch. Threads for the call 1s, a batch being answered and a full
# batch waiting; past that a posting waits for a thread, which costs no time overall, since call 2 is the slower stage.
READING_THREADS = READINGS_AT_ONCE + 2 * MAPPING_BATCH


class _Request:
    """One posting's call 2, waiting for its batch."""

    def __init__(self, title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> None:
        self.title = title
        self.requirements = requirements
        self.vocabulary = vocabulary
        self.done = threading.Event()
        self.result: CapabilityMapping | None = None
        self.error: Exception | None = None


class MappingBatcher:
    """Call 2 for several postings in one call.

    `map` waits until `size` requests are waiting, or until `upstream()` (postings queued or at call 1) is 0, so a
    posting read on its own goes at once; then the waiting requests go together, one batch at a time, each in a thread
    of its own. `poke` looks again, and is called whenever a posting changes stage or finishes.
    """

    def __init__(self, upstream: Callable[[], int], size: int = MAPPING_BATCH) -> None:
        self._upstream = upstream
        self._size = size
        self._lock = threading.Lock()
        self._waiting: list[_Request] = []
        self._running = False
        self._closed = False

    def map(self, title: str, requirements: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
        request = _Request(title, requirements, vocabulary)
        with self._lock:
            if self._closed:
                raise RuntimeError("the dashboard is stopping")
            self._waiting.append(request)
        self.poke()
        request.done.wait()
        if request.error is not None:
            raise request.error
        assert request.result is not None
        return request.result

    def waiting(self) -> int:
        with self._lock:
            return len(self._waiting)

    def poke(self) -> None:
        # `upstream()` takes the Background's lock inside this one; nothing takes the two the other way round.
        with self._lock:
            if self._running or not self._waiting:
                return
            if len(self._waiting) < self._size and self._upstream() > 0:
                return
            # One call answers under one vocabulary; a request made under a changed library waits for the next.
            vocabulary = self._waiting[0].vocabulary
            batch = [request for request in self._waiting if request.vocabulary == vocabulary][: self._size]
            self._waiting = [request for request in self._waiting if all(request is not taken for taken in batch)]
            self._running = True
        threading.Thread(target=self._answer, args=(batch,), name="mapping", daemon=True).start()

    def close(self) -> None:
        """Waiting requests fail; a batch being answered finishes."""
        with self._lock:
            self._closed = True
            waiting, self._waiting = self._waiting, []
        for request in waiting:
            request.error = RuntimeError("the dashboard is stopping")
            request.done.set()

    def _answer(self, batch: list[_Request]) -> None:
        # The mapping functions are looked up on the module at call time, so a test or the dev server can stand in.
        try:
            if len(batch) == 1:
                only = batch[0]
                records = [capability_mapping.map_requirements(only.title, only.requirements, only.vocabulary)]
            else:
                records = capability_mapping.map_many(
                    [(request.title, request.requirements) for request in batch], batch[0].vocabulary
                )
            for request, record in zip(batch, records, strict=True):
                request.result = record
        except Exception as error:  # every posting of the batch reports it, as a single call's failure is reported
            for request in batch:
                request.error = error
        finally:
            for request in batch:
                request.done.set()
            with self._lock:
                self._running = False
            self.poke()


class _PokeFirst:
    """A Jev call 2 request's place among the requests in flight; taking it first has the batcher look again.

    It is taken as the request goes out, once the key has been read: that posting is asking Jev and joins no batch now,
    so a posting Jev could not map does not wait for its answer. A look as the posting reached call 2 would split the
    batch of a copy with no key, where every posting joins.
    """

    def __init__(self, slot: AbstractContextManager[Any], poke: Callable[[], None]) -> None:
        self._slot = slot
        self._poke = poke

    def __enter__(self) -> None:
        self._poke()
        self._slot.__enter__()

    def __exit__(self, *details: Any) -> None:
        self._slot.__exit__(*details)


class Events:
    """Every open page's event queue; `publish` may be called from any thread."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def deliver() -> None:
            for queue in list(self._queues):
                queue.put_nowait(event)

        loop.call_soon_threadsafe(deliver)


class Background:
    def __init__(self, jobs: Jobs, events: Events):
        self.jobs = jobs
        self.events = events
        self._readings = ThreadPoolExecutor(max_workers=READING_THREADS, thread_name_prefix="reading")
        self._call1 = threading.BoundedSemaphore(READINGS_AT_ONCE)
        # Every call 3 on Opus runs here, CHOICES_AT_ONCE at a time; a reading's thread waits for its answer. No thread
        # of this pool waits on a reading's, so the wait cannot deadlock.
        self._choices = ThreadPoolExecutor(max_workers=CHOICES_AT_ONCE, thread_name_prefix="call3")
        # Jev's early call 3 runs on threads of its own, as many as Jev requests in flight: an Opus call 3 in Jev's
        # place does not queue behind other postings' Jev calls while TypeSafe hangs.
        self._jev_choices = ThreadPoolExecutor(max_workers=JEV_AT_ONCE, thread_name_prefix="jev-call3")
        self._batcher = MappingBatcher(self._upstream)
        self._jev = threading.BoundedSemaphore(JEV_AT_ONCE)
        self._builds = ThreadPoolExecutor(max_workers=1, thread_name_prefix="build")
        self._judges = ThreadPoolExecutor(max_workers=JUDGES_AT_ONCE, thread_name_prefix="judge")
        self._lock = threading.Lock()
        # posting id -> "queued", "call1", "call2", "call3" or "building", while reading
        self.stages: dict[str, str] = {}
        self.judging: dict[str, str] = {}  # posting id -> "brief" or "detailed", while the judge is asked
        self._rebuilding: set[str] = set()
        self._failed: dict[str, str | None] = {}  # posting id -> the page inputs a rebuild of it failed on

    def shutdown(self) -> None:
        """Stop: queued work is dropped, postings waiting for a batch fail, a page being built is finished.

        The page being built takes about a second, so no half-written file is left.

        A model call already running is not cut short: Python waits for a pool's threads when it exits, so a stop during
        a reading or a judge's answer returns when that call does (a call's limit is ten minutes).
        """
        self._batcher.close()
        for pool in (self._readings, self._judges, self._choices, self._jev_choices):
            pool.shutdown(wait=False, cancel_futures=True)
        self._builds.shutdown(wait=True, cancel_futures=True)

    # Readings ---------------------------------------------------

    def busy(self, posting_id: str) -> bool:
        """Whether this job is being read or judged now."""
        with self._lock:
            return posting_id in self.stages or posting_id in self.judging

    def stages_now(self) -> dict[str, str]:
        """Each job being read and its stage, as a copy."""
        with self._lock:
            return dict(self.stages)

    def judging_level(self, posting_id: str) -> str | None:
        """The judge's length being asked for this job; None when it is not being asked."""
        with self._lock:
            return self.judging.get(posting_id)

    def read(self, posting_id: str, *, refresh: bool = False) -> bool:
        """Queue a reading, every call made again with `refresh`; False when this posting is being read already."""
        with self._lock:
            if posting_id in self.stages:
                return False
            self.stages[posting_id] = "queued"
        self.events.publish({"type": "stage", "id": posting_id, "stage": "queued"})
        _logged(self._readings.submit(self._read, posting_id, refresh))
        return True

    def _upstream(self) -> int:
        """Postings queued or at call 1: those that may yet join a call 2 batch."""
        with self._lock:
            return sum(stage in ("queued", "call1") for stage in self.stages.values())

    def _stage(self, posting_id: str, stage: str) -> None:
        with self._lock:
            self.stages[posting_id] = stage
        self.events.publish({"type": "stage", "id": posting_id, "stage": stage})
        # A posting reaching call 2 pokes when it joins the waiting requests; a poke now, before it has joined, would
        # send a batch without it. On Jev, its request going out pokes (`_PokeFirst`).
        if stage != "call2":
            self._batcher.poke()

    def _read(self, posting_id: str, refresh: bool = False) -> None:
        try:
            calls = self.jobs.read(
                posting_id,
                lambda stage: self._stage(posting_id, stage),
                refresh=refresh,
                call1_slot=self._call1,
                mappers=self._mappers(),
                choices=self._choices,
                jev_choices=self._jev_choices,
                jev_slot=self._jev,
            )
        except Exception as error:  # any failure is reported on the page, and the job stays unread
            LOG.exception("reading %s failed", posting_id)
            with self._lock:
                self.stages.pop(posting_id, None)
            self._batcher.poke()
            self.events.publish({"type": "read-failed", "id": posting_id, "message": _message(error)})
            return
        # The stage is cleared before the event is sent: a page that hears "read" and asks for the list must not be
        # told the posting is still being read.
        with self._lock:
            self.stages.pop(posting_id, None)
        self._batcher.poke()
        self.events.publish({"type": "read", "id": posting_id, "calls": calls})

    def _mappers(self) -> dict[str, Mapper]:
        """Call 2 by each model that may answer it; `Jobs.read` chooses one for each reading.

        Jev at once, several postings at a time; Opus in batches.
        """

        def on_jev(title: str, found: list[Requirement], vocabulary: dict[str, str]) -> CapabilityMapping:
            # No batch: each posting's requests go as soon as its reading is ready. A posting Jev cannot answer joins
            # Opus's batches, which send a posting read on its own at once. The
            # capability descriptions are those of the dashboard's library, as the vocabulary is.
            return jev_mapping.map_requirements(
                title,
                found,
                vocabulary,
                descriptions=self.jobs.library_cache.get()[0].capability_descriptions,
                slot=_PokeFirst(self._jev, self._batcher.poke),
                fallback=self._batcher.map,
            )

        return {"jev": on_jev, "opus": self._batcher.map}

    # Ask the judge ----------------------------------------------

    def judge(self, posting_id: str, level: str) -> bool:
        """Ask the judge about a job's page; False when it is being asked already."""
        with self._lock:
            if posting_id in self.judging:
                return False
            self.judging[posting_id] = level
        self.events.publish({"type": "judging", "id": posting_id, "level": level})
        _logged(self._judges.submit(self._judge, posting_id, level))
        return True

    def _judge(self, posting_id: str, level: str) -> None:
        try:
            self.jobs.judge(posting_id, level)
        except Exception as error:  # reported on the page; the previous answer stays
            LOG.exception("asking the judge about %s failed", posting_id)
            with self._lock:
                self.judging.pop(posting_id, None)
            self.events.publish({"type": "judge-failed", "id": posting_id, "message": _message(error)})
            return
        with self._lock:
            self.judging.pop(posting_id, None)
        self.events.publish({"type": "judged", "id": posting_id})

    # Rebuilds ---------------------------------------------------

    def rebuild_stale(self, posting_ids: list[str]) -> None:
        """Queue a rebuild for each read job whose page is missing or out of date.

        Each pushes "built" when it built a page.
        """
        for posting_id in posting_ids:
            with self._lock:
                busy = posting_id in self._rebuilding or posting_id in self.stages
                failed_on = self._failed.get(posting_id, "")
            # A rebuild that failed is not tried again on the same inputs: every list request would repeat it, and the
            # page asks for the list after each failure.
            if busy or (failed_on != "" and failed_on == self._inputs(posting_id)):
                continue
            with self._lock:
                if posting_id in self._rebuilding:
                    continue
                self._rebuilding.add(posting_id)
            _logged(self._builds.submit(self._rebuild, posting_id))

    def _inputs(self, posting_id: str) -> str | None:
        try:
            return self.jobs.page_inputs(posting_id)
        except Exception:  # noqa: BLE001  inputs that cannot be read count as unchanged, so nothing is retried on them
            LOG.exception("reading the page inputs of %s failed", posting_id)
            return None

    def _rebuild(self, posting_id: str) -> None:
        # As with a reading, the rebuild is over before its event is sent: a page that hears it and asks for the list
        # must not find it still running.
        event: dict[str, Any] | None = None
        try:
            if not self.jobs.page_is_current(posting_id):
                self.jobs.build(posting_id)
                event = {"type": "built", "id": posting_id}
            with self._lock:
                self._failed.pop(posting_id, None)
        except Exception as error:  # reported on the page; the old page stays
            LOG.exception("building the page of %s failed", posting_id)
            inputs = self._inputs(posting_id)
            with self._lock:
                self._failed[posting_id] = inputs
            event = {"type": "build-failed", "id": posting_id, "message": _message(error)}
        finally:
            with self._lock:
                self._rebuilding.discard(posting_id)
        if event is not None:
            self.events.publish(event)


def _message(error: Exception) -> str:
    return str(error) or type(error).__name__


def _logged(future: Future[None]) -> None:
    """Log an exception that escapes a submitted job's own handling, which the pool would otherwise keep silent."""

    def check(done: Future[None]) -> None:
        if not done.cancelled() and done.exception() is not None:
            LOG.error("background work failed", exc_info=done.exception())

    future.add_done_callback(check)
