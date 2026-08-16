#!/usr/bin/env bash
# Demo: three PDFs that look identical when printed, and what the tool says
# about each. Fixtures are generated from source at run time, so the demo can
# never drift from what the test suite actually verifies.
set -uo pipefail

cd "$(dirname "$0")"
FIXTURES=tests/fixtures
OUT="${TMPDIR:-/tmp}/trueredact-demo"
mkdir -p "$OUT"

step() { printf '\n\033[1m=== %s\033[0m\n' "$1"; }
run()  { printf '\033[2m$ %s\033[0m\n' "$*"; "$@"; printf '\033[2m-> exit %s\033[0m\n' "$?"; }

# Building the fixtures needs an interpreter that can import pymupdf, and that is
# not necessarily the one that provides `trueredact`: ./install.sh deliberately
# puts the command on PATH while keeping pymupdf in a private environment, and
# Debian and Ubuntu ship no bare `python` at all. Resolved rather than assumed —
# hardcoding `python` made this step fail silently and the demo then scanned
# whatever stale fixtures happened to be lying around.
PY=""
for candidate in .venv/bin/python python3 python; do
  if [ -z "$PY" ] && command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import pymupdf' >/dev/null 2>&1; then
    PY=$candidate
  fi
done
if [ -z "$PY" ]; then
  echo "demo: no Python with pymupdf available to build the fixtures." >&2
  echo "      try: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 2
fi
if ! command -v trueredact >/dev/null 2>&1; then
  echo "demo: 'trueredact' is not on PATH — run ./install.sh first." >&2
  exit 2
fi

step "Building fixtures"
# Hard-fails: scanning fixtures that were never rebuilt is how a demo ends up
# proving something about a file nobody generated.
"$PY" "$FIXTURES/generate_fixtures.py" >/dev/null || {
  echo "demo: could not generate fixtures" >&2
  exit 2
}
echo "generated $(ls "$FIXTURES"/*.pdf | wc -l) fixture PDFs using $PY"

step "1. A clean document — nothing was ever redacted"
run trueredact scan "$FIXTURES/clean.pdf"

step "2. Redacted properly — the text object was removed, then a box drawn"
run trueredact scan "$FIXTURES/properly_redacted.pdf"

step "3. Redacted the way everyone does it — a black box drawn over live text"
run trueredact scan "$FIXTURES/fake_redacted.pdf" --html "$OUT/report.html"

step "Proof: the 'redacted' text is still in the file"
printf '\033[2m$ %s -c "import pymupdf; print(pymupdf.open(%s)[0].get_text().strip())"\033[0m\n' \
  "$PY" "'$FIXTURES/fake_redacted.pdf'"
"$PY" -c "import pymupdf; print(repr(pymupdf.open('$FIXTURES/fake_redacted.pdf')[0].get_text().strip()))"

step "Done"
echo "HTML report: $OUT/report.html"
echo "Exit codes:  0 clean · 1 leak found · 2 cannot scan · 3 partly unauditable"
