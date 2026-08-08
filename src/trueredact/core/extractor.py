"""Turn one PyMuPDF page into the PyMuPDF-free `PageContent` the detector consumes.

This is the only module that knows how PyMuPDF reports things. Two pieces of
planned complexity are absent because the spike showed they are not needed
(docs/spike-notes.md):

* no rotation transform — both source APIs already report unrotated,
  CropBox-relative coordinates, identically across /Rotate 0/90/180/270;
* no XObject recursion — MuPDF flattens form XObjects, emitting their content in
  page space with correctly interleaved sequence numbers.

A `ShapeObject` here means one thing precisely: **a solid filled rectangle, as
actually painted on the page**. Establishing that is PDF work, not detector work,
so it lives here — see `_shapes` for the clipping and fill-rule handling and
docs/spike-notes.md for the real-world false positives that forced both.

Extraction stays non-judgemental about everything else: invisible text and
near-black vs. coloured fills are passed through untouched, so "what counts as a
redaction attempt" remains a detector decision, unit-testable without a PDF.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pymupdf
import pymupdf.mupdf as _mupdf

from .models import BBox, ImageBox, PageContent, ShapeObject, TextSpan

_MAX_UNICODE = 0x10FFFF


# --------------------------------------------------------------------------
# MuPDF diagnostics
#
# MuPDF recovers from a corrupted content stream without raising: it returns
# whatever it salvaged and reports the problem out-of-band. Left unchecked, such a
# page extracts as empty and is then reported CLEAN — "nothing here" when the truth
# is "we could not read this". That is the single worst failure this tool can have.
#
# MuPDF separates *errors* from *warnings* into two channels, and that distinction
# is exactly the one we need:
#
#   errors   — "syntax error in content stream": the page did not parse
#   warnings — "FT_Get_Advance(...): invalid glyph index": cosmetic font noise
#              that affects neither geometry nor text recovery
#
# Measured over the 4,419-page survey corpus: the *error* channel fires on 1 page
# (0.02%), the warning channel on 144 (3.26%). So the error channel alone is the
# signal, and reading it needs no knowledge of MuPDF's wording — an earlier version
# substring-matched the warning text, which would have silently stopped working if
# MuPDF ever reworded a message, reinstating the CLEAN-on-corrupt bug.
# --------------------------------------------------------------------------

_diagnostics_lock = threading.Lock()
"""Serializes the capture window below.

MuPDF's callbacks are process-global, so two threads extracting at once would mix
their diagnostics and attribute one page's parse failure to another. Holding this
lock across each page's extraction makes concurrent use *correct*, at the cost of
serializing extraction. That is the right trade for a single-threaded CLI: the lock
is uncontended here and costs nothing, and it removes a latent correctness trap for
anyone who later calls this from a thread pool.
"""

_captured_errors: list[str] = []


def _register_mupdf_error_callback() -> None:
    """Route MuPDF's error channel into `_captured_errors`.

    Deliberately fails loudly. If PyMuPDF ever drops this API, detecting an
    unparseable page becomes impossible, and continuing would mean silently
    reporting damaged pages as clean — the exact bug this machinery exists to
    prevent. A hard failure at import is the honest outcome. The dependency is
    version-pinned, so this can only fire if someone unpins it deliberately.
    """
    try:
        _mupdf.fz_set_error_callback(_captured_errors.append)
    except AttributeError as exc:  # pragma: no cover - requires an unpinned PyMuPDF
        raise RuntimeError(
            "this PyMuPDF build does not expose fz_set_error_callback, so pages "
            "that fail to parse cannot be distinguished from empty ones; refusing "
            "to run rather than risk reporting a damaged page as clean"
        ) from exc


_register_mupdf_error_callback()


@contextmanager
def _mupdf_errors() -> Iterator[list[str]]:
    """Collect MuPDF errors raised during the block, into the yielded list.

    The list is populated on exit, so read it after the `with` block, not inside.
    """
    collected: list[str] = []
    with _diagnostics_lock:
        _captured_errors.clear()
        try:
            yield collected
        finally:
            collected.extend(_captured_errors)
            _captured_errors.clear()


def _bbox(raw) -> BBox:
    """Normalize any rect-like to (x0, y0, x1, y1) with x0 <= x1 and y0 <= y1.

    PDF rectangles may be stored with either corner first; MuPDF passes that
    through, and an un-normalized box silently breaks intersection arithmetic.
    """
    x0, y0, x1, y1 = (float(v) for v in tuple(raw)[:4])
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _span_text(chars) -> str:
    # chars are (unicode, glyph, origin, bbox); undecodable glyphs come through as
    # -1 and would blow up chr().
    return "".join(chr(c[0]) for c in chars if 0 <= c[0] <= _MAX_UNICODE)


def _text_spans(page: pymupdf.Page):
    # get_texttrace() rather than get_text("dict"): it is the only text API that
    # carries `seqno`, the paint order the whole detection rests on.
    for span in page.get_texttrace():
        text = _span_text(span["chars"])
        if not text.strip():
            continue  # whitespace-only runs carry no recoverable secret
        opacity = span.get("opacity")
        yield TextSpan(
            bbox=_bbox(span["bbox"]),
            text=text,
            paint_order=span["seqno"],
            render_mode=span["type"],
            opacity=1.0 if opacity is None else float(opacity),
        )


def _intersect(a: BBox, b: BBox) -> BBox | None:
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    return box


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _solid_rects(path) -> list[BBox]:
    """The rectangles this path actually fills solidly.

    Two corrections, both forced by measured false positives on real documents:

    * Only `re` items count. A path's reported `rect` is the bounding box of all
      its items, which for stroked vector art or a chart can span the page while
      painting almost none of it.
    * Nested rectangles within one path form a frame, not a block. Neither is
      solid: the outer one has a hole punched in it, and the inner one *is* the
      hole. Page borders drawn this way were the single largest source of false
      positives in the corpus survey. Both are dropped, which errs toward a missed
      detection rather than an accusation.

    Non-rectangular filled paths (curves, polygons) are skipped: a redaction box
    is a rectangle, and admitting arbitrary path bboxes is exactly what produced
    the false positives. Noted as a limitation in docs/spike-notes.md.
    """
    rects = [_bbox(item[1]) for item in path.get("items", ()) if item[0] == "re"]
    return [
        rect
        for i, rect in enumerate(rects)
        if not any(
            _contains(rect, other) or _contains(other, rect)
            for j, other in enumerate(rects)
            if i != j
        )
    ]


def _shapes(page: pymupdf.Page):
    """Yield solid filled rectangles, each already reduced to the region it paints.

    `extended=True` additionally reports the clip stack. Without it a fill looks
    like it covers its full rectangle even when clipped to a sliver elsewhere on
    the page — a white rect with a bbox 202% of the page area, clipped to a band
    that never touches the title it appeared to cover, was a real false positive.
    """
    clips: list[tuple[int, BBox]] = []

    for path in page.get_drawings(extended=True):
        level = path.get("level", 0)
        while clips and clips[-1][0] >= level:
            clips.pop()

        if path.get("type") == "clip":
            scissor = path.get("scissor") or path.get("rect")
            if scissor is not None:
                clips.append((level, _bbox(scissor)))
            continue

        fill = path.get("fill")
        if fill is None:
            continue  # a stroke paints an outline, never a cover

        alpha = path.get("fill_opacity")
        for rect in _solid_rects(path):
            painted: BBox | None = rect
            for _, clip in clips:
                painted = _intersect(painted, clip)
                if painted is None:
                    break
            if painted is None:
                continue
            yield ShapeObject(
                bbox=painted,
                fill_color=tuple(float(c) for c in fill),
                alpha=None if alpha is None else float(alpha),
                paint_order=path["seqno"],
            )


def _images(page: pymupdf.Page):
    for info in page.get_image_info():
        yield ImageBox(bbox=_bbox(info["bbox"]))


def extract_page(page: pymupdf.Page, page_number: int) -> PageContent:
    """Extract one page. A malformed page yields a PageContent carrying `error`.

    Faults are contained per page rather than raised: one unreadable page must not
    abort the scan of the rest of the document, but it must also not silently look
    like an empty (and therefore clean) page. The catch is broad on purpose —
    MuPDF surfaces malformed-stream failures as several unrelated exception types.
    """
    # The CropBox, not the MediaBox: MuPDF reports coordinates relative to the crop
    # origin, so a cropped page's spans and shapes live in this space, not the
    # media one. Unrotated either way — see the module docstring.
    crop = _bbox(page.cropbox)
    width, height = crop[2] - crop[0], crop[3] - crop[1]

    try:
        with _mupdf_errors() as errors:
            content = PageContent(
                page_number=page_number,
                width=width,
                height=height,
                spans=tuple(_text_spans(page)),
                shapes=tuple(_shapes(page)),
                images=tuple(_images(page)),
            )
    except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
        return PageContent(
            page_number=page_number,
            width=width,
            height=height,
            error=f"{type(exc).__name__}: {exc}",
        )

    if errors:
        # Any error on MuPDF's error channel means this page did not fully parse.
        # The message is carried for the reader's benefit only — the *decision*
        # rests on the channel the message arrived on, not on its wording.
        #
        # Whatever MuPDF salvaged is kept, not discarded: a partially-parsed page
        # can still carry a genuine, fully-evidenced leak, and throwing that away
        # to report "uncertain" would hide a true positive. The detector reports
        # both the findings and the unreliability.
        return replace(
            content,
            error=f"MuPDF could not fully parse this page: {errors[0].strip()}",
        )
    return content


def extract_document(doc: pymupdf.Document) -> list[PageContent]:
    pages = []
    for index in range(doc.page_count):
        try:
            page = doc.load_page(index)
        except Exception as exc:  # noqa: BLE001 - deliberate: see extract_page docstring
            pages.append(
                PageContent(
                    page_number=index + 1,
                    width=0.0,
                    height=0.0,
                    error=f"could not load page: {type(exc).__name__}: {exc}",
                )
            )
            continue
        pages.append(extract_page(page, index + 1))
    return pages
