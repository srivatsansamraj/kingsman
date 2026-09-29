"""Keeping identifying text away from model providers.

Call 2 sends the capability vocabulary to a model, call 3 the candidate's project list, and the judges projects and
pages. Two things never go:

  operational notes   library notes about publishing, repositories or accounts. They are the candidate's
                      own bookkeeping and irrelevant to choosing projects. Dropped from the library text.
  identity words      any word whose fingerprint is listed in local/identity-fingerprints.txt, plus three
                      fixed patterns: "@gmail", "noreply" and "github.com/". Any match stops the call.

The file holds SHA-256 fingerprints, one a line, never the words themselves, so reading it does not show
what it protects. It does not hide it from a guess: an unsalted hash of one word is confirmed by hashing
the guess, so the file is kept as private as the words. local/ is not in version control. Outgoing text
is split into words and each word's fingerprint is checked. Add a word with `python -m tailor_engine
guard`, which asks for it without echoing it. Without the file there is nothing to check against, and
every call is refused.
"""

from __future__ import annotations

import hashlib
import re

from tailor_engine import settings

FINGERPRINTS_FILE = settings.REPOSITORY_ROOT / "local" / "identity-fingerprints.txt"
# Any email address, a no-reply address or a GitHub link: what most often ties text to a person or an account.
ALWAYS_BLOCKED = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|noreply|github\.com/", re.I)
OPERATIONAL_NOTES = re.compile(
    r"handle|publish|repositor|github|commit|noreply|package name|public push|public page|"
    r"anonym|opsec|identity",
    re.I,
)
# The fix a refused call names: one word the user never wants sent, such as a handle or a private project's name.
ADD_A_WORD = "Add a word that must never reach a model provider with: python -m tailor_engine guard"
FINGERPRINTS_FILE_HEADER = (
    "# SHA-256 fingerprints of words that must never reach a model provider, one a line.\n"
    "# Add one with: python -m tailor_engine guard\n"
)


class PrivacyError(RuntimeError):
    """Text about to leave the machine holds an identity word, or the fingerprints file is missing."""


def fingerprint(word: str) -> str:
    return hashlib.sha256(word.strip().lower().encode("utf-8")).hexdigest()


def words_of(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def identity_fingerprints() -> set[str]:
    """The listed fingerprints. Raises PrivacyError when the file is missing or lists none.

    With nothing to check against, every text would pass, so no call is allowed instead.
    """
    if not FINGERPRINTS_FILE.exists():
        raise PrivacyError(f"{FINGERPRINTS_FILE} is missing; nothing is sent to a model without it. {ADD_A_WORD}")
    fingerprints = {
        line.strip()
        for line in FINGERPRINTS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not fingerprints:
        raise PrivacyError(
            f"{FINGERPRINTS_FILE} lists no fingerprints; nothing is sent to a model without them. {ADD_A_WORD}"
        )
    return fingerprints


def add_identity_word(word: str) -> None:
    """Add a word's fingerprint to the file, creating it if needed."""
    if words_of(word) != {word.strip().lower()}:
        raise ValueError("give one word of letters and digits; it is matched against whole words")
    FINGERPRINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = FINGERPRINTS_FILE.read_text(encoding="utf-8") if FINGERPRINTS_FILE.exists() else FINGERPRINTS_FILE_HEADER
    if fingerprint(word) not in existing:
        FINGERPRINTS_FILE.write_text(existing + fingerprint(word) + "\n", encoding="utf-8")


def check_outgoing(text: str) -> None:
    """Raises PrivacyError when `text` holds an identity word.

    Call 3, on Opus and on Jev, and the judges check only what comes from the candidate: the posting is public and may
    carry its own links. Call 2's request, on Jev and on Opus, is checked with the posting's text in it.
    """
    fingerprints = identity_fingerprints()
    if ALWAYS_BLOCKED.search(text) or any(fingerprint(word) in fingerprints for word in words_of(text)):
        raise PrivacyError("an identity word is in the text about to be sent; nothing was sent")
