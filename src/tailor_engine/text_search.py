"""Finding a requirement's word in text, whole: the one matching rule every part of the engine shares."""

from __future__ import annotations

import re
from collections.abc import Iterable

_SEPARATOR = r"[\s\-_/]"
WORD_SEPARATORS = _SEPARATOR + "+"
# Texts searched together are joined with this. "|" is not a separator, so a two-word requirement cannot
# match across the end of one text and the start of the next.
TEXT_BOUNDARY = " || "


def join_texts(texts: Iterable[str]) -> str:
    return TEXT_BOUNDARY.join(texts)


# All-capital words of up to this many letters are acronyms ("MS", "GDB", "AWS", "HTTP").
ACRONYM_LETTERS = 4
# Words of up to this many characters also refuse a preceding #, + or -.
SHORT_WORD_CHARACTERS = 2


def token_pattern(token: str) -> str:
    """A regular expression for one requirement word, matched whole.

    Acronyms match only in capitals, so a unit or an ordinary word does not count as the acronym; every
    other word matches in any case (a lower-case word always does). Spaces, hyphens, underscores and slashes
    are interchangeable, so "OS internals" matches "OS-internals". Every word refuses a following # or +,
    and short words also refuse a preceding #, + or -, so "C" is not found in "C#" or "C++". A following
    hyphen is allowed: "C" is found in "C-based" (and in "C-suite").
    """
    case_insensitive = not (len(token) <= ACRONYM_LETTERS and token.isalpha() and token.isupper())
    source = token.lower() if case_insensitive else token
    parts = [re.escape(part) for part in re.split(WORD_SEPARATORS, source) if part]
    expression = (_SEPARATOR + "*").join(parts)
    if case_insensitive:
        expression = "(?i:" + expression + ")"
    leading = ""
    if token[:1].isalnum():
        leading = r"(?<![A-Za-z0-9#+\-])" if len(token) <= SHORT_WORD_CHARACTERS else r"(?<![A-Za-z0-9])"
    trailing = r"(?![A-Za-z0-9#+])" if token[-1:].isalnum() else ""
    return leading + expression + trailing


def contains_any(tokens: Iterable[str], text: str) -> bool:
    return any(re.search(token_pattern(token), text) for token in tokens)
