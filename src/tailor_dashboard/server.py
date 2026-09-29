"""The dashboard's web server: a JSON API and the page that draws from it, on this machine only.

Two checks guard it. The Host header must name this machine, so a website cannot reach the server through a domain
name of its own that it points at 127.0.0.1 (DNS rebinding). Every request that changes something must carry the
`X-Tailor` header, which another website's page cannot add without the browser first asking the server's
permission, which it never gives.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from tailor_engine import settings
from tailor_engine.records import Posting

from . import views
from .background import (
    CHOICES_AT_ONCE,
    JEV_AT_ONCE,
    JEV_REQUESTS_A_POSTING,
    MAPPING_BATCH,
    READINGS_AT_ONCE,
    Background,
    Events,
)
from .display import company_name, posting_title, printed_paragraphs
from .jobs import Jobs, Paths
from .tracker import STATUSES, now

STATIC = Path(__file__).parent / "static"
CHANGE_HEADER = "x-tailor"
SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))
LOCAL_HOST = re.compile(r"(127\.0\.0\.1|localhost)(:\d+)?")
POSTING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
# An idle event stream sends a comment this often, so a closed page's stream is noticed and dropped.
IDLE_SECONDS = 60
# What the page may load: its own scripts only (it has no inline script); its styles, with the inline style
# attributes its code writes; its own fonts; requests to this server only. No page may frame it.
CONTENT_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# The longest title and company a job may be given. Set by hand.
TITLE_LONGEST = 200
COMPANY_LONGEST = 100


class AddJob(BaseModel):
    source: str


class JobChange(BaseModel):
    status: str | None = None
    notes: str | None = None
    title: str | None = None  # "" returns to the posting's own
    company: str | None = None


class ReadMany(BaseModel):
    ids: list[str]


class AskJudge(BaseModel):
    level: str


class Decision(BaseModel):
    accept: bool


class ModesChange(BaseModel):
    # A field it does not know (the priority an older open page sends) is refused, not passed over as a success.
    model_config = ConfigDict(extra="forbid")

    pages: str | None = None
    call2: str | None = None


class Routes:
    """Each API route as a method; `create_app` registers them."""

    def __init__(self, jobs: Jobs, background: Background, events: Events):
        self.jobs = jobs
        self.background = background
        self.events = events

    def _tracked(self, posting_id: str) -> tuple[Posting, dict[str, Any]]:
        """The job's posting and tracker row; 404 when the job is not on the list."""
        # A posting id names a file; one that could step out of the postings folder (a backslash on Windows) is refused
        # before any path is made from it. A second guard: only tracked ids have a row, and none can have that form.
        if not POSTING_ID.fullmatch(posting_id):
            raise HTTPException(404, f"no job {posting_id}")
        posting = self.jobs.posting(posting_id)
        row = self.jobs.tracker.row(posting_id)
        if posting is None or row is None or row["removed"]:
            raise HTTPException(404, f"no job {posting_id}")
        return posting, row

    def _summary(self, posting_id: str) -> dict[str, Any]:
        return self.jobs.summary(*self._tracked(posting_id))

    # Jobs -------------------------------------------------------

    def list_jobs(self) -> dict[str, Any]:
        summaries, problems = self.jobs.summaries()
        self.background.rebuild_stale([job["id"] for job in summaries if job["read"] and not job["page_current"]])
        modes = self.jobs.modes.get()
        return {
            "jobs": summaries,
            "problems": problems,
            "stages": self.background.stages_now(),
            "seconds": self.jobs.call_seconds(),
            # How readings are run, for the page's estimate of what reading several costs, by the model answering each
            # call now. "batch" is how many postings' call 2 go together: Opus's batch, or on Jev the postings whose
            # requests fit in flight at once. "call3" is how many call 3s run at once: Jev's early calls on their own
            # pool, as many as Jev requests in flight, or Opus's pool. "classifier" is who answers call 3.
            "schedule": {
                "call1": READINGS_AT_ONCE,
                "batch": (
                    max(1, JEV_AT_ONCE // JEV_REQUESTS_A_POSTING)
                    if self.jobs.call2_model(modes) == "jev"
                    else MAPPING_BATCH
                ),
                "call3": JEV_AT_ONCE if self.jobs.classifier == "jev" else CHOICES_AT_ONCE,
                "classifier": self.jobs.classifier,
            },
            "modes": dataclasses.asdict(modes),
        }

    def job_detail(self, posting_id: str) -> dict[str, Any]:
        self._tracked(posting_id)
        detail = self.jobs.detail(posting_id)
        if detail is None:
            raise HTTPException(404, f"no job {posting_id}")
        detail["judging"] = self.background.judging_level(posting_id)
        return detail

    def add_job(self, body: AddJob) -> dict[str, Any]:
        # Imported here, as in `Jobs.add`: fetching needs httpx.
        from tailor_engine.reading.fetch import FetchError

        try:
            posting_id, created = self.jobs.add(body.source)
        except (FetchError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        return {"job": self._summary(posting_id), "created": created}

    def change_job(self, posting_id: str, body: JobChange) -> dict[str, Any]:
        posting, _row = self._tracked(posting_id)
        if body.status is not None and body.status not in STATUSES:
            raise HTTPException(400, f"unknown status {body.status!r}")
        changes: dict[str, Any] = {
            name: value for name, value in (("status", body.status), ("notes", body.notes)) if value is not None
        }
        own = {"title": posting_title(posting), "company": company_name(posting.get("company") or "")}
        for name, value, longest in (("title", body.title, TITLE_LONGEST), ("company", body.company, COMPANY_LONGEST)):
            if value is None:
                continue
            value = " ".join(value.split())  # one line: it is printed in the list, the file name and the judge's prompt
            if len(value) > longest:
                raise HTTPException(400, f"a {name} of at most {longest} characters")
            # A name equal to the posting's own is not kept as a given name, so the posting's stays in charge of it.
            changes[name] = value if value and value != own[name] else None
        self.jobs.tracker.update(posting_id, **changes)
        return {"job": self._summary(posting_id)}

    def remove_job(self, posting_id: str) -> dict[str, Any]:
        """Take the job off the list; its saved files stay, and adding the posting again brings it back."""
        self._tracked(posting_id)
        if self.background.busy(posting_id):
            raise HTTPException(400, "it is being read or judged; remove it when that has finished")
        self.jobs.tracker.update(posting_id, removed=now())
        return {"removed": posting_id}

    # Reading and pages ------------------------------------------

    def read_job(self, posting_id: str) -> dict[str, Any]:
        self._tracked(posting_id)
        return {"started": self.background.read(posting_id)}

    def read_many(self, body: ReadMany) -> dict[str, Any]:
        for posting_id in body.ids:
            self._tracked(posting_id)
        return {"started": [posting_id for posting_id in body.ids if self.background.read(posting_id)]}

    def read_again(self, posting_id: str) -> dict[str, Any]:
        """Every model call made again for this job, then its page: a new reading, mapping and, as needed, choice."""
        self._tracked(posting_id)
        return {"started": self.background.read(posting_id, refresh=True)}

    def change_modes(self, body: ModesChange) -> dict[str, Any]:
        """Switch legacy or hybrid pages, whose changed pages are built again with no call, or who answers call 2."""
        try:
            modes = self.jobs.modes.set(pages=body.pages, call2=body.call2)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        self.events.publish({"type": "modes", "modes": dataclasses.asdict(modes)})
        return {"modes": dataclasses.asdict(modes)}

    def rebuild_job(self, posting_id: str) -> dict[str, Any]:
        self._tracked(posting_id)
        try:
            self.jobs.build(posting_id)
        except (LookupError, ValueError, OSError) as error:  # the Word writer's template check is a ValueError
            raise HTTPException(400, str(error) or type(error).__name__) from error
        return self.job_detail(posting_id)

    def ask_judge(self, posting_id: str, body: AskJudge) -> dict[str, Any]:
        summary = self._summary(posting_id)
        if body.level not in ("brief", "detailed"):
            raise HTTPException(400, f"unknown length {body.level!r}: brief or detailed")
        if not summary["read"] or summary["refused"] or summary["match"] is None:
            raise HTTPException(400, "this job has no page for the judge to read yet")
        return {"started": self.background.judge(posting_id, body.level)}

    def decide_proposal(self, posting_id: str, index: int, body: Decision) -> dict[str, Any]:
        """Yes or no to one of the judge's proposed page edits; a yes builds the page again with it."""
        self._not_busy(posting_id)
        try:
            self.jobs.decide(posting_id, index, accept=body.accept)
        except (LookupError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        return self.job_detail(posting_id)

    def undo_edit(self, posting_id: str, index: int) -> dict[str, Any]:
        self._not_busy(posting_id)
        try:
            self.jobs.undo(posting_id, index)
        except LookupError as error:
            raise HTTPException(400, str(error)) from error
        return self.job_detail(posting_id)

    def _not_busy(self, posting_id: str) -> None:
        # The page changes under a reading or a judge's question; an edit waits for them.
        self._tracked(posting_id)
        if self.background.busy(posting_id):
            raise HTTPException(400, "it is being read or judged; try again when that has finished")

    def word_file(self, posting_id: str) -> FileResponse:
        summary = self._summary(posting_id)
        path = self.jobs.word_path(posting_id)
        if not path.exists() or not summary["read"] or summary["refused"]:
            raise HTTPException(404, "this job has no page yet")
        return FileResponse(path, media_type=WORD_TYPE, filename=word_file_name(path, summary["company"]))

    # Profile, Stats, events, the page ---------------------------

    def profile_page(self) -> dict[str, Any]:
        return views.profile(self.jobs)

    def stats_page(self) -> dict[str, Any]:
        return views.reading_stats(self.jobs)

    async def stream(self, request: Request) -> StreamingResponse:
        queue = self.events.subscribe()

        async def body() -> AsyncIterator[str]:
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=IDLE_SECONDS)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": idle\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                self.events.unsubscribe(queue)

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    def index(self) -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(paths: Paths | None = None, *, hosts: set[str] | None = None, classifier: str | None = None) -> FastAPI:
    """The app; `hosts` are the Host headers it answers (default 127.0.0.1 and localhost on any port).

    `classifier` answers call 3, "jev" or "opus", and call 2 too when "opus"; by default `settings.CLASSIFIER`
    (TAILOR_CLASSIFIER, else Jev). Otherwise call 2 is answered as the page mode says (`page_modes`).
    """
    jobs = Jobs(paths or Paths.default(), classifier=classifier or settings.CLASSIFIER)
    jobs.sync()
    events = Events()
    background = Background(jobs, events)
    routes = Routes(jobs, background, events)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        events.bind(asyncio.get_running_loop())
        yield
        background.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs
    app.state.background = background

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "")
        if not (host in hosts if hosts is not None else LOCAL_HOST.fullmatch(host)):
            return JSONResponse({"detail": "this server answers only on this machine"}, status_code=403)
        if request.method not in SAFE_METHODS and request.headers.get(CHANGE_HEADER) != "1":
            return JSONResponse({"detail": "a change must come from the dashboard's own page"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_POLICY
        # Checked with the server on each load (a 304 when unchanged), so a page open after an update never runs the
        # script or styles the browser kept from before it.
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    for method, path, endpoint in (
        ("GET", "/api/jobs", routes.list_jobs),
        ("POST", "/api/jobs", routes.add_job),
        ("GET", "/api/jobs/{posting_id}", routes.job_detail),
        ("PATCH", "/api/jobs/{posting_id}", routes.change_job),
        ("POST", "/api/jobs/{posting_id}/remove", routes.remove_job),
        ("POST", "/api/jobs/{posting_id}/read", routes.read_job),
        ("POST", "/api/read", routes.read_many),
        ("POST", "/api/jobs/{posting_id}/read-again", routes.read_again),
        ("POST", "/api/jobs/{posting_id}/rebuild", routes.rebuild_job),
        ("POST", "/api/modes", routes.change_modes),
        ("POST", "/api/jobs/{posting_id}/judge", routes.ask_judge),
        ("POST", "/api/jobs/{posting_id}/proposals/{index}", routes.decide_proposal),
        ("POST", "/api/jobs/{posting_id}/edits/{index}/undo", routes.undo_edit),
        ("GET", "/api/jobs/{posting_id}/word", routes.word_file),
        ("GET", "/api/profile", routes.profile_page),
        ("GET", "/api/stats", routes.stats_page),
        ("GET", "/api/events", routes.stream),
        ("GET", "/", routes.index),
    ):
        app.add_api_route(path, endpoint, methods=[method])
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def word_file_name(path: Path, company: str) -> str:
    """The download's name: the name the resume prints (its first line), "Resume", and the company."""
    paragraphs = printed_paragraphs(path)
    name = "".join(text for text, _bold in paragraphs[0]["runs"]).strip() if paragraphs else ""
    stem = f"{name} Resume - {company}" if name else f"Resume - {company}"
    return re.sub(r"[^\w .,&()'-]+", "", stem).strip() + ".docx"
