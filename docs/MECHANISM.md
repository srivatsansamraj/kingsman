# How Kingsman chooses a page

For a reader who wants to check the engine's choices against its code. Each section names the module that implements
it, under `src/tailor_engine/`. Numbers are the defaults in `selection/weights.py`, named in brackets, or the
constants named beside them.

## 1. Terms

| term | meaning |
|---|---|
| library | the candidate's authored material: projects, bullets, skill rows, degrees and courses, the topic vocabulary, the capability vocabulary and its descriptions. Seven TOML files and a Word template ([LIBRARY.md](LIBRARY.md)) |
| project | one piece of work: a job, an internship, a course project, a personal build. It has a title, an end date, a complexity and a brand score from 0 to 1, a one-line summary and a one-line status (`facts`) |
| bullet | one true sentence about a project that can stand on the page alone. The unit the engine chooses |
| usable bullet | a bullet with text, not marked `fragile`, in a project not marked `held` |
| capability | something a person can do, specific enough to mean one thing, for example "threat modelling". The shipped vocabulary has 76 |
| domain, family | a domain groups capabilities (for example "Offensive security"); a family groups domains (Security, AI and data, Software, Systems, Professional). A domain may sit in two families. Each domain has a *domain tag*, such as `sec.offensive` |
| topic tag | a label from `vocabulary.toml` (25 in the shipped file, such as `security.appsec`), authored on bullets, skills and courses. A separate set from the domain tags |
| condition | a language, tool, platform, degree, certification or number of years a requirement names as needed. Checked apart from capabilities |
| reading, mapping, choice | the saved answers of call 1 (the requirements and keywords), call 2 (each requirement's capabilities and conditions) and call 3 (the projects, best first) |
| requirement weight | 3 for a required item (`required_weight`), 1 for a preferred one |
| coverage, match | the share of the posting's requirement weight a page meets. The engine's match is the coverage of the page it builds from the whole library |
| reach | the coverage the whole library could reach |
| standing | how strong a project is regardless of the posting: its merit discounted by age |

## 2. Reading a posting

Three model calls read a posting. Each answer is saved, and every later page for that posting is built from the saved
answers with no model call.

### 2.1 Fetching, and facts read by rule

`reading/fetch.py` reads Greenhouse, Lever and Ashby postings through their public APIs, Amazon and Workday postings
through their JSON endpoints, and any other URL as a web page of at most 3 MB with the HTML tags removed. LinkedIn URLs
are refused, and so is any request, redirects included, to a host that resolves to a private or local address. Pasted
text is tidied as fetched text is. A posting whose URL carries a job number or a long hexadecimal id is named by it;
any other is named by its source and the first 8 hexadecimal characters of the SHA-256 of its text (for example
`paste-89f5dd84`).

`reading/facts.py` reads, with no model: salary range, years required and degree (from the lines under requirement
headings only), sponsorship, clearance, work mode, location and level. Facts a job board publishes in structured form
replace those read from the text. The dashboard shows the facts; selection does not read them.

### 2.2 Call 1: the requirements

`reading/requirements.py`. One call to Claude Sonnet 4.6 (`claude-sonnet-4-6`) at low effort.

**Sends**: the posting's first 14,000 characters as numbered lines, with a fixed line template.

The model fills one line per requirement: required or preferred, a short label, the evidence words that would show it
is met, and the number of the posting line it came from; then keywords. The code
resolves each line number to that line's text (`source`). The template asks for no number of requirements. Duties
and qualifications are both requirements.

The code drops what slips past the prompt's rule against soft items: a hiring condition (availability, start date,
relocation, work authorisation, visa sponsorship) wherever its phrase appears in a label, and a soft skill
(communication, collaboration, teamwork, culture) only when nothing else is left of the label. The dropped labels are
kept in the reading. With fewer than three readable requirements the call is made once more with a note; a second
failure stops the reading.

### 2.3 Call 2: the capabilities of each requirement

Answered by Claude Opus 5.5 by default (Accuracy). Answered by Jev where Speed is chosen (the dashboard's Matching
menu, `read --call2 jev`, or `TAILOR_CALL2=jev`), with Opus 5.5 wherever Jev cannot answer. With Opus as the
classifier (`--classifier opus`, `TAILOR_CLASSIFIER=opus`), Opus answers whatever call 2's setting says. The setting
applies to postings read from then on; a saved mapping stays until the posting is read again.

**Sends**: the role title; each requirement's label, evidence words and whether it is required; every capability in
`capabilities.toml` with its domain, and on Jev each capability's description from `capability-descriptions.toml`.
Nothing from the candidate's projects.

**On Jev** (`reading/jev_mapping.py`). Each request's state holds the role title and the requirements (labels and
words). For each requirement, a choice over the capability vocabulary in its order, each capability given as its
domain, what it covers, what it is not for and examples, plus a described "none of these"; the question asks what the
requirement asks the candidate to be able to do, since a capability that shares a word with it is not enough. The
choices go five requirements to a request (`PER_REQUEST`: one choice with every capability described is about 8,000
tokens, against Jev's 64,000-token limit a request). One more request asks, for each word of every requirement, whether
it is the name of a specific language, tool, platform, framework, standard or protocol, certification, degree or field
of study, clearance, or an amount of experience that the requirement names, alone or as one of several examples. A
posting's requests are sent at once. When "none of these" ranks first the requirement gets no capability; otherwise the
capabilities kept are the most probable option and the second and third where each has a probability of 0.3 or above
(`THRESHOLD`). Kept as conditions: the words answered yes at 0.5 or above (`CONDITION_THRESHOLD`). An answer that
leaves out a question, or gives a probability that is not a number from 0 to 1, or an option outside the list, is not
used, and a request that fails, the word request as much as any other, sends the whole posting to Opus.

**On Opus** (`reading/capability_mapping.py`). One call to Claude Opus 5.5 (`claude-opus-5-5`) at medium effort. The
capability list, one per line with its domain in brackets, is the system prompt, the same text for every posting; the
user text is the role title and each requirement's label, required flag and words. The answer gives each requirement
up to three capabilities (two or three only when the requirement names alternatives, any one of which meets it; none
for a bare condition) and its conditions. It is checked (every requirement answered once, every name in the
vocabulary, at most three) and asked for once more with the problems listed. American spellings are folded to the
vocabulary's ("modeling" to "modelling"). In the dashboard, with Opus answering, up to five postings waiting together
share one call; a posting the shared answer leaves out or answers unusably is asked for again on its own.

Either answer is saved as the same record, so pages are built from it the same way.

### 2.4 Call 3: the projects (hybrid mode only)

Answered by Jev (the default classifier), with Claude Opus 5 wherever Jev cannot answer, whichever model
answers call 2; by Opus 5 alone with Opus as the classifier.

**Sends**: the posting's title and first 7,000 characters; the list of every project with a usable bullet, in library
order, each as id, title, one-line summary, status (`facts`) and end date. No bullet text.

**On Jev** (`reading/jev_choice.py`). One request a posting, with one yes-or-no question per project: is it among the
4 to 6 projects that make the strongest case for shortlisting the candidate for this role, judged by relevance to what
the role is about and by the strength and credibility of the evidence. The six projects with the highest probability
are kept, best first.

**On Opus** (`reading/project_choice.py`). One call to Claude Opus 5 (`claude-opus-5`) at medium effort, with the
same criterion. The project list is the system prompt; the user text is the posting. The answer is 4 to 6 ids from
the list, best first, and one sentence. It is checked (ids from the list, none twice, 4 to 6) and asked for once more
with the problems listed; an answer still broken is saved as not valid and never used.

When call 3 is made:

- the command line's `read --hybrid`: only when the engine's match is under 0.60;
- the dashboard in hybrid mode, with Jev answering: for every posting read, beside call 1, whether or not the page
  ends up using it;
- the dashboard with Opus answering, or after Jev fails: only for a page whose match is under 0.60;
- `tailor_bench read --choices`: for every benchmark posting, so the benchmark can try other thresholds.

### 2.5 How the models are called

Claude models run through the Claude Code command line bundled with the Claude Agent SDK (`reading/model_call.py`).
Each call starts a clean session with no tools, no machine settings and no tool servers. The variables that would tie
the call to a parent Claude Code session, and `ANTHROPIC_API_KEY`, are passed to it as empty. Every saved answer of
calls 1, 2 and 3 records the model asked for (`model`), the model that wrote the answer (`answered_by`), and, when
they differ, a `fallback` notice with the reason.

Jev requests (`reading/jev.py`) go over HTTPS to `https://api.typesafe.ai/v1/systemone`, model `jev-1.13.0` (pinned,
since the thresholds above were tuned on it), with the key from the credential store (service `typesafe`, name `jev`)
in the request's header. The key is read for each request and never saved, logged or put in an error; a key holding a
space, a control character or a character outside ASCII is refused before any request. A request times out after
5 s. A network error, a timeout or an HTTP 5xx gets one more attempt. An HTTP 429 (too many requests) is asked again
after the wait TypeSafe names (at most 5 s), else after 1 s and then 2 s, at most twice. A missing key, a credential
store that cannot be read, another HTTP 4xx, or an answer that does not fit the questions gets no retry. At most six
Jev requests are in flight at once. When Jev cannot answer, Opus is asked (call 2 Opus 5.5, call 3 Opus 5), and the
record says so: `fallback` names `jev-1.13.0` as the model asked for, and each failed attempt's reason leads
`first_errors`. Nothing judges the quality of an answer Jev gives: a well-formed poor answer is used.

The privacy guard (`privacy.py`) reads call 2's whole request, the project list before call 3, and everything taken
from the library before each judge call. It refuses the call when the text holds any email address, "noreply" or
"github.com/", or a word whose SHA-256 fingerprint is listed in `local/identity-fingerprints.txt`, and refuses every
such call while that file is missing or empty.

### 2.6 What is saved, and when it goes stale

`reading/store.py` saves each answer per posting, keyed on the first 16 hexadecimal characters of the SHA-256 of the
posting's text, so an edited posting is a new posting. Files are written beside their target and renamed over it, so
an interrupted write leaves no half file.

| record | file | made again when |
|---|---|---|
| posting | `data/postings/<id>.json` | the posting is read again |
| reading (call 1) | `data/readings/<id>-<key>.json` | only on `read --refresh`. A model reads a posting differently each time, so every page is built from one saved reading |
| mapping (call 2) | `data/mappings/<key>.json` | the capability vocabulary changes (a hash of its capability and domain pairs), the reading is made again, or the saved answer failed its check; a mapping Jev made also when the capability descriptions change (a hash of what Jev was sent). `page` and the benchmark refuse to build from a stale or failed mapping |
| choice (call 3) | `data/choices/<key>.json` | a listed project's id, title, summary, facts or end changes, a project joins or leaves the list, or the saved answer failed its check. Without a current choice, `page --hybrid` builds the engine's page and says so |

## 3. Capabilities and the demand

`selection/capability_matching.py`.

**A bullet's capabilities** are authored on the bullet in `projects.toml`, strongest first, with the conditions it
establishes. Inside the score, a bullet's tags are its capabilities' domain tags: the first capability carries 0.6 of
the weight (`main_capability_share`) and the others share the rest; a single capability carries all of it. A bullet
with no capability (a competition placing, as an example) takes the average tags of its project's bullets that have
one, so it is relevant exactly when its project is.

**The demand** is the posting's mix of domain tags, from its own mapped requirements: each requirement's weight goes
to the domains of its capabilities, shared equally when its alternatives span several domains, and the mix is scaled
to sum to 1. It is the `demand` field of the page record. Editing the library cannot change it.

**Tag distance** (`selection/tags.py`) is 0 for the same tag, 1 for two tags sharing the part before the dot (for
example `sec.appsec` and `sec.offensive`) or listed as adjacent (`[[domain_adjacency]]` in `capabilities.toml`), and 2 otherwise. The
posting's *core* is its fewest heaviest tags carrying 60% of the demand (`core_share`). A bullet's *drag* is the mean
distance of its tags to the nearest core tag, halved, so 0 is on topic and 1 unrelated. Its *relevance* is 1 minus
drag.

**The word-based demand** (`selection/word_matching.py`) is a second mix, over the topic tags of `vocabulary.toml`.
Each requirement word and keyword is looked up among the library's evidence words (on usable bullets, and the names
of skills, skill-row titles, courses, and each degree's evidence words): an exact match, else the first library word
containing it, else the first library phrase of two or more words found inside it. A word found takes the topic tags
of the entries that list it. Each requirement's weight (3 required, 1 preferred) is shared among its words that were
found, and each keyword weighs 0.5 (`keyword_weight`); a requirement or keyword with no word found adds its weight to
the page's `unmet` share instead. The mix is rounded to steps of 0.05 (`demand_step`), and shares under 0.025
(`demand_minimum`) are dropped. It titles the skill rows, orders the skills that fill a short row and orders courses
of equal fit (section 8). It never meets a requirement.

## 4. Requirement credit and refusal

`selection/capability_matching.py`. Each requirement, joined with its mapping, earns a credit from 0 to 1:

| the page shows | credit |
|---|---|
| a chosen bullet demonstrating one of the requirement's capabilities | 1 |
| otherwise, a chosen bullet demonstrating another capability in the same domain as one of them | 0.5 (`transferable`) |
| neither | 0 |
| any of the above, when the requirement names conditions and none is visible on the page | the credit times 0.5 (`missing_condition`) |
| a requirement with no capability, only conditions | 1 when one of its conditions or evidence words is visible, else 0 |

*Visible* means found as a whole word in the page's text outside the projects (degrees, their other names and evidence
words, fixed lines, fixed skill rows), in a chosen bullet's text, or among the conditions a chosen bullet establishes
in the library; or equal to the whole name of a skill in a printed skill row ("Python" is met by the skill Python; a
required "LLM" is not met by a skill named LLM Red Teaming). A bullet's evidence words never meet a requirement.
Courses are printed but meet no condition. Whole-word matching guards short tokens: `C` does not match `C#`.

```
coverage = sum over requirements of (weight x credit) / sum of weights        weight 3 required, 1 preferred
```

The page records each requirement's credit and the bullets that earn it (`requirement_credit`), the required items not
fully met (`missing_required`), and the requirements the mapping gave no capability (`unmapped`), which only their
words can meet.

**Refusal** (`selection/page.py`). Before anything is chosen, the whole library is matched against the posting: every
usable bullet, the text always on the page, and the skill rows chosen for the posting. If that reach is under 0.25
(`reach_floor`), no page is built. A page on which no project clears the weak-project rule (section 6) is refused the
same way.

## 5. Standing and the score

`selection/project_values.py` and `selection/scoring.py`.

```
merit     = 0.6 x complexity + 0.4 x brand                       merit_complexity
recency   = max(0.60, 1 - 0.07 x years since the project ended)  recency_floor, recency_loss_per_year; 1 while running
standing  = merit x recency
fill rank = complexity x recency
```

Years are counted in days from the date the engine runs, so standing moves as time passes.

**The family boost.** On each posting, each project's standing and fill rank are multiplied by `1 + 0.1 x overlap`
(`family_boost`). Overlap, from 0 to 1, is the share of the capability entries on the project's usable bullets that
fall in the families the posting asks for, each family weighted by its share of the demand. The selector chooses with
the boosted values; the score recorded on the finished page and the order on the page use plain standing.

```
score = 0.60 x coverage + 0.40 x emphasis + 0.70 x standing - 0.30 x incoherence - 0.35 x cost + 0.15 x depth
```

| term | definition |
|---|---|
| coverage | section 4 |
| emphasis | 1 minus half the summed absolute difference between the page's tag mix, each bullet weighted by its length, and the demand |
| standing | the mean standing of the page's projects, each weighted by its number of bullets on the page |
| incoherence | the mean over projects of 1 minus the largest single tag's share of that project's chosen bullets |
| cost | `min(1, 0.4 x liability + 0.2 x redundancy)`. Liability: the length-weighted mean over bullets of drag x (1 minus the project's standing, floored at 0) x (1 plus the bullet's own `liability` from the library), so off-topic text costs less on a strong project. Redundancy: the mean Jaccard overlap (shared items divided by all items) of evidence words over every pair of chosen bullets |
| depth | the mean over projects of the depth curve at the project's bullet count: 1 bullet 0.15, 2 0.45, 3 0.75, 4 0.92, 5 or more 1.00 |

## 6. The selector

`selection/selector.py`.

**What a page may be.** At most 6 projects, whose titles and bullets fit in 28 lines, and never both bullets of an
`excludes` pair. A bullet takes `ceil(characters / 126)` lines and a project title `ceil(characters / 122)`
(`layout.py`).

**The weak-project rule.** A project whose boosted standing is under 0.35 (`open_floor`) may open on the page only with
a bullet that itself demonstrates a capability of a required item that no other project on the page demonstrates.
Only the opening bullet is judged, so a project cannot open on a sibling bullet's evidence.

**The four passes.** A *move* is one bullet: another bullet for a project already on the page, or the bullet that
opens a new one. Every pass keeps the page within the limits above, and every accepted move is recorded in the page's
`trace` with the score after it.

1. **Add.** Repeatedly take the move with the largest score gain per line it takes (a new project's title line
   included), until no move gains.
2. **Swap.** Exchange one chosen bullet for one left out while that raises the score, taking the first improvement in
   id order, at most 200 swaps (`MAX_SWAPS`).
3. **Fill.** Spend the lines left without consulting the score, skipping any move that lowers coverage. Moves are
   ranked: those that keep the page's emphasis at or above 0.60 (`emphasis_floor`) first; then those with relevance
   at or above 0.30 (`relevance_floor`), most relevant first; then by the project's fill rank, then relevance, then
   bullet id. A move's relevance is scaled by what one more bullet adds to its project on the depth curve, divided by
   the curve's largest step, and a move that adds nothing there is skipped.
4. **Prune.** Drop the weakest project under 0.35 whose chosen bullets no longer meet a required item that no other
   project on the page meets, fill again (`refill` in the trace), and look again, at most once per slot.

**Order on the page** (`page_order`). Projects still running come first, then the rest latest-ending first. Projects
that tie on date (all running ones do, since the library has no start dates) are ordered by `0.6 x standing + 0.4 x
emphasis` of their own bullets (`order_standing`). Bullets keep id order within a project.

## 7. Hybrid mode and the 0.60 threshold

`build_hybrid_page` in `selection/page.py`. The engine builds its page from the whole library first. When that page's
coverage is under 0.60 (`model_projects_below`) and a current, valid choice is saved, the same selector builds the
page again from the chosen projects' bullets only, under the same line budget and weak-project rule, so a chosen
project may be left off. Reach, the refusal, the skill rows and the courses stay the whole library's. When the chosen
projects leave nothing to place, the engine's page stands.

The page records `projects_by` (`engine` or `model`), the choice with the chosen projects the page holds
(`choice.placed`), and, when the model's projects are used, the engine's own match (`engine_match`).

The threshold was compared on 31 real postings and one candidate's library, with call 3 then answered by Claude Opus
4.8: where the engine's match was under 0.60, blind judges preferred the pages built from call 3's projects, 7 to 1
(Claude Opus 4.8) and 4 to 0 (Claude Sonnet 5, with 4 split); at 0.60 and above, neither side led.

## 8. Skill rows and courses

`selection/skills_and_courses.py`. Each skill and course carries the capabilities it shows
(`skill-capabilities.toml`). Against each mapped requirement, a skill fits in full when the requirement names it whole
(as a condition or one of its evidence words: "Python", "Burp Suite") or asks for a capability it shows, and in part,
0.5 (`skill_transferable`), when it asks for another capability in the same domain. A skill with no entry fits only
by its name.

**Rows.** Five rows are taken one at a time from the rows that are not fixed. Each time, the row taken is the one whose
skills answer in full the most requirement weight the rows already taken do not, so rows cover different requirements
and synonyms count once. Partial fits never pick a row. Ties go to the higher `prior`, then to the row earlier in the
file, and the prior alone picks rows when nothing is answered. A row lists its skills that answer in full, best first,
at most 8; a row with fewer than 4 of those (`MIN_ROW_SKILLS`) is filled to 4 with partial fits, then by topic fit
with the word-based demand. A skill in two rows is printed once, in the row taken first. Each row wears its candidate
title that best fits the word-based demand, the earlier title on a tie. Rows are printed in order of the requirement
weight each newly answered, with the row whose id is `tools` last. Fixed rows are printed by the template as written.

**Courses.** Up to 11 a degree, ranked by their weighted fit with the requirements (a course also fits in full when
its name states a condition), then by topic fit with the word-based demand, then by name. For that topic fit, a
demanded topic the degree has no course for is moved to the nearest topics the degree does cover, shared by how much
of the degree they make up; topic-tag adjacency (`[[adjacency]]` in `vocabulary.toml`) sets which topics are near.

The page records the skills that answer a requirement in full, with their share of the requirement weight
(`skill_scores`).

## 9. The judges

Two judges exist, both measurement and advice, neither part of choosing: `tailor_bench judge` (`tailor_bench/judge.py`)
scores one page (does each project belong, is each bullet relevant, which projects are missing), and the dashboard's
Ask the judge (`tailor_dashboard/review.py`) gives a verdict, gaps, better-fitting roles and proposed page edits. An
edit only swaps bullets that are in the library; code checks it against the page's limits and applies it only when
you accept it. Both ask Claude Opus 5.5 and read the posting's first 9,000 characters, the degrees, every project with
a written bullet and the page (`rendering/judge_input.py`).

## 10. Every weight

All in `selection/weights.py`, one frozen `Weights` object. `python -m tailor_bench benchmark --weights NAME=VALUE ...`
runs the benchmark with any of them changed. Every weight must be a finite number of 0 or more; the shares and credits
(`merit_complexity`, `order_standing`, `main_capability_share`, `core_share`, `transferable`, `missing_condition`,
`skill_transferable`) at most 1; `demand_step` above 0; `depth_curve` is written as comma-separated numbers, at least
two.

The weights were set on one candidate's library and a private benchmark of 25 postings. Their origins:

- **fitted**: chosen by a search against labelled pages on half the benchmark, checked on the other half;
- **compared**: chosen from values run side by side on the benchmark;
- **judgement**: the library author's stated view, written as numbers;
- **by hand**: chosen when the mechanism was built.

| weight | default | acts on | origin |
|---|---|---|---|
| `coverage` | 0.60 | score | by hand, the scale the others were fitted against |
| `emphasis` | 0.40 | score | fitted, between 0.20 and 0.40 |
| `standing` | 0.70 | score | fitted, over 0.35 to 1.40 |
| `incoherence` | 0.3 | score | by hand; setting it to 0 changed pages |
| `cost` | 0.35 | score | by hand; setting it to 0 changed pages a little |
| `depth` | 0.15 | score | fitted, over 0 to 0.50 |
| `depth_curve` | 0, 0.15, 0.45, 0.75, 0.92, 1.00 | depth, fill | judgement |
| `core_share` | 0.60 | the posting's core tags | by hand |
| `cost_liability`, `cost_redundancy` | 0.4, 0.2 | cost | by hand, reason not recorded |
| `merit_complexity` | 0.6 (brand 0.4) | standing | by hand: complexity is the lasting signal to a technical reader, brand helps past a non-technical screen |
| `recency_floor`, `recency_loss_per_year` | 0.60, 0.07 | standing, fill rank | judgement |
| `open_floor` | 0.35 | weak-project rule, prune | by hand; pages were the same anywhere from 0 to 0.35 |
| `reach_floor` | 0.25 | refusal | compared, with an earlier word-matching method: it refused the 5 non-engineering postings of 30 and built every engineering one |
| `model_projects_below` | 0.60 | hybrid mode | compared on 31 postings (section 7) |
| `relevance_floor` | 0.30 | fill | by hand; pages were the same anywhere from 0 to 0.30 |
| `emphasis_floor` | 0.60 | fill | by hand; a preference, since as a hard limit it left pages 11 lines short |
| `order_standing` | 0.6 (emphasis 0.4) | order on the page | judgement |
| `transferable` | 0.5 | requirement credit | by hand; 0.25 and 0.35 lost to it when judged blind |
| `skill_transferable` | 0.5 | skill and course fit | by hand |
| `missing_condition` | 0.5 | requirement credit | by hand |
| `family_boost` | 0.1 | standing while choosing | compared: agreement on the target-role postings rose from 0.724 to 0.752; 0.2, 0.3, 0.5 and fixed additions did no better |
| `main_capability_share` | 0.6 | a bullet's tags | by hand |
| `required_weight` | 3.0 | every weighted share | by hand |
| `keyword_weight` | 0.5 | word-based demand | by hand |
| `demand_step`, `demand_minimum` | 0.05, 0.025 | word-based demand | by hand |

An ablation on that benchmark (each weight set to 0 and doubled) found two that changed no page for that library:
`recency_floor`, which only one project was old enough to reach and that project was never placed, and
`demand_minimum`.

Fixed by the page and not in `Weights` (`layout.py`, `rendering/word.py`): 56 lines, of which the header takes 3,
education 13, skills 11 and projects 29 (a heading and 28 lines); 126 characters a line for a bullet and 122 for a
project title or skill row; 6 project slots; exactly 5 chosen skill rows of 1 to 8 skills; exactly 2 degrees with 8
to 11 courses each. Other constants: call 3 on Jev keeps 6 projects; call 2 on Jev asks five requirements a request,
its second and third capabilities need 0.3 and its conditions 0.5; the swap pass stops after 200 swaps; a short skill
row is filled to 4.

## 11. Determinism

- Given the same posting text, library, saved reading, mapping and choice, weights and date, the engine builds the
  same page. No step draws a random number, ties between equal moves resolve by id, and sums run in a fixed order.
- The date enters only through recency (section 5). The tests pin one.
- Model answers are not repeatable, which is why each is saved once. Two readings of one posting by call 1 differ on
  about 8% of requirements; call 2 on Opus gave the same capability on 0.93 of requirements from one run to the next;
  two runs of call 3 on Opus over 31 postings chose the same set of projects on 16, with a mean overlap of 0.859.
- The Word writer (`rendering/word.py`) gives byte-identical files for the same page. Every package part other than
  the document is copied from the template byte for byte, and each new bullet paragraph gets an id derived from a hash
  of its slot and position. Before the file replaces its target, the result is read back: the other parts must equal
  the template's in content and order; the document with the slots and new bullets taken out must equal the template
  with the slots taken out, compared as canonical XML; and the slots and bullets must stand in the template's slot
  order, each once. Any difference, or a template whose SHA-256 differs from the pinned one, leaves no file.
