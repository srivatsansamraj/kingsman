#!/usr/bin/env bash
# The output check for a change meant to leave behaviour alone. Run from the repository root, with the Python that has
# the package installed (`pip install -e .[dev]`) first on the path, or named in PYTHON.
#   bash tools/check.sh baseline   before the change: the snapshot and the Word hashes, into local/checks/ (not in git)
#   bash tools/check.sh            after it: lint, types, the snapshot and the Word hashes against the baseline
#   bash tools/check.sh full       the same plus every test, and the benchmark when it has an index.json
#                                  (benchmark/, or the folder TAILOR_BENCHMARK names)
# The snapshot and the Word files are built from the example library and its saved answers (examples/).
set -uo pipefail
PY="${PYTHON:-python}"
# On Windows run this from Git Bash with PYTHON=.venv/Scripts/python.exe: the bash that PowerShell finds is WSL's,
# which cannot see the virtual environment.
if ! command -v "$PY" > /dev/null 2>&1; then
  echo "check.sh: no \"$PY\" on the path; set PYTHON to the virtual environment's Python (Windows, Git Bash: PYTHON=.venv/Scripts/python.exe)" >&2
  exit 127
fi
OUT=local/checks
mkdir -p "$OUT"
if [ "${1:-}" = "baseline" ]; then
  "$PY" tools/snapshot.py write "$OUT/snapshot.json"
  "$PY" tools/word_hashes.py "$OUT/word-baseline" > "$OUT/word-baseline.txt" && cat "$OUT/word-baseline.txt"
  exit
fi
status=0
echo "== ruff";     "$PY" -m ruff format --check src tests tools | tail -1 || status=1
                    "$PY" -m ruff check src tests tools | tail -1 || status=1
echo "== mypy";     "$PY" -m mypy src tests tools | tail -1 || status=1
echo "== snapshot"; "$PY" tools/snapshot.py compare "$OUT/snapshot.json" || status=1
echo "== Word files"
"$PY" tools/word_hashes.py "$OUT/word-now" > "$OUT/word-now.txt"
if diff "$OUT/word-baseline.txt" "$OUT/word-now.txt"; then echo "byte-identical ($(wc -l < "$OUT/word-now.txt") files)"; else status=1; fi
if [ "${1:-}" = "full" ]; then
  echo "== tests";     "$PY" -m unittest discover -s tests 2>&1 | tail -3 || status=1
  # Rows picked by name, so a warning printed before the table cannot take their place; warnings are shown too.
  if [ -f "${TAILOR_BENCHMARK:-benchmark}/index.json" ]; then
    echo "== benchmark"; "$PY" -m tailor_bench benchmark --label check 2>&1 | grep -E '^(all|lane) |warning|not in the posting' || status=1
    "$PY" -m tailor_bench benchmark --hybrid --label hybrid-check 2>&1 | grep -E '^(all|lane|hybrid)[ :]|warning|not in the posting' || status=1
  else
    echo "== benchmark: no ${TAILOR_BENCHMARK:-benchmark}/index.json, left out (ONBOARDING.md, section 14.2)"
  fi
fi
exit $status
