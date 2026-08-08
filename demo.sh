#!/usr/bin/env bash
# Demo: three PDFs that look identical when printed, and what the tool says
# about each. Fixtures are generated from source at run time, so the demo can
# never drift from what the test suite actually verifies.
set -uo pipefail

cd "$(dirname "$0")"
FIXTURES=tests/fixtures
OUT="${TMPDIR:-/tmp}/redaction-xray-demo"
mkdir -p "$OUT"

step() { printf '\n\033[1m=== %s\033[0m\n' "$1"; }
run()  { printf '\033[2m$ %s\033[0m\n' "$*"; "$@"; printf '\033[2m-> exit %s\033[0m\n' "$?"; }

step "Building fixtures"
python "$FIXTURES/generate_fixtures.py" >/dev/null
echo "generated $(ls "$FIXTURES"/*.pdf | wc -l) fixture PDFs"

step "1. A clean document — nothing was ever redacted"
run redaction-xray scan "$FIXTURES/clean.pdf"

step "2. Redacted properly — the text object was removed, then a box drawn"
run redaction-xray scan "$FIXTURES/properly_redacted.pdf"

step "3. Redacted the way everyone does it — a black box drawn over live text"
run redaction-xray scan "$FIXTURES/fake_redacted.pdf" --html "$OUT/report.html"

step "Proof: the 'redacted' text is still in the file"
printf '\033[2m$ python -c "import pymupdf; print(pymupdf.open(%s)[0].get_text().strip())"\033[0m\n' \
  "'$FIXTURES/fake_redacted.pdf'"
python -c "import pymupdf; print(repr(pymupdf.open('$FIXTURES/fake_redacted.pdf')[0].get_text().strip()))"

step "Done"
echo "HTML report: $OUT/report.html"
echo "Exit codes:  0 clean · 1 leak found · 2 cannot scan · 3 partly unauditable"
