# The example candidate and postings

Everything in this folder is invented: the candidate, the schools, the employers, the companies posting jobs, and every
figure in every bullet. It lets you run Kingsman, read its output and check it against a library, before you have a
library of your own.

| path | what it is |
|---|---|
| `library/` | a complete library for the fictional candidate: the seven TOML files and a Word template |
| `postings/` | eight fictional job postings as plain text, `x1-...txt` to `x8-...txt` |
| `data/postings/` | the same eight postings as the posting records `read` saves, named by their ids |
| `data/readings/`, `data/mappings/`, `data/choices/` | each posting's saved answers to calls 1, 2 and 3 (below) |
| `output/` | each posting's hybrid page record (`.json`), as `page` prints it (`.txt`), and its Word file (`.docx`); X1's page rendered as an image (`.png`) |
| `benchmark/` | an illustrative benchmark on the eight postings, with labels written by hand (below) |

## The saved answers

Made once, on 2026-09-29, with this repository's code: one run, every answer valid at the first attempt, and no
fallback to another model.

| call | answered by | how | saved in |
|---|---|---|---|
| 1 | `claude-sonnet-4-6` | one posting at a time, 7.5 to 12.2 s each | `data/readings/<id>-<key>.json` |
| 2 | `claude-opus-5-5` (Accuracy) | two calls: X1 to X5 together in 25.2 s, then X6 to X8 in 14.0 s | `data/mappings/<key>.json` |
| 3 | `jev-1.13.0` | one request a posting, for all eight whatever the match, 0.29 to 0.65 s each | `data/choices/<key>.json` |

`<key>` is the first 16 hexadecimal characters of the SHA-256 of the posting's text. Each record names the model
asked for (`model`), the one that answered (`answered_by`), its attempts, tokens and seconds; a mapping made in a
shared call records how many postings shared it (`batch`) and an equal share of its tokens. Call 3 was made for all
eight so that hybrid pages can be built at any threshold and the tests can lock them; `read --hybrid` makes it only
where the engine's match is under 0.60.

## The candidate

Robin Kestrel, who finished a Master of Science in Cybersecurity in May 2026 after two internships. The contacts are on
`example.com`, and the phone number, (303) 555-0142, is in a range reserved for fiction. The contact paragraph ends
with "Denver, CO – open to relocation", which wraps onto a line of its own in the printed page.

| degree | institution | dates | courses |
|---|---|---|---|
| Master of Science in Cybersecurity | Halvorsen Institute of Technology | Sep 2024 to May 2026 | 13; fixed line "Graduate Research Assistant, Applied Security Lab" |
| Bachelor of Science in Computer Science | Lakemont State University | Sep 2019 to May 2023 | 12 |

Certifications, the fixed skill row: OSCP, and AWS Certified Security - Specialty.

### Projects

13 projects and 41 bullets, of which 37 are usable. Project ids start with I for internships, G for graduate work, U
for undergraduate work, P for personal projects and O for the oldest work. Standing is merit discounted by age
([docs/MECHANISM.md](../docs/MECHANISM.md), section 5), here as of 2026-09-28; it falls as dates pass.

| id | project | ends | complexity | brand | standing | usable bullets |
|---|---|---|---|---|---|---|
| I1 | Product Security Intern, Fernhill Payments | 2025-08 | 0.62 | 0.55 | 0.544 | 5 |
| I2 | Detection Engineering Intern, Quarrystone Freight | 2022-08 | 0.52 | 0.45 | 0.349 | 3 |
| G1 | Prompt-Injection Evaluation of Tool-Using LLM Agents, Applied Security Lab | 2026-05 | 0.70 | 0.45 | 0.583 | 5 |
| G2 | Fuzzing an Open-Source Image-Decoding Library | present | 0.60 | 0.35 | 0.500 | 3 |
| G3 | Adversarial Patches on a Traffic-Sign Classifier, Machine Learning Security Course | 2025-05 | 0.40 | 0.35 | 0.342 | 2 |
| G4 | Malware Triage Sandbox, Malware Analysis Course | 2025-12 | 0.50 | 0.30 | 0.396 | 3 of 4 |
| G5 | Ransomware Tabletop and Response Plan for a University IT Team, Security Risk Management Course | 2025-04 | 0.30 | 0.35 | 0.287 | 2 of 3 |
| G6 | Kubernetes Lab Platform on AWS for a Security Course, Graduate Research Assistant | 2025-12 | 0.58 | 0.40 | 0.479 | 4 |
| U1 | Capture-the-Flag Team Captain, Lakemont State University | 2023-04 | 0.42 | 0.35 | 0.296 | 3 |
| U2 | Android Permission Auditor, Mobile Development Course | 2022-05 | 0.35 | 0.25 | 0.214 | 2 |
| U3 | Inventory Web App for a Regional Food Bank, Senior Capstone | 2023-05 | 0.45 | 0.30 | 0.297 | 3 |
| P1 | Home Network Device Monitor on a Raspberry Pi (held) | 2024-02 | 0.25 | 0.10 | 0.155 | 0 of 2 |
| O1 | Campus Bus Arrival Board, First-Year Hackathon Project | 2020-04 | 0.25 | 0.20 | 0.138 | 2 |

The library is written to exercise each rule of the engine:

- **a held project**: P1, kept off every page and out of call 3's list, and still sent to the judges;
- **a fragile bullet**: G5.2, kept off every page, and still sent to the judges;
- **a pending bullet**: G4.4, not written yet, never placed or sent;
- **an `excludes` pair**: I1.4 and I1.5, two wordings of one result, never both on a page;
- **liabilities**: G2.3 (0.2) and U2.2 (0.3);
- **a bullet with no capability**: U1.1, a competition placing;
- **notes**: a private note on I1, never sent to a judge, and a note on G2 that mentions a repository, which the
  judges' filter leaves out;
- **weak projects that alone show a capability**: O1 for data analysis and U2 for Android development, which the
  weak-project rule lets onto a page only for a required item nothing else meets;
- **age**: O1 is old enough that its recency sits at the 0.60 floor;
- **gaps**: 30 of the 76 capabilities have no usable bullet (among them applied cryptography, software supply chain
  security, formal verification, device security, microcontrollers and sensors, augmented reality apps, and LLM
  training and fine-tuning), so the dashboard's Coverage view has gaps to show.

Skill rows: seven compete for the page's five places (`appsec`, `offsec`, `detect`, `cloud`, `ai-sec`, `ml`, `tools`),
with 57 distinct skills; Burp Suite sits in `appsec` and `offsec`, Docker in `cloud` and `tools`.
`skill-capabilities.toml` has an entry for every skill and all 25 courses, so the library loads with no warnings. The
two vocabularies are the ones Kingsman ships (capability vocabulary hash `fce4b8d6fbc868de`).

The template was rendered to one page in LibreOffice for itself, for a sample page, and for a worst case (28 project
lines, the longest skill rows, 11 courses per degree). It has not been opened in Microsoft Word.

## The postings

| | posting | id | written to exercise | facts read by rule |
|---|---|---|---|---|
| X1 | Application Security Engineer, Quillmere Health | `paste-89f5dd84` | the candidate's closest fit: product security, code review, API testing | 1 year, bachelor's degree |
| X2 | Offensive Security Engineer, Vulnerability Research, Blackfen Labs | `paste-87b68035` | fuzzing and exploitation | 2 years |
| X3 | AI Security Evaluation Engineer, Lumenfold AI | `paste-3d814d16` | AI security and evaluation, a domain in two families | sponsors visas |
| X4 | Detection and Response Engineer, Carrowmere Federal Systems | `paste-9725cefb` | requirements that screen candidates out, and detection and response work | 5 years, clearance required, no visa sponsorship |
| X5 | Cloud Security Engineer, Stratavale | `paste-afdfe55e` | cloud and Kubernetes security | 3 years, bachelor's degree |
| X6 | Software Engineer, Backend (Platform), Orchard Ledger | `paste-12648881` | a neighbouring engineering role | 2 years |
| X7 | Data Analyst, Supply Chain, Wrenfield Markets | `paste-f945f360` | a distant role, where a weak, old project holds the only matching capability | 1 year, bachelor's degree |
| X8 | Senior Accountant, Revenue, Copperline Outdoor Co. | `paste-f1f45dad` | a non-technical control, written to be refused | 4 years, bachelor's degree |

Every posting states a salary range, which the facts show. None has a "Location:" line, the only form the facts
read a location from, so the dashboard shows each one's location as "Not stated".

## Results

The hybrid pages in `output/`, built on 2026-09-29. Reach is the share of the requirement weight the whole library
could meet; the engine's match is that of the engine's own page.

| | requirements (required) | reach | engine's match | page | projects chosen by | required items not fully met |
|---|---|---|---|---|---|---|
| X1 | 18 (14) | 0.98 | 0.88 | G2, G1, G6, I1: 12 bullets, 28 lines | the engine | 3 |
| X2 | 16 (12) | 0.89 | 0.82 | G2, G1, G4, I1, U1: 12 bullets, 28 lines | the engine | 3 |
| X3 | 13 (9) | 0.90 | 0.89 | G2, G1, I1, G3: 12 bullets, 27 lines | the engine | 2 |
| X4 | 18 (14) | 0.71 | 0.71 | G2, G1, G4, I2: 13 bullets, 28 lines | the engine | 6 |
| X5 | 18 (14) | 0.78 | 0.71 | G1, G6, G4, I1: 12 bullets, 28 lines | the engine | 7 |
| X6 | 18 (14) | 0.87 | 0.77 | G2, G1, G6, U3: 13 bullets, 28 lines | the engine | 5 |
| X7 | 14 (11) | 0.72 | 0.72 | G2, G1, I1, O1: 13 bullets, 28 lines | the engine | 3 |
| X8 | 17 (13) | 0.28 | 0.28 | G2, G1, I1, I2: 13 bullets, 28 lines | call 3's projects | 12 |

- No posting was refused. X8 was written to be refused, but call 2 mapped nine of its accounting requirements onto the
  capability "financial and audit controls", in the vocabulary's risk and compliance domain. No bullet shows it;
  I2.3 (the response runbooks) shows "security documentation" in the same domain and earns half credit on each,
  and the degrees meet the bachelor's degree. That put its reach at 0.28, over the 0.25 floor. Its match is under
  0.60, so its page is built from call 3's six projects; U3 and G5, both under the weak-project rule's 0.35
  standing, were left off.
- The dashboard lists two deal-breakers at the top of X4: "U.S. citizenship required" and "Security clearance
  required".
- O1, the weak, old hackathon project, reached X7's page with one bullet (O1.2), its comparison of scheduled and actual
  arrivals.
- G2 and G1, the two strongest projects by standing, are on every page. On the neighbouring and distant roles the
  hand-written labels in `benchmark/` chose differently (the database capstone U3, the data project O1), which the
  example benchmark's lower figures for those bands show.

## Running them

From the repository root. The example library is the default library until `library/` exists; to be explicit, or
once you have your own, set `TAILOR_LIBRARY=examples/library`.

The dashboard on these postings with every model answer stood in, needing no sign-in or key:

```sh
python tools/dev_server.py      # then open http://127.0.0.1:8771/
```

A page and its Word file from the saved answers, with no model call:

```sh
export TAILOR_DATA=examples/data      # PowerShell: $env:TAILOR_DATA = "examples/data"
python -m tailor_engine page paste-89f5dd84 --hybrid --output x1.json
python -m tailor_engine word x1.json --output x1.docx
```

`read` and the dashboard write beside the data (the dashboard's tracker `tracker.sqlite`, its page mode
`page-modes.json`, its pages `pages/`), so give them a copy:

```sh
cp -r examples/data local/examples-data     # PowerShell: Copy-Item -Recurse examples/data local/examples-data
export TAILOR_DATA=local/examples-data      # PowerShell: $env:TAILOR_DATA = "local/examples-data"
python -m tailor_engine read local/examples-data/postings/paste-89f5dd84.json --hybrid
python -m tailor_dashboard                  # then open http://127.0.0.1:8770/
```

`read` prints `model calls: none, already read`, since every answer is saved. Reading the plain-text file instead
gives the same posting id, because a pasted posting's id comes from its text:

```sh
python -m tailor_engine read examples/postings/x1-application-security-engineer-quillmere-health.txt --title "Application Security Engineer" --company "Quillmere Health" --hybrid
```

A posting you add makes the model calls, which need the Claude Code sign-in and a privacy guard word (README, quick
start).

The example benchmark, with no model call:

```sh
TAILOR_BENCHMARK=examples/benchmark python -m tailor_bench benchmark
```

Its labels, `benchmark/labels/<id>.labeller-1.json` and `<id>.labeller-2.json`, were written by hand for this candidate
to show the format (ONBOARDING.md, section 14); the file names are the two labellers' names the bench tools expect.
Its figures describe those labels, not the engine.

To check a page by hand, open its record in `output/` and follow the README's "How to audit a page": every id in
`selected` is a bullet in `library/projects.toml`, printed word for word.
