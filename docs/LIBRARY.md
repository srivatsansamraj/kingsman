# The library files

The library is seven TOML files and a Word template in `library/`, or in the folder `TAILOR_LIBRARY` names; while
`library/` does not exist, the example library in `examples/library/` is used. This document lists every field the
loaders accept; how to write a library is in [ONBOARDING.md](../ONBOARDING.md). The loaders are in
`src/tailor_engine/library/`, and `examples/library/` is a complete example.

| file | holds |
|---|---|
| `projects.toml` | projects and their bullets, with the capabilities each bullet demonstrates |
| `skills.toml` | the skill rows |
| `education.toml` | the two degrees and their courses |
| `skill-capabilities.toml` | the capabilities each skill and course shows (optional) |
| `vocabulary.toml` | the topic tags bullets, skills and courses may carry |
| `capabilities.toml` | the capability vocabulary: domains, their tags and families, and their capabilities |
| `capability-descriptions.toml` | what Jev is told about each capability when it answers call 2 on Speed |
| `resume-template.docx` | the one-page Word template the page is written into |

## Loading and checking

`Library.load` reads the files in this order: `vocabulary.toml`, `capabilities.toml`, `capability-descriptions.toml`,
`projects.toml`, `skills.toml`, `education.toml`, `skill-capabilities.toml`. It refuses the library at the first file with problems and names every
problem in that file:

- a TOML syntax error, with its line;
- a record that is not a table, a missing required field, a value of the wrong type (an integer is accepted where a
  number with a decimal point is expected);
- **an unknown field**, so a misspelled field name is caught;
- a repeated id, tag, domain or capability;
- a tag not in `vocabulary.toml`, a capability not in `capabilities.toml`;
- a bullet's tag weights not summing to 1.

Some problems let the library load but may not do what was meant. These are *warnings*, which `page` and the benchmark
print: a bullet with no evidence words, an `excludes` naming a bullet that is not written, a skill or course with no
entry in `skill-capabilities.toml`. To check a library on its own:

```sh
python -c "from tailor_engine.library import Library; library = Library.load(); print(len(library.usable_bullets), 'usable bullets'); print('\n'.join(library.warnings) or 'no warnings')"
```

In the tables below, **read by** says what uses each field:

- **page**: printed on the page;
- **selection**: used to choose the page;
- **call 3**: sent to a model provider in call 3 (hybrid mode);
- **judges**: sent to Anthropic when a judge is asked about a page;
- **people**: accepted and kept for people reading the file; no code reads it.

## projects.toml

A file of `[[project]]` tables, each followed by its `[[project.note]]` and `[[project.bullet]]` tables. File-level
fields for people: `about` and `capability_map_about` (strings), `appendix` and `capability_map_appendix` (arrays).

### [[project]]

| field | type | required | read by | meaning |
|---|---|---|---|---|
| `id` | string | yes | selection, call 3, judges | unique across projects, with no dot; every bullet id starts with it |
| `title` | string | yes | page, call 3, judges | the project's heading on the page |
| `facts` | string | yes | call 3, judges | the status line: dates, and whether the work is built, deployed, designed or in progress. One line |
| `complexity` | number, 0 to 1 | yes | selection | how hard the work is by today's standard (rubric in ONBOARDING.md, section 9) |
| `brand` | number, 0 to 1 | yes | selection | how much the name behind the work tells a reader |
| `ends` | string | yes | selection, call 3, judges | `"YYYY-MM"`, the month the work ended; `"present"`, `"ongoing"` or `""` while it runs. Sets the project's age and its place in the page order |
| `summary` | string | no | call 3 | what the project is, in one line of fact. Without it, call 3 sees only the title and status line |
| `held` | boolean | no | selection | `true` keeps every bullet of the project off every page and the project out of call 3's list. The judges still receive it |
| `ends_note` | string | no | judges | a note on the end date |
| `note` | array of tables | no | judges | see below |
| `bullet` | array of tables | no | | see below |
| `capability_focus`, `map_title` | string | no | people | |
| `later_notes` | array | no | people | |

Example, from `examples/library/projects.toml`:

```toml
[[project]]
id = "G1"
title = "Prompt-Injection Evaluation of Tool-Using LLM Agents, Applied Security Lab"
facts = "Graduate research, January 2025 to May 2026; harness built and results written up in a lab technical report."
complexity = 0.7
brand = 0.45
ends = "2026-05"
summary = "A test harness and measurements of prompt-injection attacks and defenses on LLM agents that call tools."
```

### [[project.note]]

| field | type | required | meaning |
|---|---|---|---|
| `text` | string | yes | a paragraph about the project |
| `private` | boolean | yes | `true`: never sent to a judge |

The judges receive at most three of a project's notes, each cut at 600 characters. They never receive a note marked
`private`, nor one containing any of these word parts, case ignored, anywhere in its text: handle, publish, repositor,
github, commit, noreply, package name, public push, public page, anonym, opsec, identity. "published" matches
"publish", so a note saying a paper was published is left out too.

### [[project.bullet]]

| field | type | required | read by | meaning |
|---|---|---|---|---|
| `id` | string | yes | selection, judges | `<project id>.<n>`; unique across every bullet, pending ones included |
| `text` | string | unless pending | page, judges | the sentence, printed word for word. Runs of white space become one space |
| `tags` | table of tag = weight | unless pending | selection | topic tags from `vocabulary.toml`, weights summing to 1 within 0.005. They reach selection through the bullet's evidence words, in the word-based demand |
| `evidence` | array of strings | no | selection | the words a posting might use for what the bullet shows. They build the word-based demand and measure how much two chosen bullets repeat each other; they never meet a requirement. Empty gives a warning |
| `capabilities` | array of `{ name, why }` | no | selection | what the bullet demonstrates, strongest first. `name` must be a capability in `capabilities.toml`; `why`, optional and for people, says where the bullet shows it. The first carries 0.6 of the bullet's topic weight |
| `conditions` | array of strings | no | selection | the languages, tools, platforms or degrees the bullet establishes. When the bullet is on the page they count as visible, even where its text does not name them |
| `excludes` | array of bullet ids | no | selection | bullets never placed beside this one, such as another wording of the same result. Listing it on one side is enough; an id that is not a written bullet gives a warning |
| `liability` | number | no | selection | a known weakness: the bullet's off-topic cost is multiplied by 1 plus this value. 0 when absent |
| `fragile` | boolean | no | selection | `true` keeps the bullet off every page. The judges still receive it |
| `pending` | boolean | no | selection | `true`: not written yet; needs no text or tags, and is never placed or sent |
| `label`, `unmapped`, `source` | string | no | people | `source` names where the claim can be checked |
| `remark` | array of `{ label, text }` | no | people | any other note on the bullet; both fields required |

A bullet is *usable* when it has text, is not fragile and its project is not held. Only usable bullets reach a page,
and only projects with a usable bullet are in call 3's list.

Example, from `examples/library/projects.toml`:

```toml
[[project.bullet]]
id = "G1.1"
text = "Built a harness that runs 420 prompt-injection attacks against tool-using LLM agents in sandboxed tasks and records whether each attack reached a tool call."
tags = { "ai.security" = 0.6, "ai.agents" = 0.2, research = 0.2 }
evidence = [
  "LLM red teaming", "AI red teaming", "prompt injection", "LLM agents", "agentic AI", "tool use",
  "evaluation harness", "Python", "Docker",
]
capabilities = [
  { name = "LLM red teaming", why = "420 prompt-injection attacks run against agents" },
  { name = "evaluation and benchmark design", why = "the harness and its success criterion" },
]
conditions = ["Python", "Docker"]
source = "Lab technical report, May 2026"
```

## skills.toml

A file of `[[row]]` tables. File-level fields for people: `about` (string), `appendix` (array).

| field | type | required | read by | meaning |
|---|---|---|---|---|
| `id` | string | yes | selection | unique across rows. The row with id `tools` is printed last when it is chosen |
| `titles` | array of `{ text, tags }` | see meaning | page, selection | the titles the row may be printed under; the one whose tags best fit the posting's word-based demand is used, the earlier on a tie. A fixed row is printed under its first. The loader accepts a row with none, and building a page that would show it then fails, so give every row at least one |
| `members` | array of `{ text, tags }` | no | page, selection | the skills the row may list; at most 8 are printed |
| `prior` | number | no | selection | the row's value on any page, 0 to 1 by convention, 0.5 when absent. It breaks ties and picks rows when the posting points at none |
| `fixed` | boolean | no | page, selection | `true`: shown on every page as written. The template prints it as fixed text, so its skills must match the template's line; they count as visible text when a requirement names a condition |
| `notes` | array | no | people | |

In each title and member, `text` and `tags` are both required and no other field is accepted; tags come from
`vocabulary.toml`, and their weights are not required to sum to 1. A skill may sit in two rows; it is printed once, in
whichever of them the engine takes first. The page prints exactly five rows chosen from the rows that are not fixed,
so a library needs at least five of those.

Example, from `examples/library/skills.toml`, shortened:

```toml
[[row]]
id = "offsec"
prior = 0.6
titles = [
  { text = "Offensive Security", tags = { "security.offensive" = 1.0 } },
  { text = "Vulnerability Research", tags = { "security.offensive" = 0.6, "reverse-engineering" = 0.4 } },
]
members = [
  { text = "Fuzzing", tags = { "security.offensive" = 0.8, "security.appsec" = 0.2 } },
  { text = "Ghidra", tags = { "reverse-engineering" = 1.0 } },
]

[[row]]
id = "certs"
fixed = true
titles = [
  { text = "Certifications", tags = { "security.grc" = 1.0 } },
]
members = [
  { text = "OSCP", tags = { "security.offensive" = 1.0 } },
]
```

## education.toml

A file of `[[degree]]` tables, in the order they are printed. File-level field for people: `about`.

| field | type | required | read by | meaning |
|---|---|---|---|---|
| `id` | string | yes | selection | unique across degrees |
| `institution` | string | yes | selection, judges | always-visible text; the template prints it |
| `award` | string | yes | selection, judges | for example "Master of Science in Cybersecurity". Always-visible text; the template prints it |
| `dates` | string | no | judges | the template prints it |
| `evidence` | array of strings | no | selection | words for the field of study. Always-visible text, and they enter the word-based demand under the topic tag `research` |
| `names` | array of strings | no | selection | other names a posting may use for the degree ("bachelor's degree", "BS in Computer Science"). Always-visible text only, so they meet a degree condition without moving the topic mix |
| `fixed` | string | no | selection | a line printed under the degree on every page, for example a research or teaching position. Always-visible text. The template prints it as fixed text and has room for one such line, under the first degree |
| `courses` | array of `{ text, tags }` | no | page, selection | chosen per posting, at most 11 printed; the Word document needs at least 8 per degree |
| `note` | string | no | people | |
| `notes` | array | no | people | |

*Always-visible text* is the text on every page outside the projects: each degree's institution, award, evidence,
names and fixed line, and the fixed skill rows. A requirement's condition found there is met before any project is
chosen. The template prints each degree's institution, award, dates and fixed line as fixed text, so edit the template
and this file together.

## skill-capabilities.toml

Optional. When present it needs both tables. From `examples/library/skill-capabilities.toml`:

```toml
[skills]
"Threat Modeling" = ["threat modelling"]
"Fuzzing" = ["vulnerability research", "security test automation"]
"Python" = []

[courses]
"Software Security" = ["application security", "vulnerability research"]
```

Each key is the exact text of a skill in a row that is not fixed, or of a course; a key that names neither is an error,
and so is a value that is not a list of capability names from `capabilities.toml`. List the most representative
capability first. A skill fits a requirement in full when the requirement names it whole or asks for a capability
listed here, and in part when it asks for another capability in the same domain. An empty list means the skill is
matched by its name alone, with no warning. A skill or course with no entry is matched by its name alone and gives a
warning. Without the file, every skill and course is matched by name and the library warns once. File-level field for
people: `about`.

## vocabulary.toml

| table | field | required | meaning |
|---|---|---|---|
| `[[tag]]` | `name` | yes | a topic tag. The part before the dot is its family (`security.appsec`, `security.offensive`); a bare name such as `research` stands alone |
| `[[tag]]` | `covers`, `anchored_by` | no | for people |
| `[[adjacency]]` | `a`, `b` | yes | two tags from different families that count as related when courses are ranked by topic |

File-level fields for people: `about`, `adjacency_notes`, `appendix`. The vocabulary must contain the tag `research`,
which every degree carries. Renaming or removing a tag means retagging every entry that carries it; the loader names
each one. The shipped file holds 25 tags and 7 adjacent pairs.

## capabilities.toml

| table | field | required | meaning |
|---|---|---|---|
| `[[domain]]` | `name` | yes | the domain's name, unique, for example "Offensive security" |
| `[[domain]]` | `tag` | yes | the domain tag, unique, for example `sec.offensive`. A separate set from the topic tags of `vocabulary.toml`; two domain tags with the same part before the dot count as related |
| `[[domain]]` | `families` | yes | the families the domain belongs to, usually one; a domain that is both, such as AI security, lists two |
| `[[domain]]` | `capabilities` | yes | the domain's capabilities, each unique across the whole file, in the order call 2 lists them |
| `[[domain_adjacency]]` | `a`, `b` | yes | two domain tags that count as related across families when the page is scored |

File-level fields for people: `about`, `domains_notes`, `families_notes`, `appendix`.

Saved mappings are tied to a hash of the capability and domain pairs. Renaming a capability or moving it to another
domain makes every saved mapping stale: the next `read` of each posting makes call 2 again (call 1 is not repeated),
and `page` refuses to build from a stale mapping. Editing the notes, a domain's tag or its families changes no hash.

The shipped file holds 76 capabilities in 17 domains and five families (Security, AI and data, Software, Systems,
Professional), drafted around security and AI roles. A capability that no bullet demonstrates is a gap: requirements
that ask for it count as unmet.

## capability-descriptions.toml

One table per capability of `capabilities.toml`, keyed by the capability's name, and one keyed `"none of these"`. Jev
is sent every table, in the vocabulary's order, with each posting it maps on Speed; Claude Opus is never sent them.

| table | field | required | meaning |
|---|---|---|---|
| `["<capability>"]` | `domain` | yes | the domain `capabilities.toml` puts the capability in, exactly |
| `["<capability>"]` | `what` | yes | the work the capability names |
| `["<capability>"]` | `not_for` | yes | its nearest neighbours, and where their asks belong |
| `["<capability>"]` | `examples` | yes | a list of asks written as a posting would word them |
| `["none of these"]` | `what`, `not_for`, `examples` | yes | the option for a requirement that names only a condition or asks for nothing in the list |

File-level field for people: `about`, which is not sent and is left out of the hash. The library is refused when a
capability has no table, when a table names something that is not a capability, when a table's `domain` differs
from `capabilities.toml`, or when the `"none of these"` table is missing. A saved Jev mapping keeps a hash of every
table (`descriptions_sha256_16`), so an edited description makes it stale; Opus's mappings are unaffected. The shipped
file describes the shipped vocabulary. Example, from `examples/library/capability-descriptions.toml`:

```toml
["vulnerability research"]
domain = "Offensive security"
what = "finding new, unknown security flaws in software, firmware or protocols; bug hunting, fuzzing, reverse engineering; what security research or cybersecurity research usually means"
not_for = "exploit development (weaponising a known bug); published research (having papers); vulnerability triage and management (handling bugs already reported)"
examples = [
  "Track record of discovering CVEs",
  "Fuzzing and bug hunting",
  "Reverse engineering to find vulnerabilities",
  "Security research background",
]
```

## resume-template.docx

A finished one-page Word resume in which 13 paragraphs are *slots*, found by their Word paragraph ids at fixed
positions in the document:

| slots | count | written as |
|---|---|---|
| coursework | 2, one per degree in library order | `Coursework: a, b, c` |
| projects | 6 | a title line followed by one bullet paragraph per bullet; unused slots are removed |
| skill rows | 5 | `Title: a, b, c` |

Everything else is fixed text: the three header lines (name, contact line, and a third line such as a location), the
section headings, each degree's institution, award and dates, the fixed line under the first degree, and the
certifications line. The engine writes only into the slots and refuses any output whose other content differs from
the template.

The template is pinned by its SHA-256, and by the slots' paragraph ids and positions, constants in
`src/tailor_engine/rendering/word.py` (`COURSEWORK_SLOTS`, `PROJECT_SLOTS`, `SKILL_SLOTS`, `EXPECTED_INDICES`). The
coursework slots must use the template's list numbering 2 and the project slots numbering 3. The SHA-256 is the
example template's (`TEMPLATE_SHA256`) unless a file `resume-template.sha256` beside the template holds another, so
your own template is pinned in your library and the example template, which the tests and the development server
use, stays pinned in the code. To put your own details in it:

1. Edit the fixed text of your copy, `library/resume-template.docx`: name, contact line, degrees, the fixed line,
   certifications. Leave the slot paragraphs, fonts, sizes and margins as they are; the line widths in
   `src/tailor_engine/layout.py` were measured on this layout.
2. Write the new file's SHA-256 into `library/resume-template.sha256` (the first word of the file is read):

   ```sh
   python -c "import hashlib; open('library/resume-template.sha256', 'w').write(hashlib.sha256(open('library/resume-template.docx', 'rb').read()).hexdigest())"
   ```

3. Run `word` on a page. If it stops with "template is missing mutable paragraph IDs" or "paragraph ... moved from
   index", the editor changed a slot's id or position; set the slot constants to the template's values. The
   constants are shared with the example template, so changing them makes the tests and the output check fail on
   it: keep your template's slot paragraphs from the example's, which avoids the change altogether.

Saving the file in Word changes its hash even when no text changed, so repeat step 2 after every save.

## Limits

| limit | value | checked by |
|---|---|---|
| degrees | exactly 2 | the Word writer |
| courses | at least 8 per degree in the library; 8 to 11 printed | the document check before writing |
| skill rows | at least 5 that are not fixed; exactly 5 printed, each with 1 to 8 skills | the document check |
| projects on a page | at most 6 | the selector and the document check |
| lines for project titles and bullets | 28; a bullet takes `ceil(characters / 126)` lines, a title `ceil(characters / 122)` | the selector |
| projects in call 3's list | at least 4, or no call 3 answer can be valid (it names 4 to 6) | call 3's answer check |
| a title, a bullet, a skill, a course | one paragraph, no line break | the document check |
| `summary`, `facts` | one line | `Library.load` |
| bullet tag weights | sum to 1 within 0.005 | `Library.load` |
| `complexity`, `brand` | 0 to 1 | `Library.load` |
| `ends` | `YYYY-MM`, `present`, `ongoing` or empty | `Library.load` |
