"""A local job tracker on top of the engine: jobs, their readings and pages, Profile and Stats.

The engine's saved postings, readings, mappings, project choices and pages stay the source of record; the tracker
(SQLite) adds what only the dashboard knows: status, notes, given names, when a job was added or removed, the judge's
last answer, accepted page edits, and the page's build record. Pages are built in hybrid or legacy mode (`page_modes`);
call 3 is answered by Jev, with Opus where it cannot answer, unless TAILOR_CLASSIFIER=opus, and call 2 by Opus, or by
Jev in the Speed mode (`page_modes`).
"""
