"""One model call through the signed-in Claude Code command line, with reasoning effort set per call.

No key is read or handled: the command line signs in with the user's own account. Each call starts a clean
session with no tools, no machine settings and no MCP servers, because the command line otherwise loads
every tool definition on the machine into the prompt (about 30,000 input tokens a call against 164 without
them).

Run from inside a Claude Code session, the spawned command line would inherit that session's variables:
the SDK starts its environment from this process's and can only override names, not drop them. The ones
that tie a call to the parent session (its ids, its messaging channel, its effort) are overridden with
empty values, and so is an API key, so a stray key never switches billing. The sign-in routing a host
sets (`ANTHROPIC_BASE_URL` and the host's auth variables) is kept, since inside the desktop app it is how
the call signs in. From a plain terminal none of these are set and nothing is overridden.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, NamedTuple, TypedDict, TypeVar

from ..records import CallRecord, Fallback

# CLAUDECODE is not listed: the SDK drops it itself, and an override would put it back, empty.
PARENT_SESSION_VARIABLES = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "ANTHROPIC_API_KEY",
)


# "off" turns extended thinking off; the others are the command line's effort levels.
Effort = Literal["off", "low", "medium", "high", "xhigh", "max"]


class Usage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    seconds: float
    model: str  # the model asked for
    answered_by: str | None  # the model that wrote the answer: another one when the command line fell back
    fallback: Fallback | None  # the command line's notice when it did
    effort: Effort | None


class TextAnswer(Usage, total=False):
    text: str


class JsonAnswer(Usage, total=False):
    json: Any  # the answer's JSON object as the model wrote it; the caller checks it


def parent_session_overrides() -> dict[str, str]:
    """An empty value for each parent-session variable set now, read at call time; only names are read."""
    return {name: "" for name in PARENT_SESSION_VARIABLES if name in os.environ}


def _json_object(text: str) -> Any:
    """The last complete JSON object in the answer.

    A model sometimes writes its answer twice, a draft then a revision (3 of 10 answers of one prompt), and the
    two together are not one object; the revision is taken.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    start = text.find("{")
    while start >= 0:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            found = value
        start = text.find("{", end)
    if found is None:
        raise ValueError("no JSON object in the model's answer")
    return found


class _Reply(NamedTuple):
    text: str
    result: Any  # the command line's ResultMessage, or None
    answered_by: str | None
    fallback: Fallback | None


async def _ask_model(model: str, system_prompt: str, user_prompt: str, effort: Effort | None) -> _Reply:
    """One question to the command line with no tools, settings or MCP servers.

    The answer's text and result, the model that wrote it, and the command line's notice when the model asked for
    refused and another answered. Raises RuntimeError when the command line reports an error, so a failed call is never
    read as an empty answer.
    """
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage, TextBlock, query

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=[],
        tools=[],
        mcp_servers={},
        permission_mode="dontAsk",
        max_turns=1,
        setting_sources=[],
        strict_mcp_config=True,
        env=parent_session_overrides(),
    )
    if effort == "off":
        options.thinking = {"type": "disabled"}
    elif effort:
        options.effort = effort
    text_parts: list[str] = []
    result_message = None
    answered_by: str | None = None
    fallback: Fallback | None = None
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            text_parts += [block.text for block in message.content if isinstance(block, TextBlock)]
            answered_by = message.model
        elif isinstance(message, ResultMessage):
            result_message = message
        elif isinstance(message, SystemMessage) and message.subtype == "model_refusal_fallback":
            # A safety classifier stopped the model asked for, and the command line asked another; the
            # record says so, since the answer is then that other model's.
            fallback = {
                "original_model": message.data.get("original_model"),
                "fallback_model": message.data.get("fallback_model"),
                "api_refusal_category": message.data.get("api_refusal_category"),
            }
    if result_message is not None and getattr(result_message, "is_error", False):
        raise RuntimeError(
            f"the Claude command line returned an error: {str(getattr(result_message, 'result', ''))[:300]}"
        )
    return _Reply("\n".join(text_parts).strip(), result_message, answered_by, fallback)


def call_text(
    model: str, system_prompt: str, user_prompt: str, effort: Effort | None = None, timeout: float = 600
) -> TextAnswer:
    """{"text", "input_tokens", "output_tokens", "seconds", "model", "answered_by", "fallback", "effort"} for one call.

    `model` is the model asked for and `answered_by` the one that wrote the answer; they differ when the command line
    fell back to another model, and `fallback` then holds its notice (None otherwise).
    """
    started = time.perf_counter()
    reply = asyncio.run(asyncio.wait_for(_ask_model(model, system_prompt, user_prompt, effort), timeout))
    usage = getattr(reply.result, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = getattr(usage, "__dict__", {}) or {}
    input_tokens = sum(
        int(usage.get(key) or 0) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return {
        "text": reply.text,
        "input_tokens": input_tokens,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "seconds": round(time.perf_counter() - started, 1),
        "model": model,
        "answered_by": reply.answered_by,
        "fallback": reply.fallback,
        "effort": effort,
    }


def call_json(
    model: str, system_prompt: str, user_prompt: str, effort: Effort | None = None, timeout: float = 600
) -> JsonAnswer:
    """As `call_text`, with the answer's JSON object under "json".

    The object is the answer's last complete one; "json" is None when the answer holds none. The caller's answer check
    reports None as a problem, so the answer is asked for again.
    """
    answer = call_text(model, system_prompt, user_prompt, effort, timeout)
    try:
        parsed = _json_object(answer["text"])
    except ValueError:
        parsed = None
    return {
        "input_tokens": answer["input_tokens"],
        "output_tokens": answer["output_tokens"],
        "seconds": answer["seconds"],
        "model": model,
        "answered_by": answer["answered_by"],
        "fallback": answer["fallback"],
        "effort": effort,
        "json": parsed,
    }


# ─────────────────────────────────────────────────────────────
# Checked answers
# ─────────────────────────────────────────────────────────────


class ModelAnswerError(RuntimeError):
    """A model's answer could not be used, even after it was asked for again."""


AnswerT = TypeVar("AnswerT", bound=Usage)


@dataclass
class CheckedAnswer(Generic[AnswerT]):
    """The last answer, what is still wrong with it ([] when it can be used), and the usage of every call."""

    answer: AnswerT
    problems: list[str]
    first_problems: list[str]
    attempts: int
    input_tokens: int
    output_tokens: int
    seconds: float  # from the first call's start to the last call's end, rounded to a tenth


def ask_checked(
    call: Callable[[str, str], AnswerT],
    system_prompt: str,
    user_prompt: str,
    problems_of: Callable[[AnswerT], list[str]],
    retry_note: Callable[[list[str]], str],
) -> CheckedAnswer[AnswerT]:
    """One call and its check.

    When the check finds problems, one more call, with `retry_note(problems)` added to the user text. Every model call
    of the engine and the bench is made through here, except call 2's batched call, whose postings are
    checked one by one and asked for again on their own (`capability_mapping.map_many`).
    """
    started = time.perf_counter()
    answer = call(system_prompt, user_prompt)
    problems = problems_of(answer)
    checked = CheckedAnswer(answer, problems, list(problems), 1, *_usage(answer), seconds=0.0)
    if problems:
        answer = call(system_prompt, user_prompt + retry_note(problems))
        input_tokens, output_tokens = _usage(answer)
        checked.answer = answer
        checked.problems = problems_of(answer)
        checked.attempts = 2
        checked.input_tokens += input_tokens
        checked.output_tokens += output_tokens
    checked.seconds = round(time.perf_counter() - started, 1)
    return checked


def _usage(answer: Usage) -> tuple[int, int]:
    return answer.get("input_tokens", 0), answer.get("output_tokens", 0)


def call_outcome(checked: CheckedAnswer[Any]) -> CallRecord:
    """What calls 2 and 3 save of a checked call: whether its answer can be used, what was wrong, attempts and cost."""
    return {
        "valid": not checked.problems,
        "errors": checked.problems,
        "first_errors": checked.first_problems,
        "attempts": checked.attempts,
        "input_tokens": checked.input_tokens,
        "output_tokens": checked.output_tokens,
        "seconds": checked.seconds,
    }


def answering_model(answer: Usage) -> CallRecord:
    """The model that wrote an answer and the command line's fallback notice, as the saved records keep them."""
    return {"answered_by": answer.get("answered_by"), "fallback": answer.get("fallback")}


def problems_note(problems: list[str]) -> str:
    """The text added to a JSON call's second attempt: what was wrong with the first."""
    return "\n\nYOUR PREVIOUS ANSWER BROKE THESE RULES; answer again:\n- " + "\n- ".join(problems)
