"""TypeSafe's Jev, the hosted classifier that answers call 3 and call 2 where chosen, and its Opus fallback.

Jev writes no text. A request carries a state (any JSON) and questions about it, which refer to parts of the state by
backticked path; each question comes back as a typed answer with probabilities. Measured: a median of 0.5 s
a request, six requests at once without an error, and nothing on the Claude plan; TypeSafe charges $0.042 a million
input tokens, output free.

The key is the user's, in the system's credential store (service "typesafe", name "jev"), put there by the user. It is
read when a request is made, only for the request's header, and is never logged, saved or put in an error. A key with
a space, a control character or a character outside ASCII inside it is refused before any request: a header holding
one fails inside the HTTP library with an error that repeats the header.

HTTP 429 (too many requests: TypeSafe allows 1,200 requests a minute and 250,000 tokens a second, and eight requests in
flight drew 429s in a test of call 1) is not Jev failing: the request is made again after the wait TypeSafe names
(Retry-After), else after 1 s and then 2 s, at most twice more. A network error, a timeout or an HTTP 5xx gets one
more attempt; a missing key, another HTTP 4xx or an answer that does not fit the questions does not. When Jev still
cannot answer, the caller asks Opus, and the saved record says so (`fell_back`). There is no fallback on the quality of
an answer: nothing measured tells a poor answer from a good one.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from ..records import CallRecord

URL = "https://api.typesafe.ai/v1/systemone"
# Pinned: the thresholds of calls 2 and 3 were tuned on this version, and TypeSafe's models page says to pin
# the versioned id once tuned. Records made before say "jev-latest"; a Jev record is known by the prefix "jev".
MODEL = "jev-1.13.0"
KEY_SERVICE, KEY_NAME = "typesafe", "jev"
# The slowest request measured took 0.86 s alone and 1.97 s with six at once; a request that hangs reaches
# Opus after 5 s, twice.
TIMEOUT_SECONDS = 5.0
ATTEMPTS = 2
# After an HTTP 429: at most this many more requests, each after TypeSafe's Retry-After, else after these waits.
LIMITED_RETRIES = 2
BACKOFF_SECONDS = (1.0, 2.0)
# A longer Retry-After is waited as this: a posting waits no longer for one retry than for a request that hangs.
LONGEST_WAIT_SECONDS = TIMEOUT_SECONDS

# Requests in flight when the caller gives no slot of its own (the command line, the benchmark): TypeSafe drew 429s at
# eight in flight, and a posting's call 2 is now several requests at once.
AT_ONCE = 6
_IN_FLIGHT = threading.BoundedSemaphore(AT_ONCE)

# `post(url, json, headers)` returns (HTTP status, the response's JSON or None): httpx by default, a stand-in in tests.
Post = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]


class JevUnavailableError(RuntimeError):
    """Jev could not answer: no key, a failed request, or an answer that does not fit the questions.

    `reasons` holds one line for each attempt that failed.
    """

    def __init__(self, *reasons: str) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class JevRetryableError(JevUnavailableError):
    """A network error, a timeout or an HTTP 5xx: Jev is asked once more."""


class JevRateLimitedError(JevUnavailableError):
    """HTTP 429: Jev is asked again after `retry_after` (the response's Retry-After header, when it has one)."""

    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__(f"TypeSafe answered HTTP {HTTPStatus.TOO_MANY_REQUESTS.value}")
        self.retry_after = retry_after


@dataclass
class Answers:
    """Jev's answers to one request, with what the request cost."""

    answers: dict[str, Any]  # question name -> its typed answer, as TypeSafe sent it; the caller checks each one
    answered_by: str  # the model version TypeSafe reports
    input_tokens: int
    output_tokens: int
    attempts: int
    first_errors: list[str]  # why an attempt before the last failed
    seconds: float


def ask(
    body: dict[str, Any],
    *,
    post: Post | None = None,
    key: Callable[[], str] | None = None,
    slot: AbstractContextManager[Any] | None = None,
) -> Answers:
    """Jev's answers to one request. Raises JevUnavailableError when Jev cannot answer.

    `slot` is held around each attempt, so a caller can cap the requests in flight (the dashboard's six); without one,
    the process-wide cap of `AT_ONCE` is held.
    """
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {(key or stored_key)()}"}
    reasons: list[str] = []
    failed = limited = 0
    while True:
        try:
            with slot or _IN_FLIGHT:
                status, data = (post or _httpx_post)(URL, body, headers)
            answers = _answers(status, data)
        except JevRateLimitedError as error:
            # Asked again after a wait, outside the slot, so the wait holds no place among the requests in flight.
            reasons.append(str(error))
            limited += 1
            if limited > LIMITED_RETRIES:
                raise JevUnavailableError(*reasons) from None
            time.sleep(_wait_seconds(error.retry_after, limited))
            continue
        except JevRetryableError as error:
            reasons.append(str(error))
            failed += 1
            if failed == ATTEMPTS:
                raise JevUnavailableError(*reasons) from error
            continue
        except JevUnavailableError as error:
            raise JevUnavailableError(*reasons, str(error)) from error
        except Exception as error:
            # Anything else the request raises becomes a reason Opus can stand in for. Only its kind is kept, and it is
            # not chained: its text may repeat the request, key included.
            raise JevUnavailableError(*reasons, f"the request failed ({type(error).__name__})") from None
        usage: dict[str, Any] = data["usage"] if isinstance(data.get("usage"), dict) else {}
        return Answers(
            answers=answers,
            answered_by=str(data.get("model") or MODEL),
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
            attempts=len(reasons) + 1,
            first_errors=reasons,
            seconds=round(time.perf_counter() - started, 2),
        )


def sent_text(body: Any) -> str:
    """Every key and string in a request body, one a line: the text the privacy guard reads before it is sent.

    The strings as TypeSafe reads them, not as JSON, where a tab or a line break is an escape joined to the next word,
    which would hide that word.
    """
    if isinstance(body, dict):
        return "\n".join(f"{key}\n{sent_text(value)}" for key, value in body.items())
    if isinstance(body, list):
        return "\n".join(sent_text(value) for value in body)
    return body if isinstance(body, str) else ""


def _answers(status: int, data: Any) -> dict[str, Any]:
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        raise JevRateLimitedError()
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        raise JevRetryableError(f"TypeSafe answered HTTP {status}")
    if status != HTTPStatus.OK:
        raise JevUnavailableError(f"TypeSafe answered HTTP {status}")
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailableError("TypeSafe's answer holds no answers")
    return answers


def _wait_seconds(retry_after: str | None, limited: int) -> float:
    """The wait before asking again after the `limited`th HTTP 429: its Retry-After in seconds, else the backoff.

    A Retry-After given as a date, which TypeSafe is not known to send, is waited as the backoff.
    """
    try:
        seconds = float(retry_after) if retry_after is not None else math.nan
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        return BACKOFF_SECONDS[limited - 1]
    return min(seconds, LONGEST_WAIT_SECONDS)


def _count(value: Any) -> int:
    """A token count from the answer's usage; 0 for one that is missing or not a number."""
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) else 0


def number(answer: Any, field: str) -> float:
    """A probability from one typed answer; JevUnavailableError when the answer does not carry one from 0 to 1."""
    value = answer.get(field) if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JevUnavailableError(f"an answer without its {field}")
    if not 0 <= value <= 1:
        # NaN fails this too: the answer's JSON may carry NaN or Infinity, which Python's reader accepts.
        raise JevUnavailableError(f"an answer whose {field} is not a probability from 0 to 1")
    return float(value)


def stored_key() -> str:
    """The user's key from the credential store, without surrounding whitespace.

    JevUnavailableError when there is none, it cannot be read, or it holds a space, a control character or a character
    outside ASCII, which no key has and a header cannot carry; the error never holds the key.
    """
    try:
        # Imported here: building pages from saved readings needs no credential store.
        import keyring
    except ImportError as error:
        raise JevUnavailableError("keyring is not installed, so no Jev key can be read") from error
    try:
        key = keyring.get_password(KEY_SERVICE, KEY_NAME)
    except Exception as error:
        # Any error, not only keyring's own: its Windows backend passes on the store's (no logon session, as over SSH or
        # from a scheduled task). Only its kind and its Windows error number, when it has one, are kept (1312: no logon
        # session), and it is not chained.
        number = getattr(error, "winerror", None)
        windows = f", Windows error {number}" if isinstance(number, int) and not isinstance(number, bool) else ""
        raise JevUnavailableError(f"the credential store could not be read ({type(error).__name__}{windows})") from None
    key = (key or "").strip()
    if not key:
        raise JevUnavailableError("no Jev key in the credential store")
    if not (key.isascii() and key.isprintable()) or any(character.isspace() for character in key):
        raise JevUnavailableError(
            "the Jev key in the credential store holds a space, a control character or a character outside ASCII; "
            "store it again"
        )
    return key


def _httpx_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    import httpx  # imported here, as the fetcher does

    # Only the error's kind goes into the reason, and httpx's error is not chained: its text may repeat the header.
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        raise JevRetryableError(f"the request timed out after {TIMEOUT_SECONDS:g} s") from None
    except httpx.LocalProtocolError as error:
        # The request itself is malformed (a header value the protocol refuses): sending it again cannot help.
        raise JevUnavailableError(f"the request could not be sent ({type(error).__name__})") from None
    except httpx.HTTPError as error:
        raise JevRetryableError(f"the request failed ({type(error).__name__})") from None
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        raise JevRateLimitedError(response.headers.get("retry-after"))
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def fell_back(record: CallRecord, error: JevUnavailableError, *, seconds: float) -> None:
    """Mark Opus's record of a call Jev could not answer as a fallback.

    Opus's own fields stay; the fallback notice names Jev as the model asked for and the model that answered, and keeps
    the refusal category of Opus's own fallback, when the command line fell back from a refusing model; each of Jev's
    reasons leads the first errors. `seconds` is the time the posting waited on Jev first, added to Opus's.
    """
    own = record.get("fallback")
    record["fallback"] = {
        "original_model": MODEL,
        "fallback_model": record.get("answered_by") or record.get("model"),
        "api_refusal_category": own["api_refusal_category"] if own else None,
    }
    record["first_errors"] = [f"Jev: {reason}" for reason in error.reasons] + list(record.get("first_errors") or [])
    record["seconds"] = round(seconds + float(record.get("seconds") or 0.0), 1)
