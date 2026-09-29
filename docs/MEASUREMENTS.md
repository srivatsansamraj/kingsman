# Measured numbers

Most figures below were measured in September 2026 on the library the engine was developed on (one candidate's
security and AI work), the quality figures on a private benchmark of 25 real postings and 15 other real postings.
None of those can be reproduced from this repository; `examples/` is what you can run and check yourself.

## Time and cost of reading one posting

| step | answered by | time | tokens | cost |
|---|---|---|---|---|
| fetch, facts, the engine's page | no model | page selection 0.8 s, median over 25 postings; loading the library 0.02 s | | |
| call 1 | Claude Sonnet 4.6, low effort | 10.1 s, median over 40 postings | on the example postings 1,468 to 1,696 in, 427 to 718 out | Claude plan |
| call 2, Accuracy | Claude Opus 5.5, medium effort | 8 to 10 s alone; about 17 s for five postings sharing one call in the dashboard | 2,600 to 3,000 in | Claude plan |
| call 2, Speed | Jev | median 1.36 to 1.55 s a posting, its requests sent at once, measured on an earlier form that sent one request fewer | about 121,000 in | about $0.005 |
| call 3 | Jev | about 0.5 s, over 31 postings | about 5,400 in | about $0.0002 |
| call 3 when Jev cannot answer | Claude Opus 5, medium effort | about 5.5 s, over 31 postings | about 4,250 in, 130 out | Claude plan |

Jev's cost is computed from the tokens TypeSafe reported, at its listed price of $0.042 a million input tokens with
output free. Claude calls count against the signed-in Claude plan.

When this repository's example answers were made (one run, eight postings, 2026-09-29): call 1 took 7.5 to 12.2 s a
posting (median 9.6 s) and 1,468 to 1,696 input tokens; call 2 on Opus took 25.2 s for a call of five postings and
14.0 s for a call of three; call 3 on Jev took 0.29 to 0.65 s and 3,628 to 3,815 input tokens a posting, $0.0012 for
all eight. No call fell back or was asked twice.

End to end, a posting read on its own with call 2 on Opus and no call 3 (a match of 0.60 or above) took about 22 s
from URL to page. Once a posting is read, its page is rebuilt with no model call in about 1 s. For a queue, call 1
runs three postings at a time and sets the pace; the figures for a queue are inferred from the call times above, not
measured end to end.

## Quality

**Call 2: Accuracy against Speed.** The capabilities and conditions of 561 requirements from 40 real postings were
labelled blind to which model answered. Opus named the right capabilities on 448 and Jev on 402; where the two
answered a requirement differently, the labels sided with Opus 152 times and with Jev 50. On conditions, Opus was
right on 526 and Jev on about 501 (about 104 of Jev's condition sets had no label and were read by one rater). Blind
judges rated the pages built from the two mappings level. Accuracy is the default for that reason.

**Agreement with labelled pages.** For each benchmark posting, two advisors (a Claude model and an OpenAI model, each
given the library without the complexity and brand scores, the posting and the page rules) chose the projects and
bullets a page should hold. Agreement is the Jaccard overlap (items in both divided by items in either) of the
engine's projects with an advisor's, and of the bullets within projects both chose, averaged over both advisors. 24
postings got a page; one, a sales role, was refused in every configuration.

| configuration | projects | bullets |
|---|---|---|
| the engine alone, call 2 on Opus | 0.674 | 0.575 |
| hybrid, calls 2 and 3 on Opus | 0.696 | 0.583 |
| hybrid, call 2 on Opus, call 3 on Jev (the default) | 0.688 | 0.586 |
| hybrid, calls 2 and 3 on Jev, with an earlier form of Jev's call 2 | 0.671 | 0.586 |
| the two advisors with each other | 0.71 | |

Jev's call 2 in its shipped form (each capability described, conditions asked in a request of their own) has not been
run through this table; an intermediate form scored 0.689 and 0.693 on projects in two runs.

**Blind judgments.** A judge model was shown two pages for the same posting, without being told which configuration
built either, and asked which fits the role better.

- The 0.60 threshold, on 31 postings, with call 3 then answered by Claude Opus 4.8: where the engine's match was under
  0.60, pages built from call 3's projects were preferred 7 to 1 by a Claude Opus 4.8 judge and 4 to 0 by a Claude
  Sonnet 5 judge (4 split); at 0.60 and above, neither side led.
- Call 3 on Jev against call 3 on Claude Opus 5, on 31 postings: 29 pages identical; of the other two, the judges
  split one and preferred Opus 5's on the other.

**Postings never used in development.** On 20 new postings, two judges from different providers, reading blind,
counted 0.65 unwanted projects per page on postings in the candidate's target roles for capability matching, against
1.00 for an earlier version that matched requirements by their literal words. The judges agreed on 83% of project
verdicts. This test ran before hybrid mode and Jev existed.

**Limits of these figures.**

- One person's library, in security and AI. Nothing yet shows how the engine does for a second person or another
  field.
- The 25 benchmark postings were used to accept and reject changes, so their figures are development-set figures.
- The advisors and judges are language models. No recruiter labelled a page, and no hiring outcome was measured.
- The judges are Claude models, the same family as call 2's and call 3's Opus answers. On security pages Claude Opus
  5.5 was stopped by its safety classifier and Claude Opus 4.8 answered, so the "Opus" judgments on those pages are
  Opus 4.8's.
- A saved answer is one draw. Two fresh call 2 mappings of the same readings (by Claude Sonnet 4.6, the model used for
  call 2 at the time) scored 0.663 and 0.703 on projects against 0.690 for the saved ones, so a difference of a few
  hundredths is within the noise of re-reading.
