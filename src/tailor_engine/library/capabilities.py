"""Reading `capabilities.toml`: the capability vocabulary.

A capability is something a person can do ("threat modelling", "backend services and APIs"). Each belongs
to one domain; each domain maps to one topic tag and belongs to one or more families:

  family  Security ─┬─ domain  Application and platform security (tag sec.appsec) ─┬─ threat modelling
                    │                                                                 └─ secure system architecture
                    └─ domain  AI security (tag ai.security) ── ...     (also in the AI and data family)

  [[domain]]             name, tag, families, capabilities (in the order the call-2 prompt lists them)
  [[domain_adjacency]]   two domain tags that count as close across families: a domain in two families is
                         written as closeness to the other family's domains, since a tag has one prefix

Which capabilities each bullet demonstrates is recorded on the bullet itself, in `projects.toml`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .records_file import Schema, StringList, field_problems, id_of, raise_if_problems, read_toml, records_of

FILE_NAME = "capabilities.toml"
FILE_FIELDS = Schema(
    {"domain": list},
    {"about": str, "domains_notes": str, "families_notes": str, "domain_adjacency": list, "appendix": list},
)
DOMAIN_FIELDS = Schema({"name": str, "tag": str, "families": StringList, "capabilities": StringList}, {})
ADJACENCY_FIELDS = Schema({"a": str, "b": str}, {})


@dataclass
class CapabilityVocabulary:
    domain_of_capability: dict[str, str]  # capability -> domain, in the file's order
    domain_tags: dict[str, str]  # domain -> its topic tag
    families: dict[str, list[str]]  # domain -> the families it belongs to
    domain_adjacency: set[frozenset[str]]  # domain tags that count as close across families


def load_capabilities(library_directory: Path) -> CapabilityVocabulary:
    """The capability vocabulary, checked. Raises LibraryError.

    Every domain is named once, every tag used once, every capability named once, and every adjacent tag is a domain's.
    """
    document = read_toml(library_directory / FILE_NAME)
    problems = field_problems(FILE_NAME, document, FILE_FIELDS)
    domain_of_capability: dict[str, str] = {}
    domain_tags: dict[str, str] = {}
    families: dict[str, list[str]] = {}
    for domain in records_of(document, "domain"):
        where = f"{FILE_NAME} domain {id_of(domain, 'name')}"
        domain_problems = field_problems(where, domain, DOMAIN_FIELDS)
        if domain_problems:
            problems += domain_problems
            continue
        name = domain["name"]
        if name in domain_tags:
            problems.append(f"{where}: the name is used by another domain")
            continue
        if domain["tag"] in domain_tags.values():
            problems.append(f"{where}: tag '{domain['tag']}' is used twice")
        domain_tags[name] = domain["tag"]
        families[name] = list(domain["families"])
        for capability in domain["capabilities"]:
            if capability in domain_of_capability:
                problems.append(f"{where}: '{capability}' is already in {domain_of_capability[capability]}")
            domain_of_capability[capability] = name
    domain_adjacency = set()
    for pair in records_of(document, "domain_adjacency"):
        where = f"{FILE_NAME} domain_adjacency"
        pair_problems = field_problems(where, pair, ADJACENCY_FIELDS)
        if pair_problems:
            problems += pair_problems
            continue
        for side in (pair["a"], pair["b"]):
            if side not in domain_tags.values():
                problems.append(f"{where}: '{side}' is not a domain's tag")
        domain_adjacency.add(frozenset((pair["a"], pair["b"])))
    raise_if_problems(FILE_NAME, problems)
    return CapabilityVocabulary(domain_of_capability, domain_tags, families, domain_adjacency)


def vocabulary_hash(vocabulary: dict[str, str]) -> str:
    """A fingerprint of the (capability, domain) pairs.

    A saved posting mapping names capabilities, so it is stale once one is renamed or moved; editing the notes in the
    file does not make it stale.
    """
    return hashlib.sha256(repr(sorted(vocabulary.items())).encode("utf-8")).hexdigest()[:16]


def load_capability_vocabulary(library_directory: Path) -> dict[str, str]:
    """Capability -> domain, in the file's order: what the call-2 prompt lists and the mapping hash covers."""
    return load_capabilities(library_directory).domain_of_capability
