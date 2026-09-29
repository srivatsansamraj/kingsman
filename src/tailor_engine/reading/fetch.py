"""Fetching a posting from its URL: the posting's text, and the facts its job board publishes structured.

  Greenhouse   boards-api.greenhouse.io      text, pay ranges, location type
  Lever        api.lever.co                  text, salary range, work mode
  Ashby        api.ashbyhq.com               text, pay tiers, workplace type, every location
  Amazon       amazon.jobs search.json       text
  Workday      <tenant>.myworkdayjobs.com    text
  anything else                              the page's HTML reduced to text

The facts are read from the board's answer in `board_facts.py`. Each board is asked once (Workday's page as well when
its API gives almost no text), and its answer gives both the text and the facts. The text is laid out as
"title / Location: ... / body" and normalised the same way for every source, because a saved reading is
keyed on the exact text: the same posting must always come out byte for byte the same.

LinkedIn is not fetched; its terms forbid automated access. Paste the posting instead.
Only public http(s) hosts are fetched, so a URL cannot point the fetcher at this machine or its network.
Every request is checked as it is sent, redirects included. The host is resolved for the check and again to
connect, so a host that changes its answer between the two is not caught.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import urlparse

import httpx

from ..records import BoardFacts, Posting
from .board_facts import ashby_facts, greenhouse_facts, lever_facts
from .store import text_key


class FetchError(RuntimeError):
    """The posting could not be fetched; the message says why, and pasting the text is the way round it."""


# A page larger than this is not a job posting; reading it would only fill memory.
PAGE_LIMIT_BYTES = 3_000_000
# Fewer characters than this and the page is drawn by JavaScript or sits behind a login.
MIN_POSTING_CHARACTERS = 300
REQUEST_HEADERS = {"User-Agent": "tailor-engine (reads one job posting at a time)", "Accept-Language": "en-US,en;q=0.9"}


# ─────────────────────────────────────────────────────────────
# Text
# ─────────────────────────────────────────────────────────────


def normalise_text(text: str) -> str:
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_html(markup: str) -> str:
    """Text from HTML: block ends become line breaks, tags become spaces.

    A whole web page is reduced the same way, navigation and footers included. A text-extraction library
    would drop those, but the text is the saved reading's key, so it must not depend on whether an optional
    package is installed.
    """
    if "&lt;" in markup and "<" not in markup:
        markup = html.unescape(markup)  # Greenhouse sends its HTML entity-escaped
    markup = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", markup, flags=re.I)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return normalise_text(markup)


def make_posting(
    text: str, source: str, *, url: str | None, title: str | None, company: str | None, board: BoardFacts
) -> Posting:
    """A posting record, its text tidied the same way whatever its source."""
    text = normalise_text(text)
    return {
        # A posting without a URL, or whose URL has no job number or long hex id, is named by its text, so
        # two such postings are never saved over each other. So is a plain company page: its job number is unique
        # only on that company's site and could match another posting's.
        "id": (posting_id_from_url(url) if url and source != "html" else None) or f"{source}-{text_key(text)[:8]}",
        "url": url,
        "source": source,
        "title": title or "",
        "company": company or "",
        "text": text,
        "board": board,
    }


def posting_id_from_url(url: str) -> str | None:
    match = re.search(r"/jobs/(\d+)", url) or re.search(r"([0-9a-f]{8})-[0-9a-f-]{27}", url)
    return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────


def _require_public_address(url: str) -> None:
    """Refuse a URL that is not http(s) or whose host resolves to a private, local or reserved address.

    Without it a posting URL could make this machine fetch from itself or its own network.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchError("only http(s) URLs are accepted")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as error:
        raise FetchError(f"cannot resolve host {parsed.hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("the URL points at a private or local address; paste the posting instead")


def _check_request(request: httpx.Request) -> None:
    _require_public_address(str(request.url))


def _from_greenhouse(client: httpx.Client, url: str, match: re.Match[str]) -> Posting:
    """The posting from Greenhouse's job API; a board that no longer lists it raises FetchError."""
    company_slug, job_id = match.groups()
    response = client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}",
        params={"questions": "false", "pay_transparency": "true"},
    )
    if response.status_code == httpx.codes.OK:
        job = response.json()
        location = (job.get("location") or {}).get("name")
        text = f"{job.get('title', '')}\nLocation: {location or ''}\n\n{strip_html(job.get('content', ''))}"
        return make_posting(
            text,
            "greenhouse",
            url=url,
            title=job.get("title"),
            company=company_slug,
            board=greenhouse_facts(job),
        )
    raise FetchError(f"Greenhouse does not list this posting (HTTP {response.status_code}); it may have closed")


def _from_lever(client: httpx.Client, url: str, match: re.Match[str]) -> Posting:
    """The posting from Lever's API, its lists and closing text joined in; an unlisted posting raises FetchError."""
    company_slug, job_id = match.groups()
    response = client.get(f"https://api.lever.co/v0/postings/{company_slug}/{job_id}")
    if response.status_code == httpx.codes.OK:
        job = response.json()
        text = (
            f"{job.get('text', '')}\nLocation: {(job.get('categories') or {}).get('location', '')}\n\n"
            f"{strip_html(job.get('description', ''))}\n"
        )
        for item in job.get("lists") or []:
            text += f"\n{item.get('text', '')}\n{strip_html(item.get('content', ''))}\n"
        text += "\n" + strip_html(job.get("additional", ""))
        return make_posting(
            text,
            "lever",
            url=url,
            title=job.get("text"),
            company=company_slug,
            board=lever_facts(job),
        )
    raise FetchError(f"Lever does not list this posting (HTTP {response.status_code}); it may have closed")


def _from_ashby(client: httpx.Client, url: str, match: re.Match[str]) -> Posting:
    """The posting found on its Ashby board; one the board no longer lists raises FetchError."""
    company_slug, job_id = match.groups()
    response = client.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}", params={"includeCompensation": "true"}
    )
    if response.status_code == httpx.codes.OK:
        for job in response.json().get("jobs", []):
            if job.get("id") == job_id:
                body = job.get("descriptionPlain") or strip_html(job.get("descriptionHtml", ""))
                text = f"{job.get('title', '')}\nLocation: {job.get('location', '')}\n\n{body}"
                return make_posting(
                    text,
                    "ashby",
                    url=url,
                    title=job.get("title"),
                    company=company_slug,
                    board=ashby_facts(job),
                )
    raise FetchError("Ashby does not list this posting; it may have closed")


def _from_amazon(client: httpx.Client, url: str, match: re.Match[str]) -> Posting:
    """The posting from Amazon's job search, found by its number; one the search no longer lists raises FetchError."""
    job_id = match.group(1)
    response = client.get("https://www.amazon.jobs/en/search.json", params={"base_query": job_id, "result_limit": 5})
    if response.status_code == httpx.codes.OK:
        for job in response.json().get("jobs", []):
            if str(job.get("id_icims")) == job_id or str(job.get("id")) == job_id:
                text = "\n\n".join(
                    filter(
                        None,
                        [
                            job.get("title", ""),
                            f"Location: {job.get('normalized_location') or job.get('location', '')}",
                            "DESCRIPTION\n" + strip_html(job.get("description", "")),
                            "BASIC QUALIFICATIONS\n" + strip_html(job.get("basic_qualifications", "")),
                            "PREFERRED QUALIFICATIONS\n" + strip_html(job.get("preferred_qualifications", "")),
                        ],
                    )
                )
                return make_posting(text, "amazon", url=url, title=job.get("title"), company="Amazon", board={})
        raise FetchError("Amazon does not list this posting; it may have closed")
    raise FetchError(
        f"Amazon's job search did not answer (HTTP {response.status_code}); try again or paste the posting"
    )


def _from_workday(client: httpx.Client, url: str, match: re.Match[str]) -> Posting:
    """The posting from Workday's API, or from its page when the API gives almost no text."""
    hostname, company_slug, site, job_path = match.groups()
    response = client.get(
        f"https://{hostname}/wday/cxs/{company_slug}/{site}/job/{job_path}", headers={"Accept": "application/json"}
    )
    if response.status_code == httpx.codes.OK:
        posting_info = response.json().get("jobPostingInfo", {})
        additional_locations = posting_info.get("additionalLocations") or []
        places = [posting_info.get("location")] + (
            list(additional_locations) if isinstance(additional_locations, list) else [additional_locations]
        )
        location = "; ".join(str(place) for place in places if place)
        description = strip_html(posting_info.get("jobDescription", ""))
        text = f"{posting_info.get('title', '')}\nLocation: {location}\n\n{description}"
        if len(text) > MIN_POSTING_CHARACTERS:
            return make_posting(
                text, "workday", url=url, title=posting_info.get("title"), company=company_slug, board={}
            )
        # Almost no text from the API: the page itself is read instead.
        return _from_page(client, url)
    raise FetchError(f"Workday does not list this posting (HTTP {response.status_code}); it may have closed")


class Board(NamedTuple):
    host_suffix: str  # the URL's host must end with this; "" for any host
    pattern: re.Pattern[str]  # the URL's form; its groups go to `read`
    read: Callable[[httpx.Client, str, re.Match[str]], Posting]


# The first board whose pattern matches decides.
BOARDS = (
    Board("greenhouse.io", re.compile(r"greenhouse\.io/([^/]+)/jobs/(\d+)"), _from_greenhouse),
    Board("lever.co", re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})"), _from_lever),
    Board("", re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})"), _from_ashby),
    Board("", re.compile(r"amazon\.jobs/[a-z-]+/jobs/(\d+)"), _from_amazon),
    Board(
        "",
        re.compile(
            r"https?://(([a-z0-9-]+)(?:\.wd\d+)?\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Za-z]{2}/)?([^/]+)/job/"
            r"(.+?)(?:\?.*)?$"
        ),
        _from_workday,
    ),
)


def _board_for(url: str) -> tuple[Board, re.Match[str]] | None:
    """The job board whose public API serves this URL, with the URL's match; None for any other page."""
    for board in BOARDS:
        match = board.pattern.search(url) if _host(url).endswith(board.host_suffix) else None
        if match:
            return board, match
    return None


def _host(url: str) -> str:
    # The host name alone, so a port or user part in the URL never becomes a web page's company.
    return urlparse(url).hostname or ""


def _from_page(client: httpx.Client, url: str) -> Posting:
    """Any other page, reduced to text.

    It is read in pieces up to a size limit and must be HTML or text; a page too large, of another type, or with almost
    no text (drawn by JavaScript) raises FetchError.
    """
    with client.stream("GET", url) as response:
        if response.status_code != httpx.codes.OK:
            raise FetchError(f"fetch failed: HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            raise FetchError(f"unsupported content type {content_type[:40]}; paste the posting instead")
        received = bytearray()
        for chunk in response.iter_bytes():
            received += chunk
            if len(received) > PAGE_LIMIT_BYTES:
                raise FetchError("the page is too large; paste the posting instead")
        markup = received.decode(response.encoding or "utf-8", errors="ignore")
    text = strip_html(markup)
    if len(text) < MIN_POSTING_CHARACTERS:
        raise FetchError("the page has almost no text (it may be drawn by JavaScript); paste the posting instead")
    title = re.search(r"<title>(.*?)</title>", markup, re.S)
    return make_posting(
        text,
        "html",
        url=url,
        title=strip_html(title.group(1)) if title else None,
        company=_host(url),
        board={},
    )


def fetch_posting(
    url: str, timeout: float = 25.0, transport: httpx.BaseTransport | None = None, check_addresses: bool = True
) -> Posting:
    """{"id", "url", "source", "title", "company", "text", "board"}.

    Raises FetchError. `transport` replaces the network, for tests, which also turn the address check off for their
    made-up hosts.
    """
    url = url.strip()
    if re.search(r"linkedin\.com/", url):
        raise FetchError("LinkedIn's terms forbid automated access; paste the posting instead")
    if check_addresses:
        _require_public_address(url)
    host = _host(url)
    hooks = {"request": [_check_request]} if check_addresses else None
    try:
        with httpx.Client(
            headers=REQUEST_HEADERS, follow_redirects=True, timeout=timeout, transport=transport, event_hooks=hooks
        ) as client:
            found = _board_for(url)
            return found[0].read(client, url, found[1]) if found else _from_page(client, url)
    except FetchError:
        raise
    except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError) as error:
        raise FetchError(
            f"{host} could not be reached ({type(error).__name__}); try again or paste the posting"
        ) from error
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # A board answered with something other than the JSON it publishes (not JSON, or another shape).
        raise FetchError(f"{host} answered in a form this reader does not know; paste the posting instead") from error
