# Redaction X-Ray — Project Plan

> Read this document first. It links to [ARCHITECTURE.md](./ARCHITECTURE.md), [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md), [TECHNICAL-DESIGN.md](./TECHNICAL-DESIGN.md), and [DECISIONS.md](./DECISIONS.md) for detail.

## 1. Project Overview

**Problem.** PDF redaction is frequently done wrong. The dominant real-world failure mode is drawing an opaque black rectangle over sensitive text instead of actually removing the underlying text object — the "redacted" content stays fully present in the file and is trivially recoverable by selecting/copying it or running text extraction. This exact bug has caused real, repeated, public incidents (court filings, government document releases, corporate disclosures leaking the precise information the redaction was meant to hide).

**Solution.** A local, offline CLI tool that reads a PDF's internal object structure — not its rendered appearance — to detect when an opaque shape sits on top of an extractable text run, and reports (and recovers) the "hidden" text as proof. No OCR, no image analysis, no AI/ML: the detection is a deterministic structural fact about the file, not an inference.

**Target users.** Anyone who redacts a PDF before sharing it (job applicants scrubbing personal info, paralegals, journalists, HR/procurement staff) and wants a fast, trustworthy, offline check before hitting send — plus engineers evaluating or building document-security tooling.

**Core value.** Turns "I think I redacted this properly" into a verifiable yes/no, in seconds, without the document ever leaving the user's machine.

## 2. Goals & Non-Goals

### Goals
- Detect the dominant real-world fake-redaction pattern: an opaque vector/shape object drawn over a live text object in the page's content stream.
- Recover and display the covered text when detection fires — the evidence *is* the deliverable.
- Run entirely offline. A sensitive-document tool that phones home defeats its own purpose.
- Be usable in under a minute: `pip install`, one command, plain-language output.

### Non-Goals (MVP)
- Not a redaction *tool* — it audits redaction, it doesn't perform it.
- Not OCR/image-based analysis. Scanned, text-layer-free PDFs are explicitly out of scope; the tool must say "cannot audit — no text layer" rather than guess from pixels.
- Not a web service. No accounts, no cloud upload, no multi-tenant anything.
- Not AI/ML anywhere in the pipeline. Every finding must be explainable by exact spatial/structural evidence, not a learned model's confidence score.

## 3. MVP Scope

**Must exist for the first working, demoable version:**
- `redaction-xray scan <file.pdf>` — prints human-readable findings, sets exit code (`0` clean, `1` leak found, `2` error).
- Core detection algorithm: per-page paint-order overlap between opaque shapes and text spans.
- `--json <path>` — machine-readable findings report.
- `--html <path>` — single self-contained HTML report with page previews and the leaked region highlighted.
- Correctly handles: rotated pages, multi-object content streams, nested form XObjects.
- A fixture-based test suite (self-generated PDFs: clean / properly-redacted / fake-redacted) that proves the algorithm is *correct*, not just that the code runs.

**Deferred — see [TECHNICAL-DESIGN.md § Future Scope](./TECHNICAL-DESIGN.md#future-scope-explicitly-deferred):** folder/batch scanning, a local web UI, scan-history storage, annotation-based (as opposed to content-stream) redaction detection, incremental-update byte forensics.

## 4. System Overview

A single Python process, no client/server split, no persistence layer, no network calls: read PDF → extract text spans and shape objects per page (PyMuPDF) → run the pure overlap-detection algorithm → emit findings as stdout / JSON / HTML. Full diagram in [ARCHITECTURE.md](./ARCHITECTURE.md).

## 5. Technology Stack

| Choice | Why |
|---|---|
| **Python 3.11+** | The one library this project actually needs (PyMuPDF) has its best bindings here; performance is fine because the heavy lifting happens in PyMuPDF's C core, not in Python loops. |
| **PyMuPDF** (import as `pymupdf`; the legacy `fitz` alias is deprecated as of 1.28) | The only mature library exposing *both* text spans and vector/shape objects with bounding boxes **and** paint order through one API — this is the load-bearing dependency the entire algorithm rests on. See [DECISIONS.md](./DECISIONS.md#decision-pymupdf-as-the-pdf-engine). |
| **stdlib `argparse`** | One flat command with a handful of flags doesn't justify a Click/Typer dependency. `IMPLEMENTATION DECISION`: revisit only if subcommands grow past 2–3. |
| **stdlib `json` + string-templated HTML** | Report generation. No web framework — the report is a static file, never served. |
| **pytest** | Standard; no justification needed. |
| **GitHub Actions** | Lint + test on every push. Worth having from day 1 even solo, because the one thing that must never silently regress is the detection algorithm's correctness. |

## 6. Development Phases

Full detail, tasks, and Definition of Done for each phase in [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md).

```text
Phase 0 — Foundation                 (repo, packaging, CI skeleton)
Phase 1 — Core Domain + Extraction   (data model, PyMuPDF spike — the make-or-break phase)
Phase 2 — Detection Algorithm        (the actual overlap/paint-order logic)
Phase 3 — CLI + JSON Report
Phase 4 — HTML Report
Phase 5 — Fixture-Based Hardening    (edge cases: rotation, XObjects, transparency)
Phase 6 — Packaging & Demo Polish
```

## 7. Success Criteria

- Running the tool against a self-made fake-redacted PDF correctly extracts and prints the hidden text, with **zero false positives** on a self-made properly-redacted PDF and a self-made clean PDF.
- A stranger can `pip install`, run one command against their own PDF, and understand the result without reading documentation.
- The full live demo (redact on stage → tool reveals the text → redo it properly → tool confirms clean) runs end-to-end from a single terminal in under 3 minutes.

## 8. Major Risks

| Risk | Mitigation |
|---|---|
| PyMuPDF's paint-order data might not cleanly indicate "was this shape drawn after this text" across every PDF producer (Word, Google Docs, Acrobat, LibreOffice, Preview.app all write PDFs differently) | Spike this first, on day 1, against 5+ real producer outputs before writing any detection logic. This is the single point of failure for the whole project. `VALIDATION REQUIRED`. |
| False positives on legitimate design elements (colored table cells, text highlights, decorative bars) that happen to sit under/over text | Constrain "redaction candidate" shapes to solid, near-black/near-white fills above a minimum size; tune thresholds against real-world fixtures, not only synthetic ones. |
| Scope creep toward OCR, image forensics, or a full GUI | Explicitly excluded from MVP (see Non-Goals) — either would roughly double the timeline for marginal demo value. |
