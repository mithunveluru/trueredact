# TrueRedact — Technical Design

> Implementation-level reference. See [ARCHITECTURE.md](./ARCHITECTURE.md) for the component-level view and [DECISIONS.md](./DECISIONS.md) for why key choices were made.

## Repository Structure

```text
trueredact/
├── docs/                        # this blueprint
├── src/trueredact/
│   ├── __init__.py
│   ├── cli.py                   # argparse entry point + orchestration
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
│   └── test_pipeline_integration.py
├── .github/workflows/ci.yml
├── pyproject.toml
├── README.md
└── LICENSE
```

**Module boundary rule:** `core/detector.py` never imports PyMuPDF and never performs I/O. It operates purely on `TextSpan`/`ShapeObject` lists already extracted by `core/extractor.py`. This is what makes the highest-risk logic in the project testable with plain Python objects, independent of PDF parsing correctness.

## Domain Model

Implemented in Phase 1 (`core/models.py`). Shape revised against spike evidence — see [spike-notes.md](./spike-notes.md) for the measurements behind each change.

```python
BBox = tuple[float, float, float, float]   # x0, y0, x1, y1; unrotated page space

@dataclass(frozen=True, slots=True)
class TextSpan:
    bbox: BBox
    text: str
    paint_order: int          # PyMuPDF seqno; comparable only within one page
    render_mode: int          # 0=fill/visible, 3=invisible-but-extractable
    opacity: float

@dataclass(frozen=True, slots=True)
class ShapeObject:
    bbox: BBox
    fill_color: tuple[float, float, float] | None   # RGB 0..1, None if unfilled
    alpha: float | None        # fill opacity; None (not 1.0) when there is no fill
    paint_order: int

@dataclass(frozen=True, slots=True)
class ImageBox:
    bbox: BBox                 # no paint_order — see KNOWN LIMITATION below

@dataclass(frozen=True, slots=True)
class PageContent:
    page_number: int
    width: float
    height: float
    spans: tuple[TextSpan, ...] = ()
    shapes: tuple[ShapeObject, ...] = ()
    images: tuple[ImageBox, ...] = ()
    error: str | None = None   # extraction failed; never report such a page CLEAN

# --- Phase 2/3, not yet implemented. `confidence` is an open question:
#     see DEVELOPMENT-PLAN.md, Phase 2. ---

class Verdict(str, Enum):
    FAKE_REDACTION = "fake_redaction"
    CLEAN = "clean"
    UNCERTAIN = "uncertain"     # e.g. no text layer at all (scanned image) — cannot assess

@dataclass(frozen=True)
class Finding:
    page_number: int
    verdict: Verdict
    region: tuple[float, float, float, float] | None   # bbox of the flagged shape, if any
    recovered_text: str | None
    confidence: float | None    # 0..1, only set for FAKE_REDACTION
    reason: str                 # human-readable explanation, always set

@dataclass(frozen=True)
class ScanReport:
    file_path: str
    page_count: int
    findings: list[Finding]
    generated_at: str           # ISO 8601

    @property
    def has_leak(self) -> bool:
        return any(f.verdict == Verdict.FAKE_REDACTION for f in self.findings)
```

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
    3   scan completed, no fake redaction found, but some pages could not be audited
```

Exit `3` is an addition to the original three-code scheme. Collapsing "nothing suspicious" and "we could not check some of this" into `0` would report a scanned, unauditable document as all-clear — the false assurance this tool exists to prevent, and a requirement the original scheme could not express. Roughly 19% of pages in the real-document corpus are unauditable (scans with no text layer), so this is a common case, not a corner. See [DECISIONS.md](./DECISIONS.md).

### Future Scope (explicitly deferred)

A local web UI, if built later, would be a single Flask process bound to `127.0.0.1` only, with exactly one endpoint (`POST /scan`, multipart file upload, returns the JSON schema above) and explicitly no authentication layer — there's nothing to authenticate on a single-user local tool.

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
- *Rotated pages (`/Rotate` 90/180/270):* **no handling required.** Measured: `get_texttrace()` and `get_drawings()` both report unrotated mediabox coordinates, byte-identical across all four rotations, so text and shapes are already in one comparable space. `/Rotate` matters only when painting a bbox onto a rendered pixmap for the HTML report (Phase 4), via `page.rotation_matrix`. Locked by a regression test.
- *Nested form XObjects:* **no handling required.** Measured: MuPDF flattens form XObjects, emitting their content already in outer-page coordinates with `seqno` correctly interleaved into the page's single sequence. There is no nesting for either layer to compose. Locked by a regression test.
- *Text or shapes covered by a raster image:* image paint order is not recoverable (see the `KNOWN LIMITATION` under Domain Model) — must yield `UNCERTAIN`, not `CLEAN`.
- *`seqno` tie between a candidate shape and a covered span:* order is genuinely ambiguous, so the verdict must be `UNCERTAIN`. Comparison uses strict `<`; an ambiguous order must never produce an accusation.
- *Semi-transparent shapes over text:* `IMPLEMENTATION DECISION` — still counted as `is_redaction_candidate` down to `OPACITY_THRESHOLD = 0.5`, since a semi-transparent "redaction" is arguably a worse failure (visually leaky *and* programmatically extractable), not a non-issue. Revisit the threshold after Phase 5 fixture testing.
- *Text repainted after its cover* (presentation slide builds): not a leak — the identical text is visible on the page. See [DECISIONS.md](./DECISIONS.md).
- *Non-rectangular filled paths:* `KNOWN LIMITATION` — a redaction drawn as a polygon or rounded blob is not detected. Only `re` path items are considered; admitting arbitrary path bounding boxes caused 1,598 false positives. **This includes rectangles painted under a rotating or skewing `cm` transform**, which MuPDF reports as four line segments rather than an `re`.
- *Corrupted content stream:* MuPDF recovers without raising and returns whatever it salvaged, so a mangled page can extract as empty. Detected via MuPDF's `syntax error` / `page may not be correct` warnings and reported `UNCERTAIN`; anything salvaged is still analysed, so a leak on such a page is reported *alongside* the uncertainty rather than replaced by it.
- *CropBox smaller than MediaBox:* coordinates are reported relative to the crop origin. Page dimensions come from the CropBox; no other handling is required, since spans and shapes shift together.
- *Annotation-based redaction* (PDF `/Redact` or `/Square` annotations rather than content-stream drawing operators): out of MVP scope — see Future Scope below. The detector only sees content-stream shapes.
- *No text layer at all (scanned image page):* the detector cannot distinguish "properly redacted" from "never had extractable text." This must surface as `Verdict.UNCERTAIN`, never `CLEAN` — reporting "clean" on a page we didn't actually check would be a false assurance, arguably worse than no tool at all.

### Future Scope (explicitly deferred)

- **Annotation-based redaction detection** — some producers apply redaction via a `/Redact` or `/Square` annotation object rather than a content-stream drawing; would require walking the page's `/Annots` array as a second candidate source.
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

This tool's core purpose is parsing untrusted, potentially adversarial PDF files — that surface must be treated carefully even though there is no network exposure:

- **Resource caps before parsing:** file-size and page-count checked from the file header/metadata *before* PyMuPDF does real work, to bound worst-case processing time/memory on a pathological input.
- **No active content execution:** the tool never evaluates embedded JavaScript, form actions, or launch actions — it only reads passive text/vector geometry.
- **Per-page fault isolation:** a malformed content stream on one page is caught and skipped with a warning; it must not crash the whole scan (also a correctness requirement, not just security).
- **Dependency hygiene:** PyMuPDF version pinned in `pyproject.toml`; `pip-audit` run in CI to catch known vulnerabilities in the dependency tree.
- **No telemetry, no network calls** — the strongest security property this tool has is architectural: it is physically incapable of leaking the document it's analyzing, because it never opens a socket.

## Error Handling

| Failure | Detection | Recovery | User Impact |
|---|---|---|---|
| Non-PDF file (wrong magic bytes) | Header check in `loader.py`, before PyMuPDF is invoked | Abort with typed `LoadError` | Exit 2, clear message: "not a valid PDF file" |
| Encrypted/password-protected PDF | PyMuPDF open-time flag | Abort with typed `LoadError` | Exit 2, message: "file is encrypted — cannot audit" |
| Oversized file / too many pages | Size/page check before parsing | Abort with typed `LoadError` | Exit 2, message stating the configured limit and how to raise it via flags |
| Malformed content stream on one page | Exception caught per-page in `extractor.py` | Skip that page, continue scan, log a warning | Scan completes; final report notes the skipped page explicitly (not silently) |
| No text layer at all on a page (scanned image) | Zero text spans extracted for that page | Emit `Verdict.UNCERTAIN` for that page, not `CLEAN` | User is told the page couldn't be audited, not falsely reassured |
| Unexpected internal exception | Top-level `try/except` in `cli.py` | Print a clean error message + the exception type (not a raw traceback by default; full traceback only under `-v`) | Exit 2 |

Retry logic is explicitly not applicable — every operation here is local, synchronous, and deterministic; retrying a parse failure would just reproduce the same failure.

## Future Scope (explicitly deferred) — consolidated

- Local web UI (drag-and-drop, localhost-only, no auth).
- SQLite-backed batch scan history.
- Annotation-based redaction detection.
- Incremental-update (leftover-bytes) forensics.
- Folder/batch scanning with per-file parallelism.
- PyPI publishing / signed releases.
