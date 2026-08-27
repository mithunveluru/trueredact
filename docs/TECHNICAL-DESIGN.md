# TrueRedact — Technical Design

> Implementation-level reference. See [ARCHITECTURE.md](./ARCHITECTURE.md) for the component-level view and [DECISIONS.md](./DECISIONS.md) for why key choices were made.

## Repository Structure

```text
trueredact/
├── docs/                        # this blueprint
├── packaging/trueredact.svg     # desktop-entry icon
├── src/trueredact/
│   ├── __init__.py
│   ├── cli.py                   # argparse entry point + orchestration
│   ├── web.py                   # loopback drag-and-drop UI, stdlib only
│   └── core/
│       ├── __init__.py
│       ├── models.py            # TextSpan, ShapeObject, Finding, ScanReport
│       ├── loader.py            # open + validate PDF, enforce caps
│       ├── extractor.py         # content-stream -> TextSpan/ShapeObject
│       ├── detector.py          # pure overlap-detection algorithm
│       ├── report_json.py
│       └── report_html.py
├── tests/
│   ├── fixtures/
│   │   └── generate_fixtures.py # reproducibly builds all test PDFs
│   ├── test_loader.py
│   ├── test_extractor.py
│   ├── test_detector.py         # most important test file in the repo
│   ├── test_report_json.py
│   ├── test_report_html.py
│   ├── test_cli.py
│   ├── test_web.py
│   ├── test_edge_cases.py
│   └── test_pipeline_integration.py
├── .github/workflows/ci.yml
├── demo.sh
├── install.sh
├── pyproject.toml
├── README.md
├── SECURITY.md
└── LICENSE
```

**Module boundary rule:** `core/detector.py` never imports PyMuPDF and never performs I/O. It operates purely on `TextSpan`/`ShapeObject` lists already extracted by `core/extractor.py`. This is what makes the highest-risk logic in the project testable with plain Python objects, independent of PDF parsing correctness.

## Domain Model

Implemented in `core/models.py`, which is the authority — the field-level definitions live there with the reasoning attached, and are deliberately not duplicated here where they would drift. Shape revised against spike evidence; see [spike-notes.md](./spike-notes.md) for the measurements behind each change.

| Type | Carries |
|---|---|
| `BBox` | `(x0, y0, x1, y1)`, normalized, in unrotated CropBox-relative page space |
| `TextSpan` | bbox, text, `paint_order` (PyMuPDF `seqno`), `render_mode` (3 = invisible but extractable), opacity |
| `ShapeObject` | bbox, `fill_color` (`None` if unfilled), `alpha` (`None`, not `1.0`, when there is no fill), `paint_order` |
| `ImageBox` | bbox only — **no paint order**, see the `KNOWN LIMITATION` below |
| `PageContent` | page number, spans, shapes, images, `/Redact` boxes, and `error` when extraction failed |
| `Verdict` | `FAKE_REDACTION` / `CLEAN` / `UNCERTAIN` |
| `CoveredSpan` | text, bbox, coverage ratio, paint order, render mode |
| `Finding` | page number, verdict, reason, the implicated `ShapeObject` if any, and the covered spans |
| `ScanReport` | file path, page count, findings, `generated_at` |

Two fields the original design specified are **absent**: `Finding.confidence` (an uncalibrated 0–1 number reading as a probability) and `Finding.region` (superseded by `shape.bbox`). `PageContent.width`/`height` were added and then deleted, unconsumed. Each removal is recorded in [DECISIONS.md](./DECISIONS.md).

~~`ASSUMPTION`~~ → **VALIDATED (Phase 1 spike).** PyMuPDF's `seqno` is a single page-local counter shared by `get_texttrace()` and `get_drawings()`, reproducing content-stream order exactly. Measured across 4,397 pages / 187 files / 33 producers: 11 pages (0.25%) show a text↔shape `seqno` collision, of which only 3 (0.07%) involve a *filled* shape — all 15–51 pt², far below `CANDIDATE_MIN_AREA`. The content-stream-parsing fallback is **not needed**. Full evidence in [spike-notes.md](./spike-notes.md).

`KNOWN LIMITATION`: **images carry no usable paint order.** `get_image_info()['number']` is an index, not a sequence number, and collides with real `seqno` values on the majority of real-world pages with images. So for a text span substantially covered by an image we cannot tell which is on top. Phase 2 must report that as `UNCERTAIN`, never `CLEAN`. Recovering true image order means parsing `Do` operators from the raw content stream — bounded work, deliberately outside MVP.

## "Database" — Report Schema (not a persistent store)

There is no database in MVP (see [ARCHITECTURE.md § Database](./ARCHITECTURE.md#database)). The closest thing to a schema is the on-disk JSON report format, which is the actual data contract other tools/scripts would integrate against.

### JSON Report Schema

Every page contributes at least one finding, including clean ones, so the report is
a complete record of what was checked rather than only what was wrong.

```json
{
  "file_path": "string",
  "page_count": 3,
  "generated_at": "2026-08-08T00:00:00Z",
  "has_leak": true,
  "summary": { "clean": 2, "fake_redaction": 1, "uncertain": 0 },
  "findings": [
    {
      "page_number": 1,
      "verdict": "fake_redaction",
      "reason": "1 text span(s) painted before an opaque shape (fill rgb(0.00, 0.00, 0.00), opacity 1.00) that covers 100%-100% of each, and whose text is still extractable",
      "recovered_text": "John Smith, SSN 000-00-0000",
      "shape": {
        "bbox": [72.0, 640.0, 300.0, 660.0],
        "fill_color": [0.0, 0.0, 0.0],
        "alpha": 1.0,
        "paint_order": 12
      },
      "covered_spans": [
        {
          "text": "John Smith, SSN 000-00-0000",
          "bbox": [74.0, 642.0, 280.0, 658.0],
          "coverage": 1.0,
          "paint_order": 7,
          "render_mode": 0
        }
      ]
    }
  ]
}
```

`shape` and `covered_spans` are `null` / `[]` on findings with no implicated shape
(an unreadable page, a page with no text layer). There is no `confidence` field —
the measurements it would have been derived from are published directly instead.
`shape.bbox` is the flagged region, so the old separate `region` key is gone.

**All `bbox` values are in unrotated, CropBox-relative page space**, matching how
PyMuPDF reports both text and drawings — measured from the crop origin, not the
media one, and ignoring `/Rotate`. A consumer drawing these onto a *rendered* page
image must first apply the page rotation —
`pymupdf.Rect(*bbox) * page.rotation_matrix` — or the rectangle will land in the
wrong place on any page with `/Rotate` set. This is what `core/report_html.py`
does; see `_overlay_style`.

### Future Scope (explicitly deferred)

If batch history becomes a real requirement, a single local SQLite table is the right future shape — not designed in detail now, since designing it before there's a concrete consumer is guessing:

```sql
-- FUTURE, not part of MVP:
CREATE TABLE scans (
    id INTEGER PRIMARY KEY,
    file_hash TEXT NOT NULL,
    file_path TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    has_leak BOOLEAN NOT NULL,
    findings_json TEXT NOT NULL
);
```

## Interface Contract

### CLI

```text
trueredact scan <file.pdf>
    [--json <path>]              write JSON report
    [--html <path>]              write HTML report
    [--max-pages N]               default 500
    [--max-file-size-mb N]        default 100
    [-v | --verbose]

Exit codes:
    0   scan completed, no fake redaction found, every page audited
    1   scan completed, at least one fake redaction found
    2   scan could not run (bad input, unreadable file, unwritable report)
        — but a leak already found still exits 1, since the verdict is printed
          before any report is written and must not be buried by a failed write
    3   scan completed, no fake redaction found, but some pages could not be audited
```

Exit `3` is an addition to the original three-code scheme. Collapsing "nothing suspicious" and "we could not check some of this" into `0` would report a scanned, unauditable document as all-clear — the false assurance this tool exists to prevent, and a requirement the original scheme could not express. Roughly 19% of pages in the real-document corpus are unauditable (scans with no text layer), so this is a common case, not a corner. See [DECISIONS.md](./DECISIONS.md).

### Local UI — built, and not as sketched

`trueredact ui` binds `http.server` to `127.0.0.1` on an ephemeral port and serves one page plus `POST /scan`. Three details differ from the deferred sketch, each for a measured reason:

- **stdlib, not Flask.** One page and one endpoint do not justify doubling the dependency tree of a security tool.
- **Raw bytes, not `multipart/form-data`.** Parsing multipart would need `cgi.FieldStorage`, removed in Python 3.13, which CI tests. Raw bytes have no parsing surface at all.
- **It does have authentication.** "Nothing to authenticate on a single-user local tool" was wrong: a loopback socket a browser can reach is reachable by every page in that browser. A random per-run token guards every `POST`, alongside a `Host` check.

The response is the plain-English summary plus the full HTML report, not the JSON schema above.

## Core Algorithm

**Problem:** given a page's text spans and shape objects (each with a bounding box and paint-order index), determine which shapes are plausible "redaction cover" attempts, and for each, whether a text span sits beneath it and remains extractable.

**Input:** one `PageContent`. **Output:** `list[Finding]`, always at least one.

Implemented in `core/detector.py`. Two rules below exist because the first working
version produced 1,598 false positives on 4,397 real pages — see
[spike-notes.md § Phase 2 Addendum](./spike-notes.md).

```text
CANDIDATE_MIN_AREA = 200          # pt², filters out rules, underlines, glyph fragments
COVERAGE_THRESHOLD = 0.85         # fraction of text bbox that must be covered
OPACITY_THRESHOLD = 0.5           # alpha at/above which a shape counts as a cover

function is_redaction_candidate(shape):
    return shape.fill_color is not None
       and (shape.alpha is None or shape.alpha >= OPACITY_THRESHOLD)   # None => PDF default 1.0
       and area(shape.bbox) >= CANDIDATE_MIN_AREA
    # NB: fill *colour* is not tested. Paint order plus coverage already establish
    # that the text is hidden; a dark-blue box leaks exactly as much as a black one.
    # The fill is reported as evidence rather than used as a filter.

function detect_page(page):
    if page.error:  return [UNCERTAIN, "page could not be analysed"]
    if no spans:    return [UNCERTAIN if page.images else CLEAN]   # no text layer to audit

    findings = []
    for shape in filter(page.shapes, is_redaction_candidate):
        hidden, ambiguous = [], []
        for span in page.spans:
            if coverage(span.bbox, shape.bbox) < COVERAGE_THRESHOLD:  continue
            if span.paint_order < shape.paint_order:
                if repainted_after(span, shape, page):  continue   # slide build, still visible
                hidden.append(span)
            else if span.paint_order == shape.paint_order:
                ambiguous.append(span)                             # order unknowable
        if hidden:         findings.append(FAKE_REDACTION with shape + covered spans)
        else if ambiguous: findings.append(UNCERTAIN, "paint order cannot be established")

    for image in page.images:                       # images carry no paint order
        if any span not already accounted for is covered:
            findings.append(UNCERTAIN, "text under an image; order unrecoverable")

    return findings or [CLEAN]

function repainted_after(span, shape, page):
    # Presentation software paints a state, wipes it opaque, paints the next state.
    # The buried copy is covered and extractable, but the identical text is visible
    # on the page, so nothing is hidden.
    return any(other.paint_order > shape.paint_order
               and other.text.strip() == span.text.strip()
               for other in page.spans)
```

There is no `confidence_score`. Each finding carries the measurements it was derived from — per-span coverage ratio, the covering shape's bbox, fill colour, fill opacity, and both paint-order indices — rather than collapsing them into an uncalibrated 0..1 number. See [DECISIONS.md](./DECISIONS.md).

**What "shape" means here:** a solid filled rectangle *as actually painted* — clip-intersected, taken from the path's own `re` items, excluding frames and strokes. Establishing that is the extractor's job; see [DECISIONS.md](./DECISIONS.md) and `core/extractor.py`.

**Complexity:** O(shapes × spans) per page, and `repainted_after` adds a further O(spans) in the covered branch only. Trivially fast at real-world page scale; measured ~15 ms/page over a 4,397-page corpus.

**Edge cases:**
- *Rotated pages (`/Rotate` 90/180/270):* **no handling required.** Measured: `get_texttrace()` and `get_drawings()` both report unrotated, CropBox-relative coordinates, byte-identical across all four rotations, so text and shapes are already in one comparable space. `/Rotate` matters only when painting a bbox onto a rendered pixmap for the HTML report (Phase 4), via `page.rotation_matrix`. Locked by a regression test.
- *Nested form XObjects:* **no handling required.** Measured: MuPDF flattens form XObjects, emitting their content already in outer-page coordinates with `seqno` correctly interleaved into the page's single sequence. There is no nesting for either layer to compose. Locked by a regression test.
- *Text or shapes covered by a raster image:* image paint order is not recoverable (see the `KNOWN LIMITATION` under Domain Model) — must yield `UNCERTAIN`, not `CLEAN`.
- *`seqno` tie between a candidate shape and a covered span:* order is genuinely ambiguous, so the verdict must be `UNCERTAIN`. Comparison uses strict `<`; an ambiguous order must never produce an accusation.
- *Semi-transparent shapes over text:* `IMPLEMENTATION DECISION` — still counted as `is_redaction_candidate` down to `OPACITY_THRESHOLD = 0.5`, since a semi-transparent "redaction" is arguably a worse failure (visually leaky *and* programmatically extractable), not a non-issue. Revisit the threshold after Phase 5 fixture testing.
- *Text repainted after its cover* (presentation slide builds): not a leak — the identical text is visible on the page. See [DECISIONS.md](./DECISIONS.md).
- *A path carrying more than 200 rectangles:* `KNOWN LIMITATION` — skipped as artwork. The frame rule is pairwise within a path, so an uncapped scan is quadratic on a file that passes every size cap. A redaction is one rectangle drawn on its own; a path with hundreds is a heatmap or a shaded table.
- *Non-rectangular filled paths:* `KNOWN LIMITATION` — a redaction drawn as a polygon or rounded blob is not detected. Only `re` path items are considered; admitting arbitrary path bounding boxes caused 1,598 false positives. **This includes rectangles painted under a rotating or skewing `cm` transform**, which MuPDF reports as four line segments rather than an `re`.
- *Corrupted content stream:* MuPDF recovers without raising and returns whatever it salvaged, so a mangled page can extract as empty. Detected from MuPDF's **error channel** (`fz_set_error_callback`) rather than from message wording, and reported `UNCERTAIN`; anything salvaged is still analysed, so a leak on such a page is reported *alongside* the uncertainty rather than replaced by it. An earlier version substring-matched the warning text, which would have broken silently on any upstream rewording — see [DECISIONS.md](./DECISIONS.md).
- *CropBox smaller than MediaBox:* coordinates are reported relative to the crop origin. Page dimensions come from the CropBox; no other handling is required, since spans and shapes shift together.
- *Annotation-based redaction* (PDF `/Redact` or `/Square` annotations rather than content-stream drawing operators): **handled, post-MVP.** `/Square` needs nothing — MuPDF flattens an annotation's appearance stream into `get_drawings()` with a correct `seqno`, so those covers arrive as ordinary shapes. `/Redact` needs its own path because an unapplied mark paints nothing at all: its rectangles are extracted into `PageContent.redactions`, carrying no fill and no paint order. See DECISIONS.md. Other annotation types (`/Stamp`, opaque `/FreeText`) remain unexamined.
- *No text layer at all (scanned image page):* the detector cannot distinguish "properly redacted" from "never had extractable text." This must surface as `Verdict.UNCERTAIN`, never `CLEAN` — reporting "clean" on a page we didn't actually check would be a false assurance, arguably worse than no tool at all.

### Future Scope (explicitly deferred)

- ~~**Annotation-based redaction detection**~~ — built after the MVP, and smaller than this entry assumed: `/Square` was already covered by the shape path, so only `/Redact` needed the `/Annots` walk. See DECISIONS.md.
- **Incremental-update byte forensics** — a PDF can be saved via incremental update, leaving prior object revisions (potentially the un-redacted original) physically present in the file bytes even when not referenced by the current xref table. Detecting this means scanning raw file bytes for orphaned prior `xref`/`trailer` sections — a distinct, self-contained algorithm from the content-stream overlap check above, worth its own phase if pursued.

## AI/ML

**Not applicable — by design, not by omission.** No component in this pipeline is a trained model or makes a probabilistic judgment call about pixels or semantics. Every `Finding` traces back to an exact, reproducible structural fact (bounding-box overlap + paint order) that a developer can verify by hand against the PDF's raw object structure. See [DECISIONS.md](./DECISIONS.md#decision-no-aiml-anywhere-in-the-pipeline) for why this boundary is deliberate and non-negotiable for this project.

## Concurrency, Caching, Background Jobs

**Not applicable for MVP.** Single file, single process, synchronous, page-by-page — the workload is too small (tens of pages) to justify concurrency, and there is nothing to cache (each invocation is independent, stateless). `IMPLEMENTATION DECISION`, future: if batch/folder scanning is added, per-file parallelism (not per-page) would be the natural first optimization, using a plain `multiprocessing.Pool` — not before there's a real multi-file use case.

## Configuration

No environment variables and no config file in MVP — there are no secrets, no external services, and no per-environment behavior to configure. All tunables are CLI flags with sane defaults (`--max-pages`, `--max-file-size-mb`; `COVERAGE_THRESHOLD`/`OPACITY_THRESHOLD`/`CANDIDATE_MIN_AREA` are module-level constants, not exposed as flags until a real need to tune them per-invocation appears — inventing that flexibility now would be speculative).

## External Integrations

None. No network calls anywhere in the runtime pipeline (see [DECISIONS.md](./DECISIONS.md#decision-no-network-io-anywhere-in-the-pipeline)).

## Security-Relevant Design

This tool's core purpose is parsing untrusted, potentially adversarial PDF files — that surface must be treated carefully even though there is no outbound network exposure:

- **Resource caps before parsing:** file-size and page-count checked from the file header/metadata *before* PyMuPDF does real work. These bound input size, not work: a 29 KB page carrying 15,000 rectangles in one path passes both and took 44 s against the pairwise frame rule. Per-path rectangle count is therefore capped as well (`_MAX_RECTS_PER_PATH`), which makes per-page cost linear in the rectangles actually drawn.
- **No active content execution:** the tool never evaluates embedded JavaScript, form actions, or launch actions — it only reads passive text/vector geometry.
- **Per-page fault isolation:** a malformed content stream on one page is contained in that page's `PageContent.error`; it must not crash the whole scan (also a correctness requirement, not just security).
- **Dependency hygiene:** PyMuPDF version pinned in `pyproject.toml`; `pip-audit` run in CI to catch known vulnerabilities in the dependency tree.
- **No telemetry, no outbound calls** — the tool never initiates a connection to anything. `scan` opens no socket at all; `ui` binds one listening socket to `127.0.0.1` so a local browser can reach it, and even then nothing is sent outward.
- **Local UI hardening:** loopback bind, `Host` header check against DNS rebinding, a random per-run token on `POST /scan`, size cap enforced from `Content-Length` before the body is read, uploads written to a private temp dir that is deleted afterwards, and `Content-Security-Policy: default-src 'none'`. See [DECISIONS.md](./DECISIONS.md).

## Error Handling

| Failure | Detection | Recovery | User Impact |
|---|---|---|---|
| Non-PDF file (wrong magic bytes) | Header check in `loader.py`, before PyMuPDF is invoked | Abort with typed `LoadError` | Exit 2, clear message: "not a valid PDF file" |
| Encrypted/password-protected PDF | PyMuPDF open-time flag | Abort with typed `LoadError` | Exit 2, message: "file is encrypted — cannot audit" |
| Oversized file / too many pages | Size/page check before parsing | Abort with typed `LoadError` | Exit 2, message stating the configured limit and how to raise it via flags |
| Malformed content stream on one page | Exception caught per-page in `extractor.py`, plus MuPDF's error channel | Keep whatever was salvaged and set `PageContent.error`; nothing is logged, the fault travels in the data | Scan completes; the page is reported `UNCERTAIN`, never silently `CLEAN`, and any leak found on it is reported too |
| No text layer at all on a page (scanned image) | Zero text spans extracted for that page | Emit `Verdict.UNCERTAIN` for that page, not `CLEAN` | User is told the page couldn't be audited, not falsely reassured |
| Unexpected internal exception during load/detect | Top-level `try/except` in `cli.py` | Print a clean error message + the exception type (not a raw traceback by default; full traceback only under `-v`) | Exit 2 |
| Report cannot be written (bad path, unrenderable page) | `_write_report` in `cli.py` | Summary is already printed; the failure is named on stderr and the scan's own verdict stands | Exit 1 if a leak was found, otherwise 2 |
| Page too large for MuPDF to rasterize | `try/except` in `report_html._preview_png` | Card is rendered without a preview; recovered text and spans table are the evidence | Finding is reported in full, minus the image |

Retry logic is explicitly not applicable — every operation here is local, synchronous, and deterministic; retrying a parse failure would just reproduce the same failure.

## Future Scope (explicitly deferred) — consolidated

- ~~Local web UI (drag-and-drop, localhost-only, no auth).~~ Built — and it does
  have auth: a per-session token, plus a `Host` check, because a loopback socket a
  browser can reach is reachable by every page in that browser.
- SQLite-backed batch scan history.
- ~~Annotation-based redaction detection.~~ Built.
- Incremental-update (leftover-bytes) forensics.
- Folder/batch scanning with per-file parallelism.
- PyPI publishing / signed releases.
