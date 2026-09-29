"""The candidate's library, read once from seven TOML files and checked.

  projects.toml       projects and their bullets, with each bullet's capabilities and conditions
  vocabulary.toml     the topic tags, and which count as related
  capabilities.toml   the capability vocabulary: domains, their tags and families
  capability-descriptions.toml   what Jev's call 2 is told about each capability
  skills.toml         the skill rows
  education.toml      the degrees and their courses
  skill-capabilities.toml   the capabilities each skill and course shows

`Library.load` raises LibraryError at the first file with problems, naming every problem in that file.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .. import settings
from ..records import Bullet, Degree, Project, SkillRow
from .capabilities import load_capabilities, vocabulary_hash
from .capability_descriptions import CapabilityDescriptions, descriptions_hash, load_capability_descriptions
from .projects import BulletCapabilities, ProjectProfile, bullet_errors_and_warnings, load_projects
from .records_file import LibraryError
from .section_capabilities import load_section_capabilities
from .sections import DEGREE_TAG, always_shown_text, load_degrees, load_skill_rows, section_entries
from .vocabulary import load_tag_vocabulary


@dataclass
class Library:
    """The candidate's library as the engine reads it: bullets, projects, sections and vocabularies."""

    bullets: list[Bullet]
    projects: dict[str, Project]
    profiles: dict[str, ProjectProfile]
    tag_adjacency: set[frozenset[str]]
    skill_rows: dict[str, SkillRow]
    degrees: dict[str, Degree]
    capability_vocabulary: dict[str, str]  # capability -> domain, in the file's order
    bullet_capabilities: dict[str, BulletCapabilities]  # bullet id -> what it demonstrates
    families: dict[str, list[str]]  # domain -> its families
    domain_tags: dict[str, str]  # domain -> its topic tag
    domain_adjacency: set[frozenset[str]]  # domain tags close across families
    capability_descriptions: CapabilityDescriptions  # what Jev's call 2 is told about each capability
    skill_capabilities: dict[str, list[str]] = field(default_factory=dict)  # skill name -> what it shows
    course_capabilities: dict[str, list[str]] = field(default_factory=dict)  # course name -> what it shows
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path | None = None, as_of: dt.date | None = None) -> Library:
        """Every file of the library read and checked; `as_of` is the date projects' ages are measured from.

        Raises LibraryError at the first file with problems, on errors across bullets (tag weights, unknown
        capabilities), and when degrees are listed but the vocabulary lacks their tag. Warnings are kept on the library
        for the commands to print.
        """
        directory = directory or settings.LIBRARY
        tags, tag_adjacency = load_tag_vocabulary(directory)
        capabilities = load_capabilities(directory)
        descriptions = load_capability_descriptions(directory, capabilities.domain_of_capability)
        projects_file = load_projects(directory, tags, as_of)
        errors, warnings = bullet_errors_and_warnings(projects_file.bullets)
        for bullet_id, entry in projects_file.bullet_capabilities.items():
            for name in entry["capabilities"]:
                if name not in capabilities.domain_of_capability:
                    errors.append(f"{bullet_id}: '{name}' is not in the capability vocabulary")
        if errors:
            raise LibraryError("library errors:\n  " + "\n  ".join(errors))
        skill_rows = load_skill_rows(directory, tags)
        degrees = load_degrees(directory, tags)
        if degrees and DEGREE_TAG not in tags:
            raise LibraryError(f"vocabulary.toml has no '{DEGREE_TAG}' tag, which degrees carry")
        section_capabilities, section_warnings = load_section_capabilities(
            directory, capabilities.domain_of_capability, skill_rows, degrees
        )
        return cls(
            bullets=projects_file.bullets,
            projects=projects_file.projects,
            profiles=projects_file.profiles,
            tag_adjacency=tag_adjacency,
            skill_rows=skill_rows,
            degrees=degrees,
            capability_vocabulary=capabilities.domain_of_capability,
            bullet_capabilities=projects_file.bullet_capabilities,
            families=capabilities.families,
            domain_tags=capabilities.domain_tags,
            domain_adjacency=capabilities.domain_adjacency,
            capability_descriptions=descriptions,
            skill_capabilities=section_capabilities.skills,
            course_capabilities=section_capabilities.courses,
            warnings=warnings + section_warnings,
        )

    @property
    def usable_bullets(self) -> list[Bullet]:
        """Bullets that may go on a page: written, not marked fragile, in a project not held back."""
        return [
            bullet
            for bullet in self.bullets
            if not bullet.pending and not bullet.fragile and not self.projects[bullet.parent].held
        ]

    @property
    def section_entries(self) -> list[Bullet]:
        return section_entries(self.skill_rows, self.degrees)

    @property
    def always_shown_text(self) -> str:
        return always_shown_text(self.degrees, self.skill_rows)

    @property
    def vocabulary_hash(self) -> str:
        return vocabulary_hash(self.capability_vocabulary)

    @property
    def descriptions_hash(self) -> str:
        return descriptions_hash(self.capability_descriptions)


__all__ = ["Library", "LibraryError"]
