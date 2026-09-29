# From nothing to your own pages

This guide takes you from an empty `library/` to pages built from your own work, then to a small benchmark for
checking that the engine chooses well for you. The terms (bullet, capability, call 1, 2 and 3, match, hybrid mode) are
defined in the [README](README.md), and every field named here is listed in [docs/LIBRARY.md](docs/LIBRARY.md).

The library takes most of the effort. Nothing reads your resumes for you, and the engine can only choose what you have
written down.

## 1. Set up

Install Kingsman, run the example library as the README's quick start shows (the development server, then a page and
its Word file from the saved answers), sign in to Claude Code and add a privacy guard word, so you have seen a page
and its record before you write your own. Then start from a copy of the example:

```sh
cp -r examples/library library        # PowerShell: Copy-Item -Recurse examples/library library
```

Once `library/` exists it is the default library, and with `TAILOR_DATA` unset the commands and the dashboard keep
postings and their saved answers in `data/`. Replace the content of `projects.toml`, `skills.toml`, `education.toml`
and `skill-capabilities.toml`, and the fixed text of the template (section 12). Keep `vocabulary.toml`,
`capabilities.toml` and `capability-descriptions.toml` unless your field needs capabilities they lack (section 7).
`library/`, `data/`, `local/` and `benchmark/` hold your own material; `.gitignore` keeps them out of version
control, and they stay out of any copy you share.

## 2. Collect everything before writing anything

Gather every source first: every version of your resume, your repositories, course lists, transcripts, competition
results, internship letters and reports. List every project named in any source, then decide which ones matter. Advice
drawn from an incomplete library ("nothing backs this skill") is wrong when the backing project was never entered.

| source | how to read it | what goes wrong |
|---|---|---|
| PDF resume | text extraction | columns merge, bullets lose their line breaks, ligatures break words; compare with the rendered page |
| Word resume | the document XML, or a library such as `python-docx` | text boxes and tables are skipped by simple readers |
| notes | read directly | facts mixed with plans and opinions |
| repositories | README, commit history, merged pull requests | does not show who did the work |
| transcripts, letters, results | read directly | dates and titles differ between documents |

Reconcile the list: each project named anywhere ends up in the library, merged into another with a note, or left out
with a reason. Keep that inventory outside `library/`. A name missing from it with no reason is a lost project.

Keep weak and old projects. The engine decides relevance per posting, and a project useless for one role may be the
only fit for another. The weak-project rule already keeps a weak project off the page unless it meets a required item
nothing else meets.

## 3. Projects and bullets

- **One project per piece of work.** Two courses that produced one system are one project; one internship that
  produced two unrelated systems is two.
- **One bullet per claim a reader could check.** "Built X" and "X reached 200 users" are two bullets if either could
  stand alone.
- **Split a bullet that makes several claims.** As an example, one bullet claimed a threat model, a response plan and a
  costed priority order in one sentence, so a posting asking for any one of them placed all three claims; it became
  three bullets. A bullet that needs three capabilities in its map (section 6) is the signal.
- **Write other wordings of the same fact as separate bullets that exclude each other** (`excludes`), so the engine
  picks the wording that suits the posting and never prints both. The example library's I1.4 and I1.5 are such a pair.
- **State who did it.** Where the work was a team's, the bullet says what you did. A number that belongs to a tool or
  a community never goes behind your verb.
- **Describe early work as what it was.** Reading about a topic is not a project; an interviewer will ask what you
  ran, on what, and what happened.
- **Leave out bullets about someone else's work or about housekeeping.** They reach pages they answer nothing on.

Each project also has a one-line `summary` (what it is) and a one-line `facts` (dates, and whether it is built,
deployed, designed or in progress). Call 3 reads both, with the title and end date, and sends them to a model provider,
so write facts in them, never judgement, and no word from section 11.

## 4. Writing bullets

A bullet passes two readers in turn. A screener, or an applicant tracking system, reads the first clause of each
bullet and the skills lines, and decides whether to pass the page on. A hiring manager or engineer reads the specifics
and checks whether they hold. Write for both:

| simplify | keep |
|---|---|
| sentence structure: one clause, then the next | every tool, number, artifact and result |
| word choice, where a plain word means the same | the technical term an applicant tracking system matches literally |
| jargon the posting does not use | jargon the posting does use |

- **Say what the work achieved, then how**, in terms a technical reader can check. The first clause is the one every
  reader sees, so it is plain; the mechanism after it is what makes the result believable.
- **Put a measured outcome on every bullet that has one.** Every number comes from a real measurement you can explain,
  and `source` says where it is recorded.
- **State the action, the artifact and the result, and stop.** A clause that argues for a choice ("so that ...",
  "rather than ...") comes out.
- **Use the strongest verb the evidence supports, and no stronger.**

  | evidence | verb |
  |---|---|
  | shipped, merged, ran against something real | built, shipped, ran, validated |
  | designed and specified, not deployed | designed, specified, architected |
  | investigated and concluded | showed, found, measured |
  | contributed to work someone else led | contributed, supported |

- **Where strong becomes false:**

  | allowed | not allowed |
  |---|---|
  | naming what a design achieves | claiming it was deployed when it was not |
  | saying what a result enables | inventing the result |
  | saying a problem was hard, and why | inventing difficulty |
  | stating a number from the record | estimating a number never measured |
  | drawing out an implication a reader would miss | implying scale, users or impact that did not exist |

  The test for any phrase: asked about it in an interview, does the honest answer match what the page implied? If the
  honest answer would deflate it, the phrase comes out.
- **Words that read as filler to a technical reader:** "responsible for", "helped", "assisted", "contributed to" where
  the work was your own, "leveraged", "passionate", "cutting-edge", "spearheaded", "synergy".
- **Name every language and tool you used**, in the text or in the bullet's `conditions`. A requirement that names a
  language gets full credit only when the page shows that language, so a language left unnamed on strong work either
  halves that credit or pulls in a weaker project that does name it.
- **Length.** A bullet of up to 126 characters takes one of the page's 28 project lines; up to 252 takes two.

**Evidence words** (`evidence`) are the words a posting might use for what the bullet shows. They never meet a
requirement. They set the posting's word-based topic mix, which titles the skill rows and orders courses, and they
measure how much two chosen bullets repeat each other. Name the field as well as the mechanism ("AI security" beside
"least privilege"), give both singular and plural of key nouns ("evaluation", "evaluations"), and prefer the long form
of a one- or two-letter token.

## 5. Dates

`ends` is the month the work ended, or `present` while it runs. A course project ends when its semester ends. A
project's age lowers its standing by 0.07 a year, down to 0.60 of its merit, counted from the day the engine runs.

## 6. The capability map

Requirements are met through capabilities, so the map on each bullet decides what the page can meet.

- **On each bullet**, list the capabilities it demonstrates, strongest first, each with a `why` that names its object:
  "authorization and least privilege", because of "an ownership check on the payout endpoints". The first carries
  most of the bullet's weight. A bullet that shows nothing a posting could ask for, such as a competition placing,
  lists none; its project's standing carries it.
- **Conditions apart.** Languages, tools, platforms and degrees go in `conditions`, never among the capabilities.
- **Have someone other than the bullets' author check the map.**
- **The gap list.** Once postings are read, the dashboard's Profile page, under Coverage, lists the capabilities your
  read postings ask for that no bullet shows. Each is either real work never written down (add a bullet, with its
  source) or a true gap (leave it; the engine counts it as unmet).

## 7. The capability vocabulary

The shipped `capabilities.toml` holds 76 capabilities in 17 domains, drafted around security and AI roles. For another
field, draft additions from two sides: your own projects, and the requirements of 20 to 30 real postings in the roles
you will apply to, so capabilities you lack are named too. A capability is specific enough to mean one thing. Each
domain belongs to a family; a domain that is genuinely both, such as AI security, lists two. Renaming a capability or
moving it to another domain makes every saved mapping stale, and the next `read` of each posting makes call 2 again.

**Check the granularity** once 20 or more postings are read. List the requirements that landed on each capability:

```sh
python -c "
import collections, glob, json
asked_for = collections.defaultdict(list)
for path in glob.glob('data/mappings/*.json'):
    for requirement in json.load(open(path, encoding='utf-8'))['requirements']:
        for capability in requirement.get('capabilities') or []:
            asked_for[capability].append(requirement['name'])
for capability, names in sorted(asked_for.items(), key=lambda item: -len(item[1])):
    print(f'{len(names):3d}  {capability}: {\"; \".join(names)}')
"
```

A capability that collects several different asks and is met by one of your bullets gives credit the evidence does not
support, and hides real gaps. As an example, one broad penetration-testing capability collected web, API and
business-logic testing, and a single exploit bullet met all of them; it was split into narrower capabilities. A broad
capability that no bullet shows does no harm: it is one gap.

### The capability descriptions

`capability-descriptions.toml` is what Jev is told about each capability when it answers call 2 on Speed. Claude
Opus, on Accuracy, is sent only the capability names and domains, and never reads this file. It holds one table per
capability, keyed by the capability's name, and one for "none of these", the option for a requirement that names only
a condition (a language, a degree, years of experience) or asks for nothing in the list:

```toml
["exploit development"]
domain = "Offensive security"
what = "turning vulnerabilities into working exploits: memory corruption, ROP, mitigation bypass, proof-of-concept exploits, binary exploitation"
not_for = "vulnerability research (finding the bug); red team operations (running an engagement against an organisation)"
examples = [
  "Write working exploits for memory corruption bugs",
  "Bypassing ASLR and stack canaries",
  "Binary exploitation (pwn) skills",
]
```

- `domain` must be the domain `capabilities.toml` puts the capability in; `what` says what the capability covers;
  `not_for` names its nearest neighbours and where their asks belong; `examples` are written as a posting would word
  the ask. The library is refused unless every capability has exactly one table here, under its own domain.
- Add, rename or move a capability in `capabilities.toml` and do the same here in the same edit.
- Write the descriptions from the capability names and general knowledge of postings, never from your projects: every
  table is sent to TypeSafe with every posting Jev maps. The file's `about` note is not sent.
- Editing a table makes every saved Jev mapping stale, so each posting mapped on Speed is mapped again when it is next
  read. Mappings Opus made are unaffected.
- The shipped descriptions were drafted for the shipped vocabulary; the "not for" lines are what keeps Jev from
  choosing a neighbour that shares a word with the requirement, so read them after any change to the vocabulary.

## 8. Tags, skills and courses

- **Topic tags** come only from `vocabulary.toml`, and a bullet's weights sum to 1. The tree carries meaning: security
  *of* AI is `ai.security`, under AI.
- **Skills.** Back every skill with at least one bullet; a skill no project demonstrates is what an interviewer probes,
  and the engine does not check it. Write at least five rows that are not fixed, each with two or three candidate
  titles. A skill may sit in two rows on purpose: the row printed decides how it is framed.
- **Courses** come from the transcript, technical subjects only, at least 8 per degree.
- **Skill and course capabilities.** Give every skill in a competing row and every course an entry in
  `skill-capabilities.toml`; loading the library warns about each one missing. A plain language or tool that postings
  only ask for by name gets an empty list.
- **Priors** (`prior` on a row, 0.5 when absent): a row with a higher prior wins a tie, and priors alone pick the rows
  when a posting points at none.

## 9. Complexity and brand

Complexity and brand set a project's **standing**, how strong it is regardless of the posting:

```
standing = (0.6 x complexity + 0.4 x brand) x recency
```

Standing orders the page, lets strong work win a slot when it is off topic, and keeps weak projects (under 0.35) off
the page unless they meet a required item nothing else meets.

**Do not grade your own work, and do not let whoever wrote the bullets grade it.** Makers misjudge in both directions:
on the library the engine was developed on, independent graders raised one project from 0.60 to 0.73 and lowered
another from 0.80 to 0.47. Use two graders from different model families, blind to any existing numbers, given only
each project's description and facts. Take the mean; if they differ by more than one band, find out why before
accepting either. Give graders facts ("sandbox built and run on 60 samples from 4 families; kernel telemetry driver
designed, not built"), never self-assessment. No grading tool ships with v1, and grading sends your project
descriptions to those providers, so check them against section 11 first.

**Complexity**, judged by today's standard; how hard the work was for you at the time does not enter:

| score | meaning |
|---|---|
| 0.9 to 1.0 | hard, original work on a system others depend on, and it exists: merged upstream, deployed at scale, or published in a strong venue |
| 0.7 to 0.8 | substantial own engineering with a working, non-trivial result; or a detailed original design of a hard system with its mechanisms specified |
| 0.5 to 0.6 | a solid project with some own design, using standard methods, that works |
| 0.3 to 0.4 | coursework or tutorial grade, conventional methods, or preliminary reading or prototyping |
| 0.1 to 0.2 | early learning; simple by today's standard |

Unbuilt work (a design, a plan, an early implementation) is capped at 0.7. Scale alone is not difficulty. A team
project is graded on what your own bullets say you did.

**Brand**, how much the name behind the work tells a reader: 0.9 a globally recognised sponsor, employer or venue; 0.7 a
well-known institution, government body or named company engagement; 0.5 a course at a top university or a recognised
competition placing; 0.3 an independent project with some outside context; 0.1 to 0.2 purely personal.

## 10. Held, fragile, pending, excludes, liability

| field | use it when | effect |
|---|---|---|
| `held = true` on a project | the project should not be used for now | its bullets are off every page and it leaves call 3's list |
| `fragile = true` on a bullet | a claim you cannot yet defend in an interview | the bullet is off every page |
| `pending = true` on a bullet | the bullet is not written yet, or waits on a measurement | needs no text; never placed or sent |
| `excludes = [...]` on a bullet | two wordings of one fact | never both on a page |
| `liability`, for example `0.2`, on a bullet | a true bullet with a known weakness | costs more when it is off topic |

Held projects and fragile bullets are still sent to the judges (section 11), so neither is a way to keep text private.

## 11. Keep identity out of the library

The library is sent to model providers in normal use. Call 3 sends each usable project's id, title, summary, facts and
end date, to TypeSafe or Anthropic. The judges send every project with a written bullet, its bullets, facts, end date
and up to three notes, to Anthropic. Call 2 sends no project text: the capability names and domains, and on Speed the
capability descriptions. Keep handles, account names, repository and publishing plans and commit identities in a
separate private file, never in `library/`.

The privacy guard (`src/tailor_engine/privacy.py`) stands in front of calls 2 and 3 and every judge call. It refuses
text containing any email address, "noreply" or "github.com/", or any word whose fingerprint is listed in
`local/identity-fingerprints.txt`. Add each word with `python -m tailor_engine guard`, which asks for it without echo.
Without at least one fingerprint, calls 2 and 3 and the judges are refused, and the refusal ends with the command
that adds a word.

- `guard` takes one word of letters and digits, and the check compares whole words, case ignored. A handle with
  punctuation is split at it (`rk-sec` becomes `rk` and `sec`), so guard its most distinctive part.
- The fingerprints are unsalted SHA-256, so anyone holding the file can confirm a guessed word.
- Notes marked `private`, and notes containing word parts such as "publish", "repositor", "handle" or "identity" (the
  full list is in docs/LIBRARY.md), are left out of the judges' text. That filter is a backstop; do not write such
  notes at all.

## 12. The template

The example template holds the fictional candidate's header, degrees, fixed line and certifications as fixed text.
Edit those in your copy, `library/resume-template.docx`; keep the degree lines and certifications line in step with
`education.toml` and the fixed `certs` row; then write the template's new SHA-256 into
`library/resume-template.sha256`, which pins your template in place of the example's. The steps, and what the writer
checks, are in docs/LIBRARY.md under resume-template.docx.

## 13. Check the library

| check | how | automatic |
|---|---|---|
| tags and capabilities in their vocabularies, bullet weights summing to 1, ids unique, dates well formed, no unknown fields | `Library.load`, which refuses the library and names each problem | yes |
| `excludes` pointing at written bullets; every skill and course in `skill-capabilities.toml` | warnings from `Library.load`, printed by `page` and the benchmark | yes, as warnings |
| a page builds and writes for a real posting | `read`, `page` and `word` on one posting | yes |
| every source project accounted for | the inventory of section 2 | by hand |
| each bullet true and sourced | `source` on each bullet, read by you | by hand |
| each bullet one claim | three or more capabilities in the map flags it | by eye |
| no identity words or publishing notes in the library | section 11; the guard stops a call, nothing stops the writing | by hand |
| pages sensible on real postings | section 14 | partly |

## 14. Calibrate on real postings

The weights were set on one library. Before trusting the defaults for yours, look at pages for postings you would
really apply to.

### 14.1 Read and look

Read 10 to 20 postings, in the dashboard or with `read --hybrid`. For each page look at:

- `missing_required` and the requirement credits: is each missing item a real gap, a bullet never written, or a
  capability missing from a bullet's map?
- `unmapped`: requirements call 2 gave no capability, which only their words can meet;
- which projects appear, and which never do;
- on hybrid pages, `engine_match` and which of call 3's projects were `placed`.

Fix the library first. Most wrong pages trace to the library: a missing project, a bullet making three claims, a
capability left off a map.

### 14.2 Build a benchmark

A benchmark is a set of postings, each with pages chosen by people, that the engine's pages are compared against.

1. **Choose 10 to 20 postings you would apply to**, and add a few from neighbouring roles and one or two
   non-technical postings as controls. Give each a band: `lane` (the roles you target), `adjacent`, `distant` or
   `control`.
2. **Have two or three people choose each page independently.** One person's picks carry that person's bias, and the
   labellers' agreement with each other is the figure the engine's agreement is read against. Give each labeller the
   library with `complexity` and `brand` removed, the posting, and the page rules: at most six projects; titles and
   bullets within 28 lines, a bullet taking one line per 126 characters and a title one per 122; never both bullets
   of an `excludes` pair; only usable bullets; answers as ids only. Show no labeller the engine's page or another
   labeller's.
3. **Lay out the files.** The bench tools read `benchmark/` under the repository root, or the folder `TAILOR_BENCHMARK`
   names; `TAILOR_DATA` does not move it. `examples/benchmark/` is a complete, illustrative one for the example
   library (section 14.3). `src/tailor_bench/dataset.py` reads exactly this:

   ```text
   benchmark/
     index.json                        [{"id": "<posting id>", "band": "lane: application security"}, ...]
     postings/<id>.json                each posting record, as read saves it
     labels/<id>.labeller-1.json       the first labeller's page for the posting
     labels/<id>.labeller-2.json       the second labeller's page
     readings/<id>-<key>.json          call 1's saved answers        made by tailor_bench read
     mappings/<key>.json               call 2's saved answers        made by tailor_bench read
     choices/<key>.json                call 3's saved answers        made by tailor_bench read --choices
     runs/                             written by the bench tools
   ```

   - `index.json` is a JSON list with one object per posting. The tools read `id`, the posting's file name without
     `.json`, and `band`, whose text before the first `:` must be `lane`, `adjacent`, `distant` or `control`; any text
     after it, and any other key (`title`, `url`), is for people.
   - The easiest way to get the posting records is to read each posting with the benchmark as the data folder:
     `TAILOR_DATA=benchmark python -m tailor_engine read URL` saves the posting, its reading and its mapping straight
     into `benchmark/`. Pasted postings get ids of the form `paste-<8 hexadecimal characters>`.
   - A label file holds `{"page": [{"project": "G1", "bullets": ["G1.1", "G1.3"]}, ...]}`, in the library's ids;
     other keys are ignored. A file without `page`, or a project without `bullets`, stops the run.
   - The label files' names come from `ADVISORS` in `src/tailor_bench/dataset.py`: `labeller-1` and `labeller-2`.
     Name your labellers' files with them.
     For a third labeller, add a third name to `ADVISORS`; agreement is then averaged over all three, and every posting
     that gets a page needs a file for each name.
4. **Make the model answers once:** `python -m tailor_bench read --choices`. It makes calls 1 and 2 for every posting
   whose answers are missing or stale, and call 3 for every posting whatever its match, so hybrid runs work at any
   threshold. Call 2 goes to the model `TAILOR_CALL2` names (Opus unless set) and call 3 to Jev, as in `read`; pass
   `--classifier opus` to leave Jev out. Each call's data leaves the machine as the README's privacy table describes.
5. **Measure the labellers against each other** before the engine. The bench tools do not report it; this prints each
   posting's overlap of the first two labellers' projects:

   ```sh
   python -c "
   from tailor_bench import dataset
   from tailor_bench.agreement import jaccard
   for entry in dataset.index():
       first, second = (dict(dataset.advisor_page(entry['id'], name)) for name in dataset.ADVISORS[:2])
       print(entry['id'], round(jaccard(set(first), set(second)), 3))
   "
   ```

### 14.3 Run it

Try the tools on the example benchmark first. It holds the eight example postings with their saved answers, and two
label files per posting, written by hand for the fictional candidate to show the format; its figures describe those
illustrative labels, not the engine:

```sh
TAILOR_BENCHMARK=examples/benchmark python -m tailor_bench benchmark
# PowerShell: $env:TAILOR_BENCHMARK = "examples/benchmark"; python -m tailor_bench benchmark
```

```text
            projects  bullets  pages  refused
all            0.589    0.613      8        0
lane           0.933    0.615      3        0
adjacent       0.479    0.683      3        0
distant        0.310    0.525      1        0
control        0.167    0.167      1        0
```

Then on your own:

```sh
python -m tailor_bench benchmark              # the engine's pages
python -m tailor_bench benchmark --hybrid     # hybrid pages, from the saved call 3 answers
```

Each prints the project and bullet agreement for all postings and for each band, with the pages built and refused,
and writes its record to `benchmark/runs/`: `benchmark-capabilities.json`, `benchmark-capabilities-hybrid.json`, or
`benchmark-<label>.json` with `--label`. The hybrid run also prints how many pages call 3's projects made. No model is
called. A posting with no saved reading, or no current mapping or choice, stops the run and names the command that
makes it.

Agreement, per posting and labeller, is the Jaccard overlap (items in both divided by items in either) of the page's
projects with the labeller's, and of the bullets within each project both chose; the figures are the means over all
labellers and postings.

### 14.4 Try weights

```sh
python -m tailor_bench benchmark --weights family_boost=0 --label no-family-boost
python -m tailor_bench benchmark --hybrid --weights depth_curve=0,0.2,0.5,0.8,0.95,1 transferable=0.35 --label trial
```

A run with other weights must be named. Weight names and defaults are in docs/MECHANISM.md, section 10.

### 14.5 Judge changed pages blind

Agreement measures how close the engine comes to your labellers. Whether a changed page is better is a separate
question, for a judge who reads the pages without knowing which version built them.

`python -m tailor_bench judge LABEL [--data FOLDER]` sends one saved page, `benchmark/runs/LABEL/page.json`, to a
Claude judge (Claude Opus 5.5) with the posting and your whole library. For each project on the page it answers whether
it belongs, for each bullet whether it is relevant, which projects (at most three) should have been there instead, and
why. The verdict is written to `benchmark/runs/LABEL/judge.json`. v1 has no command that shows a judge two pages side
by side, so compare versions by judging each page on its own and hiding which is which.

For a library change, build each benchmark posting's page before and after the change into run folders, and judge
each. From the repository root, since `benchmark` is a relative path here:

```sh
TAILOR_DATA=benchmark python -m tailor_engine page <id> --hybrid --output benchmark/runs/before-<id>/page.json
# make the change; if a project's title, summary, facts or end changed, run tailor_bench read --choices again
TAILOR_DATA=benchmark python -m tailor_engine page <id> --hybrid --output benchmark/runs/after-<id>/page.json
python -m tailor_bench judge before-<id> --data benchmark
python -m tailor_bench judge after-<id> --data benchmark
```

The command lines build no single page under trial weights. To judge those, build the pages in Python and save them
the same way, then run `python -m tailor_bench judge trial-<id> --data benchmark` for each:

```python
import json

from tailor_bench import dataset
from tailor_engine.library import Library
from tailor_engine.reading import store
from tailor_engine.reading.project_choice import projects_hash
from tailor_engine.selection.page import Refused, build_hybrid_page
from tailor_engine.selection.weights import with_changes

library = Library.load()
weights = with_changes(["family_boost=0"])
listing = projects_hash(library)
for entry in dataset.index():
    posting = dataset.posting(entry["id"])
    reading = dataset.reading(posting["text"], entry["id"])
    mapping = dataset.mapping(posting["text"])["requirements"]
    choice = dataset.choice(posting["text"])
    current = choice if store.choice_is_current(choice, listing) else None
    try:
        page = build_hybrid_page(posting, reading, library, mapping, current, weights=weights)
    except Refused as refusal:
        print(entry["id"], "refused:", refusal)
        continue
    folder = dataset.RUNS / f"trial-{entry['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / dataset.PAGE_FILE).write_text(json.dumps(page, indent=1), encoding="utf-8")
```

Only the pages the change altered need judging: compare `selected` in the two records first. Then compare the counts
of projects judged not to belong, bullets judged not relevant and projects named missing. To keep it blind, have
someone else rename the run folders, or read the verdicts before looking at which folder is which. Each judge call
sends your library to Anthropic, through the privacy guard.

### 14.6 What to look at, and what not to over-fit

- **Read the engine's figures against the labellers' agreement with each other.** On the library the engine was
  developed on, the engine's project agreement on target-role postings (0.758) was level with the two advisors'
  agreement with each other on those postings (0.755), and a re-run of the model calls moved agreement as much as any
  configuration change tried.
- **A re-read moves the figures.** Two fresh call 2 mappings of the same readings moved project agreement by up to
  0.03 there. Measure one re-read on your benchmark before trusting a gain smaller than that.
- **Set the pass mark before the run**, and change one thing at a time.
- **A weight earns its place with an ablation**: set it to 0 and doubled, and see whether pages change. On the
  original library two weights changed nothing.
- **Postings used to accept or reject changes stop being a fair test.** Keep a few postings aside, unseen, for a final
  check.
- **Do not tune a weight to make one posting come out right.** A change must improve pages in general.
- **Look at refusals by band**: controls should be refused or get weak pages, and your target roles never refused.

## 15. Changing the code: the output check

A change meant to leave behaviour alone must leave every output the same:

```sh
bash tools/check.sh baseline      # before the change: the snapshot and the Word file hashes, into local/checks/
bash tools/check.sh               # after it: formatting, lint and types, then the snapshot and the Word hashes compared
bash tools/check.sh full          # the same, plus every test and both benchmark runs
```

The snapshot holds everything the engine builds from the example library and the example postings' saved answers at a
pinned date: each posting's page, in engine and hybrid mode, with its trace and document content; the facts of every
example posting; and the loaded library. The Word check writes the eight example postings' Word files
(`tools/word_hashes.py`), which must be byte-identical before and after. Both read `examples/` only, so they check the
code, whatever your own library holds. `full` also runs the benchmark when one exists (`benchmark/index.json`, or the
folder `TAILOR_BENCHMARK` names; `TAILOR_BENCHMARK=examples/benchmark` runs the example one). The script needs bash
and the `.[dev]` install; on Windows run it from Git Bash with `PYTHON=.venv/Scripts/python.exe` (the bash that
PowerShell finds is WSL's, which cannot see the virtual environment). The baseline and the
Word files go to `local/checks/`. The tests alone run with `python -m unittest discover -s tests`; the behaviour locks
among them (`tests/test_selection_regression.py`, `tests/test_word_document.py`) also run on the example library and
its saved answers, so editing your own library never breaks them.

- Do not refresh a saved reading during such a change: a new reading is a new requirement list.
- A change meant to alter pages is measured on your benchmark first, with its pass mark set before the run. The
  locks in the tests are then recorded again on the example library, and the change's effect on the example pages is
  part of what a reviewer reads.
- Adding a project, or editing a project's title, summary, facts or end date, makes every saved call 3 answer stale:
  run `python -m tailor_bench read --choices` again, one model call a posting, and take a new baseline.
