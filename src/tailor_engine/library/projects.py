"""Reading `projects.toml`: the candidate's projects and their bullets, and checking them.

  [[project]]                one project: id, title, facts, complexity and brand (each 0 to 1), ends
                             (YYYY-MM or "present"), notes, a one-line summary (model call 3 reads it with the
                             title, facts and end); other fields for people only
  [[project.note]]           a paragraph for people and the judge; `private` says whether it stays on this
                             machine, and must be given
  [[project.bullet]]         one bullet: text, topic tags (weights summing to 1), evidence words,
                             liability, excludes, the capabilities it demonstrates (each with why), the
                             conditions it establishes (a language, tool, platform), its source
  [[project.bullet.remark]]  any other note on the bullet, with its label ("corrected 2026-09-21", ...)

A bullet with `pending = true` has no text yet and is never placed. `held = true` keeps a project off every
page; `fragile = true` keeps a bullet off every page. Ids are unique: a project's across projects, a bullet's
across every bullet, pending ones included.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from ..records import RUNNING_END_VALUES, Bullet, Project
from .records_file import (
    Schema,
    StringList,
    field_problems,
    id_of,
    raise_if_problems,
    read_toml,
    records_of,
    tag_problems,
)

FILE_NAME = "projects.toml"
PROJECT_FIELDS = Schema(
    {"id": str, "title": str, "facts": str, "complexity": float, "brand": float, "ends": str},
    {
        "held": bool,
        "ends_note": str,
        "note": list,
        "bullet": list,
        "summary": str,
        # For people reading the file; the engine does not use them.
        "capability_focus": str,
        "map_title": str,
        "later_notes": list,
    },
)
NOTE_FIELDS = Schema({"text": str, "private": bool}, {})
BULLET_FIELDS = Schema(
    {"id": str},
    {
        "pending": bool,
        "text": str,
        "tags": dict,
        "evidence": StringList,
        "liability": float,
        "excludes": StringList,
        "fragile": bool,
        "capabilities": list,
        "conditions": StringList,
        # For people reading the file; the engine does not use them.
        "label": str,
        "unmapped": str,
        "source": str,
        "remark": list,
    },
)
CAPABILITY_FIELDS = Schema({"name": str}, {"why": str})
REMARK_FIELDS = Schema({"label": str, "text": str}, {})
FILE_FIELDS = Schema(
    {"project": list},
    {"about": str, "capability_map_about": str, "appendix": list, "capability_map_appendix": list},
)
# Tag weights are written to two decimals, so a bullet's may sum to 1 within rounding.
TAG_WEIGHT_TOLERANCE = 0.005


class BulletCapabilities(TypedDict):
    """What one bullet demonstrates: capability names, and the conditions it establishes."""

    capabilities: list[str]
    conditions: list[str]


@dataclass(frozen=True)
class Note:
    text: str
    private: bool


@dataclass(frozen=True)
class ProjectProfile:
    """What people, the judge and call 3 read about a project, beside the numbers the engine uses."""

    title: str
    facts: str
    ends: str
    ends_note: str
    notes: tuple[Note, ...]
    summary: str = ""


@dataclass
class ProjectsFile:
    bullets: list[Bullet]
    projects: dict[str, Project]
    profiles: dict[str, ProjectProfile]
    bullet_capabilities: dict[str, BulletCapabilities]  # bullet id -> what it demonstrates


def load_projects(library_directory: Path, tags: set[str], as_of: dt.date | None = None) -> ProjectsFile:
    """Everything in projects.toml, checked. Raises LibraryError naming every problem found."""
    document = read_toml(library_directory / FILE_NAME)
    reader = _ProjectsReader(tags, as_of or dt.date.today())
    reader.problems += field_problems(FILE_NAME, document, FILE_FIELDS)
    for project in records_of(document, "project"):
        reader.read_project(project)
    raise_if_problems(FILE_NAME, reader.problems)
    return reader.loaded


# A month the work ended, written YYYY-MM.
END_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


class _ProjectsReader:
    """Reads projects.toml one record at a time, keeping what has been read and every problem found, in file order.

    A record with a problem is left out, since its values cannot be trusted; its problems are reported with the rest
    when the file is done.
    """

    def __init__(self, tags: set[str], as_of: dt.date):
        self.tags = tags
        self.as_of = as_of
        self.loaded = ProjectsFile(bullets=[], projects={}, profiles={}, bullet_capabilities={})
        self.bullet_ids: set[str] = set()
        self.problems: list[str] = []

    def read_project(self, project: object) -> None:
        """One [[project]] table: its checks, its profile and notes, then each of its bullets."""
        where = f"{FILE_NAME} project {id_of(project)}"
        project_problems = field_problems(where, project, PROJECT_FIELDS)
        if project_problems:
            self.problems += project_problems
            return
        if not isinstance(project, dict):  # never true once field_problems found nothing; narrows the type
            return
        project_id = project["id"]
        if project_id in self.loaded.projects:
            # Copying a block to start a new project and keeping its id would otherwise merge the two.
            self.problems.append(f"{where}: the id is used by another project")
            return
        for rating in ("complexity", "brand"):
            if not 0.0 <= project[rating] <= 1.0:
                self.problems.append(f"{where}: {rating} {project[rating]} should be from 0 to 1")
        for line_field in ("summary", "facts"):
            if "\n" in project.get(line_field, "").strip():
                # Call 3 is sent one line a project; a line break would split this one across two.
                self.problems.append(f"{where}: {line_field} should be one line (call 3 reads one line a project)")
        ends = project["ends"]
        if ends.strip().lower() not in RUNNING_END_VALUES and not END_MONTH.fullmatch(ends.strip()):
            # Unchecked, a typo such as "2025-7" would make the project count as still running.
            self.problems.append(f"{where}: ends '{ends}' should be YYYY-MM or present")
            return
        self.loaded.projects[project_id] = Project(
            id=project_id,
            title=project["title"],
            complexity=float(project["complexity"]),
            brand=float(project["brand"]),
            held=project.get("held", False),
            ends=ends,
            as_of=self.as_of,
        )
        notes = []
        for note in records_of(project, "note"):
            note_problems = field_problems(f"{where} note", note, NOTE_FIELDS)
            self.problems += note_problems
            if not note_problems:
                notes.append(Note(note["text"], note["private"]))
        self.loaded.profiles[project_id] = ProjectProfile(
            project["title"],
            project["facts"],
            ends,
            project.get("ends_note", ""),
            tuple(notes),
            project.get("summary", ""),
        )
        for bullet in records_of(project, "bullet"):
            self.read_bullet(bullet, project_id, where)

    def read_bullet(self, bullet: object, project_id: str, project_where: str) -> None:
        """One [[project.bullet]] table. A pending bullet is kept without text; a written one needs text and tags."""
        bullet_id = id_of(bullet)
        where = f"{project_where} bullet {bullet_id}"
        bullet_problems = field_problems(where, bullet, BULLET_FIELDS)
        if bullet_problems:
            self.problems += bullet_problems
            return
        if not isinstance(bullet, dict):  # never true once field_problems found nothing; narrows the type
            return
        if bullet_id in self.bullet_ids:
            bullet_problems.append(f"{where}: the id is used by another bullet")
        self.bullet_ids.add(bullet_id)
        if bullet_id.split(".")[0] != project_id:
            bullet_problems.append(f"{where}: the id does not start with its project's id")
        for remark in records_of(bullet, "remark"):
            bullet_problems += field_problems(f"{where} remark", remark, REMARK_FIELDS)
        if bullet.get("pending"):
            self.problems += bullet_problems
            self.loaded.bullets.append(Bullet(bullet_id, project_id, "", {}, [], pending=True))
            return
        if not bullet.get("text") or not bullet.get("tags"):
            bullet_problems.append(f"{where}: a bullet that is not pending needs text and tags")
        bullet_problems += tag_problems(where, bullet.get("tags", {}), self.tags)
        for capability in records_of(bullet, "capabilities"):
            bullet_problems += field_problems(f"{where} capability", capability, CAPABILITY_FIELDS)
        if bullet_problems:
            self.problems += bullet_problems
            return
        self.loaded.bullets.append(
            Bullet(
                id=bullet_id,
                parent=project_id,
                text=" ".join(bullet.get("text", "").split()),
                tags={tag: float(weight) for tag, weight in bullet.get("tags", {}).items()},
                evidence=list(bullet.get("evidence", [])),
                liability=float(bullet.get("liability", 0.0)),
                fragile=bullet.get("fragile", False),
                excludes=list(bullet.get("excludes", [])),
            )
        )
        self.loaded.bullet_capabilities[bullet_id] = {
            "capabilities": [item["name"] for item in bullet.get("capabilities", [])],
            "conditions": list(bullet.get("conditions", [])),
        }


def bullet_errors_and_warnings(bullets: list[Bullet]) -> tuple[list[str], list[str]]:
    """(errors, warnings) across written bullets.

    An error makes the library unusable; a warning names a bullet that can never meet a requirement or excludes one that
    does not exist. Unknown tags, orphan bullets and repeated ids cannot get this far: loading refuses them.
    """
    errors: list[str] = []
    warnings: list[str] = []
    written = [bullet for bullet in bullets if not bullet.pending]
    for bullet in written:
        total = sum(bullet.tags.values())
        if abs(total - 1.0) > TAG_WEIGHT_TOLERANCE:
            errors.append(f"{bullet.id}: tag weights sum to {total:.3f}, must be 1")
        if not bullet.evidence:
            warnings.append(f"{bullet.id}: no evidence words, so it adds nothing to a posting's topic demand")
    written_ids = {bullet.id for bullet in written}
    for bullet in written:
        for excluded_id in bullet.excludes:
            if excluded_id not in written_ids:
                warnings.append(f"{bullet.id}: excludes '{excluded_id}', which is not a written bullet")
    return errors, warnings
