"""The dashboard's two switches for how pages are built, kept in `data/page-modes.json`.

  pages     "hybrid" (the default): where the engine's match is under `Weights.model_projects_below`, the page is built
            from the projects model call 3 chose; "legacy": the engine's projects on every page, no call 3
  call2     who answers model call 2 for a posting read from now on: "opus" (Accuracy, the default, `settings.CALL2`)
            or "jev" (Speed: Jev, with Opus where it cannot answer); with Opus as the classifier
            (TAILOR_CLASSIFIER=opus) Opus answers whatever it says. A mapping already saved stays; Read again makes it
            with the switch as it is then

When call 3 is made is no longer a switch: Jev's call 3 starts beside call 1 on every posting, and Opus's is
made only for a page whose match is low (`Jobs.read`). A file saved with the Efficiency or Speed priority of before
still loads; its "priority" is ignored. A file saved before `call2` loads with its default.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from pathlib import Path
from typing import Any

from tailor_engine import settings
from tailor_engine.reading.store import retried, write_json

LOG = logging.getLogger(__name__)

PAGES = ("hybrid", "legacy")
CALL2 = settings.CLASSIFIERS


@dataclasses.dataclass(frozen=True)
class PageModes:
    pages: str = "hybrid"
    # Read when a PageModes is made, so a test or TAILOR_CALL2 sets it.
    call2: str = dataclasses.field(default_factory=lambda: settings.CALL2)

    @property
    def hybrid(self) -> bool:
        return self.pages == "hybrid"


class PageModesFile:
    """The switches, read from their file and written whole on a change."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def get(self) -> PageModes:
        """The saved switches, or the defaults.

        The defaults stand in quietly for a missing file or switch, and with a warning for a file that cannot be read or
        holds an unknown value.
        """
        try:
            # Tried again while a change on another thread replaces the file (`store.retried`), which would otherwise
            # read as unreadable and switch to the defaults.
            saved: Any = json.loads(retried(lambda: self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return PageModes()
        except (OSError, ValueError):
            saved = None
        readable = isinstance(saved, dict)
        default = PageModes()
        # An older file also holds a "priority", which nothing reads now.
        pages = saved.get("pages", default.pages) if readable else None
        call2 = saved.get("call2", default.call2) if readable else None
        if pages not in PAGES or call2 not in CALL2:
            # A hand-edited file gone wrong would otherwise switch legacy pages to hybrid, and its call 3s, silently.
            LOG.warning("%s is unreadable or holds an unknown value; using the defaults", self.path)
        return PageModes(pages if pages in PAGES else default.pages, call2 if call2 in CALL2 else default.call2)

    def set(self, *, pages: str | None = None, call2: str | None = None) -> PageModes:
        """Change either switch; raises ValueError on a value it does not know."""
        if pages is not None and pages not in PAGES:
            raise ValueError(f"pages must be one of {', '.join(PAGES)}")
        if call2 is not None and call2 not in CALL2:
            raise ValueError(f"call2 must be one of {', '.join(CALL2)}")
        with self._lock:
            saved = self.get()
            changed = PageModes(pages or saved.pages, call2 or saved.call2)
            write_json(self.path, dataclasses.asdict(changed))
            return changed
