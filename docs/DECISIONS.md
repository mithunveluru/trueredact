# Redaction X-Ray — Architecture Decision Records

Only decisions with real, debatable alternatives are recorded here. Trivial choices (test runner, `.gitignore` contents) are omitted deliberately.

---

## Decision: PyMuPDF as the PDF engine

### Context
The entire project depends on extracting two things from a PDF, in a comparable coordinate space and comparable draw-order: text runs, and vector/shape objects. Most PDF libraries do one or the other well, not both.

### Decision
Use PyMuPDF, a Python binding to the MuPDF C library. Pinned to `pymupdf==1.28.2`; import it as `pymupdf`, not the deprecated `fitz` alias.

### Alternatives
- **pikepdf** — excellent for low-level PDF object manipulation and structural edits, but has no built-in text-layout extraction; would require hand-parsing content stream operators just to get text spans.
- **pdfplumber** — good text/table extraction, but weaker access to raw vector drawing objects and paint order.
- **pdf.js (Node)** — would work, but moves the whole project off Python for no offsetting benefit, and its drawing-object introspection API is less direct than PyMuPDF's `get_drawings()`.
- **Hand-rolled content-stream parser** — full control, but reimplements a significant chunk of a PDF interpreter for a 4–6 day project; only justified if PyMuPDF's paint-order data proves unusable (see the Phase 1 spike in [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md#phase-1--core-domain--extraction-spike)).

### Reason
PyMuPDF is the only option that gives both text spans and shape objects, each with bounding boxes and an inferable paint/draw order, through one well-documented, actively maintained API — directly matching what the core algorithm needs.

### Trade-off
Dependency on MuPDF's interpretation of paint order being consistent enough across PDF producers to be trustworthy — this is exactly what Phase 1's spike exists to validate before the rest of the project is built on top of it.

---

## Decision: paint order comes from PyMuPDF's `seqno`, not a hand-rolled content-stream parser

### Context
The whole project rests on answering "was this shape drawn *after* this text?". The plan flagged this `VALIDATION REQUIRED` and reserved a fallback: parse the raw content-stream operator sequence ourselves.

### Decision
Use `seqno` from `page.get_texttrace()` and `page.get_drawings()` directly. No fallback parser. Text comes from `get_texttrace()` rather than `get_text("dict")`, because it is the only text API that carries `seqno`.

### Alternatives
- Hand-rolled content-stream operator parser (the reserved fallback) — full control, but reimplements a chunk of a PDF interpreter.
- `get_text("dict")` for text — richer layout structure, but no sequence number, which makes it useless here. It also applies `/Rotate` while `get_drawings()` does not, so mixing the two would silently mismatch coordinate spaces on rotated pages.

### Reason
Measured, not assumed. `seqno` is one page-local counter shared across both APIs and reproduces content-stream order exactly. Across 4,397 pages / 187 files / 33 producers (Word 2010–2024, Google Docs, LibreOffice, macOS Quartz, Acrobat Distiller, pdfTeX, ReportLab, iText, FPDF, TCPDF, Ghostscript, Aspose): 0.25% of pages showed any text↔shape collision, and only 0.07% involved a filled shape — all of them 15–51 pt² vector-traced glyph fragments, an order of magnitude below the 200 pt² candidate floor. Evidence in [spike-notes.md](./spike-notes.md). Writing a PDF interpreter to solve a 0.07% problem that the area filter already excludes would be unjustifiable.

### Trade-off
We inherit MuPDF's interpretation of ordering. The residual ambiguity is handled rather than ignored: a `seqno` tie on a genuine candidate yields `UNCERTAIN`, never `FAKE_REDACTION`. The regression tests for rotation and XObject flattening exist to fail loudly if a future MuPDF changes these semantics.

---

## Decision: raster-image covers are reported as UNCERTAIN, not detected

### Context
Text can also be hidden under an opaque *image* (e.g. a pasted black rectangle screenshot) rather than a vector shape. The original domain model anticipated this with `ShapeObject.shape_type = "image"`.

### Decision
Extract image bounding boxes, but carry no paint order for them. A text span substantially covered by an image yields `UNCERTAIN` with an explicit reason. Image-cover detection proper is deferred.

### Alternatives
- Treat `get_image_info()['number']` as a paint order — **measured wrong**: given ground-truth order `img, text, rect, img, text`, the second image is reported as `2` when it sits at position `3`, colliding with the rect. It collides with real `seqno` values on most real-world pages containing images (e.g. 216 of 263 Word 2016 pages).
- Parse `Do` operators from the raw content stream to recover true image order — correct, but it is the content-stream parser we just declined to write.
- Ignore images entirely — would report `CLEAN` on a page whose secret is sitting under an image.

### Reason
Of the three, only `UNCERTAIN` is honest. Silently reporting `CLEAN` on an undetectable case is the specific failure this tool exists to prevent — a false assurance is worse than no answer. Gating it on *substantial* coverage keeps it from firing on ordinary logos and figures, which would otherwise make every real document `UNCERTAIN` and the tool useless.

### Trade-off
A genuine image-based fake redaction is reported as "cannot determine" rather than "leak found", so the user must check it by hand. Accepted for MVP: the dominant real-world failure mode is a vector rectangle, and `Do`-operator parsing is a self-contained future addition.

---

## Decision: CLI-first, no GUI or web server in MVP

### Context
The original proposition's demo works entirely from a terminal. A GUI or local web server would improve accessibility for non-technical users but is a second, separate surface to build and test.

### Decision
Ship a CLI only for MVP. A local web UI is explicit Future scope.

### Alternatives
- A Flask-based local web UI from day one.
- A native desktop GUI (e.g. via a Python GUI toolkit).

### Reason
The CLI alone fully demonstrates the core value (detection + recovered text + reports) and is achievable within the 4–6 day budget alongside the algorithm work, which is where the real risk and value both live. A GUI adds surface area without adding to what's actually being proven.

### Trade-off
Non-technical users can't use the MVP without terminal comfort — acceptable, since the HTML report (which *is* built) is the artifact meant for non-technical consumption; the CLI is the producer, not the primary audience-facing surface.

---

## Decision: a fourth exit code for "completed, but not everything could be audited"

### Context
The planned CLI contract was `0` no leak, `1` leak, `2` error. But the detector has three verdicts, and `UNCERTAIN` fits neither `0` nor `2`: the scan ran fine, so it is not an error, yet some pages were not actually checked.

### Decision
Add exit code `3`: scan completed, no leak found, but at least one page could not be audited. `0` now means "no leak found *and* every page was audited".

### Alternatives
- Map `UNCERTAIN` to `0` (the literal original contract).
- Map `UNCERTAIN` to `2` — conflates a working scan with a failed one, making a partially-audited document indistinguishable from an unreadable file.
- Add a `--strict` flag that turns uncertainty into a failure — a flag to opt into being told the truth is backwards.

### Reason
Mapping `UNCERTAIN` to `0` means a scanned, text-layer-free PDF exits "all clear". That is precisely the false assurance the project is built to prevent, and the requirement to keep "no suspicious evidence found" distinct from "could not reliably analyze the document" cannot be expressed in three codes. Not a corner case: about 19% of pages in the 4,481-page real corpus are unauditable scans. In CI the useful shape is "fail on 1, warn on 3", which needs them separated.

### Trade-off
A deviation from the documented contract: a script written against the original scheme that treats non-zero as failure will now also fail on `3`. Judged the right direction — that script failing loudly on an unauditable document is correct behaviour, not a regression.

---

## Decision: unreliable pages are detected from MuPDF's error *channel*, not its message text

### Context
MuPDF recovers from a corrupted content stream without raising: it returns whatever it salvaged and reports the problem out-of-band. A mangled page therefore extracted as empty and was classified `CLEAN` — a page nobody could read, reported as containing nothing. The worst failure this tool can have.

An earlier implementation detected this by substring-matching MuPDF's warning text for `syntax error` and `page may not be correct`. That worked, but it made correctness depend on another library's wording: a reworded message would have silently stopped flagging corrupt pages and reinstated the bug, with no test failure anywhere until someone bumped the pin.

### Decision
Register a callback on MuPDF's **error** channel (`fz_set_error_callback`) and treat *any* error raised while extracting a page as "this page did not fully parse". Message text is carried into the finding for the reader's benefit but plays no part in the decision. Salvaged spans and shapes are kept, not discarded.

Registration failure is fatal: if a PyMuPDF build does not expose the callback, the extractor raises at import rather than run without the ability to distinguish a damaged page from an empty one.

### Alternatives
- **Substring-match the warning text** (the previous implementation). Correct today, silently wrong after any upstream rewording.
- **Treat any MuPDF *warning* as failure.** Rejected on measurement: the warning channel fires on 144 of 4,419 pages (3.26%), almost all benign font noise (`FT_Get_Advance(...): invalid glyph index`) affecting neither geometry nor text. This would manufacture uncertainty on one page in thirty.
- **Compare content-stream byte length against what was extracted.** A heuristic with no principled threshold; legitimately sparse pages exist.
- **Discard salvaged content and report only `UNCERTAIN`.** Would hide a genuine, fully-evidenced leak found on a partially-parsed page.

### Reason
MuPDF already separates errors from warnings, and that split is exactly the distinction needed — a content-stream parse failure is an error, a missing glyph advance is a warning. Using the channel rather than the wording makes the check independent of message phrasing entirely, so there is nothing left to silently reword.

Measured across the 4,419-page corpus: the **error** channel fires on **1 page (0.02%)**, the warning channel on 144 (3.26%). Re-running the full corpus changed the verdict of exactly one page — a `format error: cmsOpenProfileFromMem failed` that the old text-matching missed and that is legitimately "we could not fully process this". Findings were otherwise unchanged: 10 leaks across the same 4 documents.

### Trade-off
Any MuPDF error now marks a page unreliable, including ones that may not affect text or geometry (the colour-profile case above). At 0.02% of real pages that is a negligible cost, and erring toward "we could not fully read this" is the correct direction. The callback is process-global — see the next decision.

---

## Decision: the diagnostic capture window is serialized with a lock

### Context
MuPDF's error callback is process-global. Extraction reads it as a window — clear, extract one page, collect — and two threads extracting concurrently would interleave those windows: one page's capture clears another's buffer mid-extraction, so a parse failure is attributed to the wrong page or dropped entirely. Dropping it silently restores "corrupt page reported CLEAN".

### Decision
A module-level `threading.Lock` is held across each page's capture window, in `_mupdf_errors()`.

### Alternatives
- **Document "single-threaded only" and move on** (the previous state). Leaves a correctness trap armed for whoever later calls this from a thread pool, and the failure is silent.
- **Thread-local error buffers.** The callback is registered globally in MuPDF; there is no per-thread channel to bind to, so this cannot be expressed.
- **A per-document MuPDF context per thread.** The proper upstream fix, far beyond MVP scope, and unnecessary for a single-file CLI.

### Reason
The lock is uncontended in the single-threaded CLI, so it costs nothing measurable, and it converts a latent silent-corruption bug into correct (if serialized) behaviour for any future concurrent caller. Cheap insurance against a failure mode that is invisible when it happens.

Verified by a test that forces the interleaving with events rather than hoping to observe a race: it passes with the lock and fails deterministically without it. An earlier version of that test drove real concurrent extraction and passed with the lock removed — it was measuring nothing, and was replaced.

### Trade-off
Extraction is serialized across threads, so page-level parallelism would gain nothing while this stands. Acceptable: the workload is milliseconds per page, and per-file parallelism (the natural first optimization if batch scanning ever arrives) is unaffected because each process has its own MuPDF context.

---

## Decision: No database or persistence layer in MVP

### Context
The default instinct for "serious" project blueprints is to include a database. This tool's actual operation is stateless: one file in, one report out.

### Decision
No database of any kind in MVP. Reports are files on disk; nothing is retained between invocations.

### Alternatives
- SQLite for scan history from the start.
- Postgres, for a hypothetical future multi-user service.

### Reason
There is no current requirement that needs persisted state across runs. Building storage before a real consumer of that storage exists means guessing at a schema that will likely be wrong once the actual requirement shows up.

### Trade-off
No "have I scanned this file before" or trend-over-time capability in MVP. Deferred to Future scope as a single SQLite table, to be designed against a real use case if/when it appears.

---

## Decision: No AI/ML anywhere in the pipeline

### Context
"Just point a vision model at the rendered page and ask if it looks redacted" is an obvious-seeming shortcut, and worth explicitly rejecting rather than silently avoiding.

### Decision
Zero AI/ML components. Detection is 100% deterministic structural analysis (bounding-box overlap + paint order).

### Alternatives
- A vision-language model classifying rendered page images for suspicious black boxes.
- An OCR-plus-heuristic pipeline to compare "visible" vs. "extractable" text.

### Reason
A structural, deterministic finding ("this exact text span, at this exact bounding box, sits beneath this exact shape in paint order") is independently verifiable by any developer who opens the PDF's raw object structure. A model's confidence score is not independently verifiable in the same way, and this is fundamentally a forensics tool — its output needs to be *provable*, not merely plausible. It's also simply unnecessary: the underlying signal is already exact and machine-readable in the file itself.

### Trade-off
The tool cannot handle scanned/image-only PDFs with no text layer — see [TECHNICAL-DESIGN.md § Edge Cases](./TECHNICAL-DESIGN.md#core-algorithm), reported as `UNCERTAIN`, not attempted via OCR. This is treated as a scope boundary, not a gap to patch with a model later.

---

## Decision: No network I/O anywhere in the pipeline

### Context
This tool exists specifically to audit sensitive, unreleased documents. Any telemetry, update-check, or cloud-processing call would undermine the tool's core trust proposition.

### Decision
The runtime binary makes zero network calls, ever. No telemetry, no crash reporting, no "check for updates," no cloud fallback for any feature.

### Alternatives
- Anonymous usage telemetry to guide roadmap decisions.
- Optional cloud-based processing for large files.

### Reason
The strongest, simplest claim this tool can make is "it is architecturally incapable of leaking your document," which only holds if there is genuinely no code path that opens a socket. Any exception, even an "opt-in" one, weakens that claim and adds a security-relevant code path to review.

### Trade-off
No usage data to inform future prioritization; users must self-report issues via GitHub. Acceptable for a solo open-source tool at this scale.

---

## Decision: no confidence score at all — report the measurements instead

*Supersedes the original "confidence scoring is a heuristic, not a learned classifier" decision, which specified a `confidence: float` on every `Finding`.*

### Context
The original design put a 0–1 `confidence` on each finding, a weighted heuristic over coverage ratio, opacity, and fill darkness, and the JSON schema advertised values like `0.94`.

### Decision
`Finding` has no `confidence` field. Each finding instead carries the exact quantities the verdict was computed from: per-span coverage ratio, the covering shape's bbox, fill colour, fill opacity, and both paint-order indices.

### Alternatives
- Keep the weighted heuristic (the original plan).
- Train a classifier on labelled fixtures (rejected earlier and still rejected — see "No AI/ML").

### Reason
Nothing calibrates the heuristic against outcomes, so `0.94` would read as a probability while being an arbitrary weighted sum. That is false precision in the one place this tool cannot afford it: it exists to produce *verifiable* evidence, and a number no one can verify undermines every number next to it. The inputs are already exact, already explainable, and already what a careful reader would check by hand — so publishing them directly is both more honest and less code. A reader who wants a ranking can sort by coverage.

### Trade-off
No single scalar to sort or threshold on, so consumers wanting "most suspicious first" must pick a field themselves. Cheap price: `FAKE_REDACTION` findings are meant to be confirmed by reading the recovered text, which is right there in the finding.

---

## Decision: a "shape" means a solid filled rectangle as actually painted

### Context
The first working detector was run against 4,397 real pages and produced **1,598 `FAKE_REDACTION` findings across 14 documents** — all false positives. Root cause: `get_drawings()['rect']` is a path's *bounding box*, which is not the region the path actually fills.

### Decision
The extractor emits a `ShapeObject` only for a solid filled rectangle, reduced to the region it genuinely paints:

1. **Clip-aware.** Read `get_drawings(extended=True)`, maintain the clip stack, and intersect every fill with the active clip.
2. **Rectangles only, from `items`.** Use the path's own `re` items rather than the path bbox; skip non-rectangular filled paths entirely.
3. **No frames.** Within one path, a rectangle that contains — or is contained by — another is part of a frame: the outer has a hole, and the inner *is* the hole. Both are dropped.
4. **No strokes.** An outline paints no region, so stroke-only paths are not shapes at all.

### Alternatives
- Keep using the path bbox and raise `CANDIDATE_MIN_AREA` — would trade one arbitrary constant against real detections, and does nothing about clipped fills.
- Implement full PDF fill-rule and path rasterization — correct, disproportionate, and a bug farm.
- Rasterize each candidate region and compare pixels — reintroduces exactly the pixel-guessing this tool rejects, and cannot distinguish "covered" from "covered then re-covered".

### Reason
Measured. Clipping alone explained a white rectangle whose bbox was 202% of the page area but which painted nothing near the text it appeared to cover; frame paths explained a further 663 findings in a single document. After both fixes the count fell from 1,598 to 44. Each rule is a statement about what "painted" means, which is squarely the extractor's job, so the detector stayed pure and unchanged.

### Trade-off
A redaction drawn as a non-rectangular filled path (a polygon or rounded blob) is not detected. Accepted: real redactions are rectangles, and admitting arbitrary path bboxes is precisely what produced 1,598 false positives. Recorded as a `KNOWN LIMITATION`.

---

## Decision: text repainted after its cover is not a leak

### Context
After the shape fixes, the largest remaining false-positive class was presentation slides: 44 findings, most from Keynote/PowerPoint exports.

### Decision
When a covered span's exact text (whitespace-stripped) is painted again on the same page at a *higher* paint order than the covering shape, it is not reported.

### Alternatives
- Report it anyway — technically true, practically wrong, and noisy enough to bury real findings.
- Compare rendered pixels — pixel-guessing, rejected.
- Suppress covers above some fraction of page area — arbitrary, and would miss genuine full-page redactions.

### Reason
Presentation software builds a slide by painting a state, wiping it with an opaque rectangle, and painting the next state on top. On Python-OOP p9 the span `'>>> '` is painted at order 7, covered by a white rect at order 58, and repainted at order 63. The buried copy is genuinely covered and genuinely extractable — but the identical text is plainly visible on the page, so nothing is hidden. Matching on exact text rather than geometry is deliberate: the rebuilt copy is usually nudged to a new position. This took findings from 44 to 10.

### Trade-off
A real redaction is missed if the same string also appears later on the same page — in which case that string is not secret on that page anyway. Errs toward a missed detection over a false accusation, which is the correct direction for a forensics tool.
