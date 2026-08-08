# TrueRedact — Development Plan

> The most important document in this blueprint. Follow phases in order — each depends on the previous. See [TECHNICAL-DESIGN.md](./TECHNICAL-DESIGN.md) for schemas/algorithm detail referenced below.

---

## Phase 0 — Foundation

**Objective.** A repo that installs, lints, and runs CI green with nothing but a "hello world" CLI — before any real logic exists.

**Why now.** Everything else depends on having a working package/import structure and a CI gate that will catch regressions in the algorithm from day 1 onward.

**Tasks**
1. Initialize repo with the structure in [TECHNICAL-DESIGN.md § Repository Structure](./TECHNICAL-DESIGN.md#repository-structure).
2. `pyproject.toml` — package metadata, Python `>=3.11`, PyMuPDF as the sole runtime dependency, pytest/ruff as dev dependencies.
3. `src/trueredact/cli.py` — stub `argparse` entry point: `trueredact scan <file>` prints "not implemented" and exits `0`.
4. `.github/workflows/ci.yml` — run `ruff check` + `pytest` on push/PR.
5. `README.md` — one-paragraph project description + install/run instructions (fill in as later phases land).

**Files.** `pyproject.toml`, `src/trueredact/__init__.py`, `src/trueredact/cli.py`, `.github/workflows/ci.yml`, `README.md`, `.gitignore`.

**Tests.** A single smoke test: `trueredact --help` exits `0`.

**Definition of Done.**
- [x] `pip install -e .` succeeds in a clean virtualenv.
- [x] `trueredact --help` runs.
- [x] CI is green on a pushed branch.

**Dependencies.** None — this is the starting point.

---

## Phase 1 — Core Domain + Extraction Spike

**Objective.** Prove the foundational assumption the entire project depends on: that PyMuPDF's paint-order data reliably distinguishes "shape drawn after text" from "text drawn after shape" across real-world PDF producers. Also lands the domain model types.

**Why now.** This is the highest-risk unknown in the whole plan (flagged in [PROJECT-PLAN.md § Major Risks](./PROJECT-PLAN.md#8-major-risks)). If it doesn't hold up, the detection approach needs to change *before* two more days get built on top of it — so it comes immediately after Phase 0, not after the algorithm is written.

**Tasks**
1. Define `TextSpan`, `ShapeObject`, `Finding`, `ScanReport` dataclasses per [TECHNICAL-DESIGN.md § Domain Model](./TECHNICAL-DESIGN.md#domain-model).
2. Implement `core/loader.py` (open + validate a PDF; size/page caps; typed `LoadError`s).
3. Implement `core/extractor.py`: walk `page.get_text("dict")` and `page.get_drawings()`, normalize both into the domain types with page-space bounding boxes and a paint-order index.
4. **Spike, not production code:** manually create 5 test PDFs, each with a black rectangle drawn over text, from 5 different producers (e.g. Microsoft Word export, Google Docs export, macOS Preview "Markup," LibreOffice export, and one hand-built with `reportlab`). Extract spans/shapes from each and manually confirm the paint-order signal is consistent and usable. `VALIDATION REQUIRED` — record findings directly in this file's Risks note or a `docs/spike-notes.md`.
5. If the signal is *not* consistent across producers, stop and re-evaluate the algorithm approach (e.g., fall back to raw content-stream operator order parsing instead of relying on PyMuPDF's derived paint order) before proceeding to Phase 2.

**Files.** `src/trueredact/core/models.py`, `core/loader.py`, `core/extractor.py`, `tests/test_loader.py`, `tests/test_extractor.py`, `tests/fixtures/generate_fixtures.py` (script to produce the 5 spike PDFs programmatically so they're reproducible, not hand-crafted one-offs).

**Tests.** Loader: rejects non-PDF, encrypted PDF, oversized file, each with the correct `LoadError` variant. Extractor: given a hand-built single-shape/single-text-span PDF, returns exactly the expected `TextSpan`/`ShapeObject` with correct bbox and paint order.

**Definition of Done.**
- [x] Spike across 5 producers complete, results recorded → [spike-notes.md](./spike-notes.md). Exceeded: **33 producers, 187 files, 4,397 pages.**
- [x] Loader and extractor pass their unit tests (22 tests).
- [x] Go/no-go decision made explicitly on the paint-order approach before Phase 2 starts → **GO**, recorded in [DECISIONS.md](./DECISIONS.md).

**Deviations from this plan, and why.**
- *Spike ran before the model was written, not after.* Defining the domain types first would have baked in the very assumptions the spike existed to test — and it did overturn two of them (rotation, XObject recursion).
- *Real producer output replaced hand-made spike PDFs.* 187 real PDFs from 33 producers were already on the development machine, which is stronger evidence than five synthetic files and cost less. Only aggregate structural statistics were read; no document content was inspected.
- *`Finding` / `ScanReport` / `Verdict` deferred to Phase 2/3.* They are detector output, nothing in Phase 1 produces one, and `Finding.confidence` is under active review (see Phase 2). Defining them now would mean rewriting them.
- *Two planned mechanisms deleted rather than built:* the rotation transform and the XObject recursion. Both measured unnecessary.

**Dependencies.** Phase 0.

---

## Phase 2 — Detection Algorithm

**Objective.** Implement the actual overlap-detection core — the one component that must be correct, since it's the entire value proposition.

**Why now.** Domain types and real extracted data (from the Phase 1 spike PDFs) already exist to test against; this is a pure function with no I/O, so it can be built and tested in isolation from the CLI/reporting layers.

**Tasks**
1. Implement `core/detector.py` per the pseudocode in [TECHNICAL-DESIGN.md § Core Algorithm](./TECHNICAL-DESIGN.md#core-algorithm): candidate-shape filtering (opacity, fill solidity, minimum area), paint-order-constrained bbox overlap, coverage-ratio threshold, confidence scoring.
2. Extend `tests/fixtures/generate_fixtures.py` to programmatically generate the three canonical fixture classes: **clean** (no shapes over text), **properly redacted** (text object actually removed, shape present), **fake redacted** (shape drawn over live text).
3. Unit-test `detector.py` directly against hand-built `TextSpan`/`ShapeObject` lists (no PDF parsing involved) to cover exact-boundary cases: full coverage, partial coverage below/above threshold, shape below text in paint order (should NOT flag), transparent shape (see `IMPLEMENTATION DECISION` in TECHNICAL-DESIGN on whether to flag).
4. Integration-test the full pipeline (loader → extractor → detector) against the three fixture classes end-to-end.

**Open question to settle first — `Finding.confidence`.** [TECHNICAL-DESIGN.md](./TECHNICAL-DESIGN.md#core-algorithm) specifies a 0..1 weighted heuristic and the JSON schema shows `"confidence": 0.94`. Nothing calibrates that number against outcomes, so it reads as a probability while being an arbitrary weighted sum — false precision in a tool whose whole selling point is verifiable evidence. Proposal: drop the field and report the exact inputs instead (coverage ratio, fill opacity, fill colour, paint-order gap), which are already exact, already explainable, and already what a human would check. Decide and record in [DECISIONS.md](./DECISIONS.md) before writing the detector; changing it later is a breaking change to the JSON contract.

**Also settled by the Phase 1 spike, and required here:** `UNCERTAIN` on a `seqno` tie, `UNCERTAIN` on substantial image coverage, `UNCERTAIN` on a page with `PageContent.error` set. See [spike-notes.md](./spike-notes.md).

**Files.** `src/trueredact/core/detector.py`, `tests/test_detector.py`, `tests/test_pipeline_integration.py`, fixture generator updates.

**Tests.** This phase *is* mostly tests — see Tasks 3–4. Minimum bar: all three fixture classes produce the correct verdict with zero false positives/negatives.

**Definition of Done.**
- [x] `detector.py` complete and covered by unit tests including boundary cases (48 detector tests; area, opacity and coverage thresholds each tested on both sides).
- [x] Integration test passes against all three fixture classes, plus rotation, XObject nesting, frames, clipping, slide builds, and image covers.
- [x] ~~Confidence scoring produces sane, monotonic values~~ → **`confidence` was removed.** Findings carry the measurements instead. Recorded in [DECISIONS.md](./DECISIONS.md).

**Validated beyond the plan.** The detector was run over the 4,397-page real-document corpus and every `FAKE_REDACTION` was ground-truthed by rendering. False positives went **1,598 → 44 → 10**, and all 10 survivors were confirmed structurally correct. Details in [spike-notes.md § Phase 2 Addendum](./spike-notes.md). Two rules exist purely because of that survey: clip/frame-aware shape extraction, and the repainted-text rule.

**Dependencies.** Phase 1 (domain model, extractor, and validated paint-order assumption).

---

## Phase 3 — CLI + JSON Report

**Objective.** Wire the detection pipeline up to a real, usable command: `trueredact scan file.pdf [--json out.json]`.

**Why now.** The algorithm is proven in isolation (Phase 2); this phase makes it usable and gives the first end-to-end demoable artifact.

**Tasks**
1. Replace the Phase 0 CLI stub with the real orchestration: load → extract → detect (per page) → aggregate into `ScanReport` → render stdout summary → set exit code.
2. Implement `core/report_json.py` per the schema in [TECHNICAL-DESIGN.md § JSON Report Schema](./TECHNICAL-DESIGN.md#json-report-schema).
3. Wire `--json <path>`, `--max-pages`, `--max-file-size-mb`, `-v/--verbose` flags.
4. Human-readable stdout formatting: per-finding page number, recovered text, confidence — plain text, no dependency on a TUI library.

**Files.** `src/trueredact/cli.py` (rewrite), `core/report_json.py`, `tests/test_cli.py`, `tests/test_report_json.py`.

**Tests.** CLI end-to-end against each fixture class: correct exit code, correct JSON schema, correct stdout content (assert on key substrings, not exact formatting).

**Definition of Done.**
- [x] `trueredact scan fake_redacted.pdf` prints the recovered text and exits `1`.
- [x] `trueredact scan clean.pdf` exits `0`.
- [x] `--json` output validates against the documented schema.

**Deviation.** A fourth exit code, `3`, was added for "completed, no leak, but some pages could not be audited" — see [DECISIONS.md](./DECISIONS.md). Three codes could not express the difference between "nothing suspicious" and "we could not check part of this", and ~19% of real-world pages fall in the latter bucket.

**Dependencies.** Phase 2.

---

## Phase 4 — HTML Report

**Objective.** A self-contained, shareable HTML report with page-preview thumbnails and the leaked region visually highlighted — the artifact that makes the finding "provable" to a non-technical reader, not just a JSON blob.

**Why now.** This is the single highest-leverage feature for demo impact (per the original proposition's "HOLY SHIT" feature), and it depends on findings already existing (Phase 3) — no reason to build it earlier.

**Tasks**
1. Implement `core/report_html.py`: render each flagged page as a preview image (`page.get_pixmap()`), draw a highlight rectangle over the detected region, embed the recovered text alongside it.
2. Template: plain Python string templating (f-strings/`string.Template`) producing one self-contained HTML file with inlined CSS and base64-embedded page images — no external assets, no server. `IMPLEMENTATION DECISION`: revisit Jinja2 only if the template logic outgrows string substitution.
3. Wire `--html <path>` flag in the CLI.

**Files.** `src/trueredact/core/report_html.py`, `tests/test_report_html.py`.

**Tests.** Generated HTML is well-formed and contains the expected recovered-text string and an embedded image per finding (assert on HTML structure, not pixel-level image content).

**Definition of Done.**
- [x] `--html report.html` produces a file that opens correctly in a browser with zero external dependencies (asserted: no `<script>`, no `http(s)://`, no non-`data:` `src`).
- [x] Each finding is visually highlighted on its page preview — the covering shape and each still-extractable span get separate overlays.

**Implementation notes.**
- Highlights are CSS-positioned overlays on an unmodified page image, not pixels drawn into it. Percentages of the rendered page need no DPI arithmetic and stay crisp at any zoom.
- **This is the only place in the codebase where `/Rotate` matters.** The extractor works in unrotated mediabox space; the preview renders in display space. `_overlay_style` bridges them with `page.rotation_matrix`, verified against actual rendered pixel positions at 0/90/180/270 and locked by a parametrized test.
- Previews are produced only for `FAKE_REDACTION` findings, and **one per page, not one per finding**. A preview exists to prove a leak; an unauditable page has nothing proven to show, and rendering every scan page of a 500-page document would produce a file nobody can open. Measured on the real corpus: 0.20–0.52 MB per report.

**Dependencies.** Phase 3.

---

## Phase 5 — Fixture-Based Hardening

**Objective.** Close the gap between "works on the 3 fixtures we built" and "doesn't embarrass us on a real PDF" — rotation, nested XObjects, multi-object pages, transparency edge cases.

**Why now.** Core functionality and both report formats already exist; this phase spends the remaining time budget on correctness robustness rather than new features, which is the right trade at this point in a 4–6 day project.

**Tasks**
1. Extend fixtures to cover: rotated pages (`/Rotate` 90/180/270), text/shapes nested inside form XObjects, multiple overlapping shapes on one page, a semi-transparent shape over text (confirm the `IMPLEMENTATION DECISION` from Phase 2 behaves as intended), a legitimate colored table cell that should NOT be flagged.
2. Fix any detector/extractor bugs surfaced by the new fixtures.
3. Run the tool against a handful of real (non-fixture, self-sourced, non-sensitive) PDFs to sanity-check behavior beyond synthetic cases.

**Files.** Fixture generator additions, fixes across `core/extractor.py` and `core/detector.py` as needed, corresponding new test cases.

**Tests.** One test per new edge case added in Task 1; all previous tests still pass.

**Definition of Done.**
- [x] All edge-case fixtures pass with the correct verdict (19 fixtures, 128 tests).
- [x] No known false positive/negative left unaddressed or undocumented — see the `KNOWN LIMITATION` list in [TECHNICAL-DESIGN.md](./TECHNICAL-DESIGN.md#core-algorithm) and [spike-notes.md](./spike-notes.md).

**Two bugs found by hunting rather than by confirming.** Written up in [spike-notes.md § Phase 5 Addendum](./spike-notes.md):
1. **A corrupted content stream was reported `CLEAN`.** MuPDF recovers silently instead of raising, so the page extracted as empty. Now detected from a narrow allowlist of MuPDF warnings; measured to add zero false uncertainty across the corpus.
2. **`PageContent.width/height` used the MediaBox** while MuPDF reports CropBox-relative coordinates. Latent (nothing consumed it yet) but wrong; fixed, and the coordinate space renamed accurately throughout.

**Limitation made precise.** A rectangle painted under a rotating/skewing `cm` transform is reported as four line segments, so the "non-rectangular paths" limitation covers tilted rectangles too. Pinned by a test asserting the current behaviour, so it fails loudly if MuPDF ever changes.

**Fixtures added this phase:** overlapping covers, semi-transparent cover (flagged) and barely-transparent tint (not flagged), a shaded table with text written into it, a cropped page, a tilted cover, and a three-page document with a corrupted middle page.

**Dependencies.** Phase 4 (needs the full pipeline and both report formats to validate against).

---

## Phase 6 — Packaging & Demo Polish

**Objective.** Make the tool trivially installable and the demo bulletproof.

**Why now.** Last phase by design — polish only matters once the underlying tool is correct (Phases 1–5).

**Tasks**
1. Finalize `pyproject.toml` for `pip install` from source (PyPI publish is explicitly Future scope, not required for the demo).
2. Finalize `README.md`: install instructions, usage examples, sample output (before/after screenshots or terminal recording).
3. Prepare the exact demo script/fixtures referenced in [PROJECT-PLAN.md § Success Criteria](./PROJECT-PLAN.md#7-success-criteria): a fake-redacted sample, a properly-redacted sample, a clean sample — checked into `tests/fixtures/` and reused directly for the live demo so there's zero risk of an untested file misbehaving on stage.
4. Full dry-run of the 3-minute demo end-to-end, timed.

**Files.** `README.md`, `pyproject.toml` finalization, demo fixture files.

**Tests.** None new — this phase is validation of what already exists, not new logic.

**Definition of Done.**
- [x] Fresh clone → `pip install` → demo script runs clean, no manual fixes needed. Verified in a clean-room copy (no `.git`, no `.venv`, no built fixtures) with a **non-editable** `pip install ".[dev]"`: console script on PATH, all 128 tests pass against the installed package, `./demo.sh` runs.
- [x] Demo dry-run completes in under 3 minutes — **0.6 seconds** for the automated portion.

**Notes.**
- `demo.sh` generates its fixtures from `generate_fixtures.py` at run time rather than using checked-in binaries, so the demo can never drift from what the test suite verifies. No `.pdf` files are committed.
- `LICENSE` (MIT) added — declared in `pyproject.toml` since Phase 0 but the file was missing. **The copyright line reads "the trueredact authors" and should be replaced with a real name before publishing.**
- PyPI publishing remains explicit Future scope; the package installs from source.

**Dependencies.** Phase 5.
