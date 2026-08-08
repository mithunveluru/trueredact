# Phase 1 Extraction Spike — Findings

**Question.** Does PyMuPDF expose a paint-order signal reliable enough to decide
"was this shape drawn *after* this text?" across real-world PDF producers?

**Answer: yes. GO.** Details below.

**Method.** Synthetic controlled PDFs for the ordering semantics, then a survey of
**4,397 pages across 187 real PDFs from 33 distinct producers** found on the
development machine — including Microsoft Word 2010/2013/2016/2019/2024/365,
Google Docs (Skia/PDF m117–m151), LibreOffice 24.2, macOS Quartz PDFContext
(10.6 / 12.3 / 14.5), Acrobat Distiller, Adobe PDF Library 11/15, pdfTeX,
ReportLab, iText 2/5, OpenPDF, FPDF, TCPDF, Ghostscript, Aspose.Words,
PowerPoint, and iLovePDF. This exceeds the plan's "5+ producers" bar.
Only aggregate structural statistics were read; no document content was inspected.

---

## VALIDATED — the core assumption holds

**`seqno` is a single, page-local sequence counter shared by `get_texttrace()`
and `get_drawings()`.** It reproduces content-stream order exactly:

| content stream | reported |
|---|---|
| text, rect, rect, text | TEXT seq=0, SHAPE seq=1, SHAPE seq=2, TEXT seq=3 |

It resets to 0 on each page, so ordering comparisons are only ever valid
*within* one page.

### Collision rate (the failure mode that would break detection)

| | pages |
|---|---|
| scanned | 4,397 |
| any text/shape `seqno` collision | 11 (0.25%) |
| collision involving a **filled** shape | **3 (0.07%)** |

Only filled shapes can ever be redaction candidates, so only the 3 matter. All
three are from one producer (iLovePDF), all are near-black filled shapes of
15–51 pt² — vector-traced glyph outlines, not redaction boxes. All fall far below
the planned `CANDIDATE_MIN_AREA = 200 pt²`, so the area filter already excludes
them before ordering is ever consulted.

`IMPLEMENTATION DECISION` (Phase 2): compare with strict `<`. A `seqno` tie on a
shape that *is* a candidate and *does* cover text should yield `UNCERTAIN`, never
`FAKE_REDACTION` — ambiguous order must not produce an accusation.

---

## The plan was wrong about two things — both delete work

### 1. Rotation needs no handling in the extractor

`TECHNICAL-DESIGN.md` states bounding boxes "must be computed in a consistent
page-space after applying the page rotation matrix — handled in `extractor.py`".

**Measured: false.** Both `get_texttrace()` and `get_drawings()` return
*unrotated* mediabox coordinates, identical across `/Rotate` 0/90/180/270:

```
ROTATED   0 | page.rect=(0,0,400,300)  TEXT (50.0, 50.6, 98.7, 62.6)  SHAPE (45,45,250,68)
ROTATED  90 | page.rect=(0,0,300,400)  TEXT (50.0, 50.6, 98.7, 62.6)  SHAPE (45,45,250,68)
ROTATED 180 | page.rect=(0,0,400,300)  TEXT (50.0, 50.6, 98.7, 62.6)  SHAPE (45,45,250,68)
ROTATED 270 | page.rect=(0,0,300,400)  TEXT (50.0, 50.6, 98.7, 62.6)  SHAPE (45,45,250,68)
```

Text and shapes land in the *same* coordinate space regardless of rotation, which
is all overlap detection requires. Applying a rotation matrix would be a no-op at
best and a bug at worst.

`/Rotate` matters only when mapping a bbox onto a **rendered pixmap** for the HTML
report — use `page.rotation_matrix` there (Phase 4), not in the extractor.

> Note: `page.get_text("dict")` *does* apply rotation. We do not use it (see below),
> so the two coordinate conventions never mix.

### 2. Nested form XObjects need no recursion

`ARCHITECTURE.md` specifies the extractor "walk[s] the content stream (composing
the page/XObject transform matrix as it recurses)".

**Measured: unnecessary.** MuPDF already flattens form XObjects. Text placed at
(20,50) inside an XObject drawn into `Rect(50,50,250,150)` is reported at
page-space (70.0, 90.6, 154.0, 102.6), with its `seqno` correctly interleaved
into the page's single sequence. There is nothing to recurse into.

---

## KNOWN LIMITATION — images cannot be paint-ordered

An opaque **image** pasted over text is a plausible fake-redaction, and
`get_image_info()` gives its bbox — but **not a usable paint order**.

Ground truth `img, text, rect, img, text` (positions 0–4) is reported as:

```
text  seqnos:  [1, 4]     correct
draw  seqnos:  [2]        correct
image numbers: [0, 2]     WRONG — the second image is at position 3, not 2
```

`get_image_info()['number']` collides with real `seqno` values on the majority of
real-world pages carrying images (e.g. 216 of 263 Word 2016 pages). It is an
index, not a sequence number.

**Consequence.** For a text span substantially covered by an image we cannot tell
whether the image is above or below it. Reporting `CLEAN` there would be a false
assurance. Phase 2 must emit `UNCERTAIN` for that case. Recovering true image
paint order would require parsing `Do` operators out of the raw content stream —
a bounded but real piece of work, deliberately not in MVP.

---

## Other measured facts that shaped the code

| Fact | Consequence |
|---|---|
| `get_texttrace()` yields text, bbox, `seqno`, `opacity`, render mode (`type`), color, and per-char bboxes | `get_text("dict")` is not needed anywhere — one API, and it is the only one carrying `seqno` |
| Stroke-only shapes report `fill=None` **and `fill_opacity=None`** (not `1.0`) | `ShapeObject.alpha` must be `float \| None`; the detector must not assume a float |
| Render mode 3 (invisible) text is still returned, with `type=3` | Invisible text under a box is still extractable — still a leak. Keep it, record the mode as evidence |
| `doc.metadata` can be `None` | Guard before `.get()` |
| Encrypted docs **open successfully**; they only raise `ValueError` at page load | The loader must check `needs_pass` / `is_encrypted` up front, not rely on catching later |
| `seqno` resets per page | Never compare paint order across pages |
| Empty page returns `[]` from both APIs, no exception | No special-casing needed |
| `apply_redactions()` genuinely removes the text object | Valid "properly redacted" fixture generator |

## Model changes justified by the above

- **Dropped `ShapeObject.shape_type`** (`"rect" | "path" | "image"`). Images are a
  separate list because they have no paint order; rect-vs-path is never consulted.
- **Dropped `page_number` from `TextSpan`/`ShapeObject`** — hoisted to the
  `PageContent` container that owns them, instead of repeated per object.
- **Added `PageContent.error`** so a page that failed extraction stays
  distinguishable from a page with nothing on it (`UNCERTAIN`, never `CLEAN`).

---

# Phase 2 Addendum — False-Positive Survey

The first working detector was run over the same 4,397-page corpus. Findings were
then ground-truthed by rendering each flagged region and checking whether the
supposedly-hidden text was actually visible. (Pixel analysis is barred from the
*product*; it is the right instrument for auditing the product.)

| Stage | FAKE_REDACTION findings | Documents |
|---|---|---|
| first working detector | 1,598 | 14 |
| after clip-aware + frame-aware extraction | 44 | 7 |
| after the repainted-text rule | **10** | **4** |

**All 10 survivors were verified structurally correct** — text painted, then
covered by an opaque rectangle painted later, with the text still extractable.
Zero confirmed algorithm false positives remain. What they are in practice:

- a `Redis` label and page numbers covered by coloured banners in lecture slides;
- `Photograph` / `Signature of the Candidate` placeholder labels on exam hall
  tickets, covered by a box and then a pasted photo.

These are benign in intent but are exactly the structural fact the tool is defined
to report. Deciding that a covered string is *unimportant* is a semantic judgement
the tool deliberately refuses to make — it reports the evidence and the recovered
text, and the human decides.

> One caveat on the render-based audit itself: an image painted over the region
> makes it non-uniform, so a covered label under a photo looks "visible" to the
> pixel check. Three findings initially read as false positives this way and were
> confirmed genuine once image coverage was checked. The instrument needed
> auditing too.

## KNOWN LIMITATION added in this phase

Redactions drawn as **non-rectangular filled paths** (polygons, rounded blobs) are
not detected. Only `re` items are considered, because admitting arbitrary path
bounding boxes is what produced the original 1,598 false positives.

---

# Phase 5 Addendum — Hardening Bug Hunt

Three suspected weak points were probed deliberately. Two were real bugs.

### BUG: a corrupted content stream was reported CLEAN

MuPDF **recovers from a mangled content stream without raising**. The page returns
zero spans and zero shapes, `extract_page` sees no exception, and the page was then
classified `CLEAN` — "nothing here" when the truth was "we could not read this".
Exactly the false assurance the project exists to prevent.

The signal is MuPDF's diagnostics, but they cannot be used bluntly: **3.26% of real
pages (144 / 4,419) emit warnings**, almost all benign font warnings
(`FT_Get_Advance(...): invalid glyph index`) that affect neither geometry nor text
recovery. Treating any warning as failure would manufacture uncertainty on one page
in thirty.

Salvaged content is kept rather than discarded, so a page that fails to parse but
still yields a fully-evidenced leak reports *both* the leak and the unreliability —
dropping to UNCERTAIN alone would hide a true positive.

**First implementation, since replaced:** substring-match the warning text for
`syntax error` and `page may not be correct`. It worked, but it made correctness
depend on another library's wording — a reworded message would silently stop
flagging corrupt pages and reinstate this exact bug, with nothing failing until
someone bumped the version pin.

### The fix, second iteration: use the channel, not the wording

MuPDF routes **errors** and **warnings** through separate callbacks
(`fz_set_error_callback` / `fz_set_warning_callback`), and that split is precisely
the distinction needed:

| channel | example | meaning here |
|---|---|---|
| error | `syntax error: syntax error in content stream` | the page did not parse |
| warning | `FT_Get_Advance(...): invalid glyph index` | cosmetic, ignore |

Measured over the same 4,419 pages:

| channel | pages | share |
|---|---|---|
| **error** | **1** | **0.02%** |
| warning | 144 | 3.26% |

So registering an error callback and treating *any* error as "this page did not
fully parse" requires no knowledge of MuPDF's phrasing at all. Message text is
still carried into the finding for the reader, but plays no part in the decision.
Re-running the corpus changed exactly one page's verdict — a
`format error: cmsOpenProfileFromMem failed` that the text-matching version missed
— with findings otherwise identical (10 leaks across the same 4 documents).

Registration failing is treated as fatal: a PyMuPDF build without the callback
cannot distinguish a damaged page from an empty one, and running anyway would mean
silently reporting damaged pages as clean.

### Thread safety of the capture window

The callback is process-global, so the clear-extract-collect window is serialized
with a lock. Without it two threads' windows interleave, and a parse failure is
attributed to the wrong page — or dropped, silently restoring "corrupt page
reported CLEAN".

Worth recording how this was verified, because the first attempt was worthless: a
test that drove genuine concurrent extraction of a clean and a corrupt document
**passed with the lock removed**, because the interleaving never actually occurred.
It was replaced with one that forces the ordering with events, which passes with
the lock and fails deterministically without it. A race test that only sometimes
reproduces is a test that only sometimes tests anything.

### BUG: `PageContent.width/height` used the MediaBox

MuPDF reports coordinates **relative to the CropBox origin**, not the MediaBox. On
a page with `CropBox = (20, 20, 380, 280)` inside `MediaBox = (0, 0, 400, 300)`, a
span authored at x=50 is reported at x=30. Detection was unaffected (everything
shifts together) and the HTML overlay was already correct (it divides by
`page.rect`), but `width`/`height` described a 400×300 page that does not exist in
that coordinate space. Now taken from the CropBox.

The naming throughout the codebase was corrected with it: the space is
**unrotated, CropBox-relative**, not "mediabox".

### NOT A BUG, but a limitation made precise

A rectangle painted under a **rotating or skewing `cm` transform** is reported by
MuPDF as four `l` (line) items, not an `re`. It is therefore invisible to the
solid-rectangle rule. The existing "non-rectangular paths" limitation is broader
than first written: it covers *tilted rectangles* too, which are a plausible way to
draw a redaction box. Asserted by a test that pins the current behaviour, so if
MuPDF ever reports transformed rectangles as rectangles the test fails and the
limitation can be lifted.
