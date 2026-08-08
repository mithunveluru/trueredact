"""The detection core: does live text sit underneath something that hides it?

Pure and deterministic. No I/O, no PyMuPDF, no globals — it consumes `PageContent`
and returns `Finding`s, which is what lets the highest-risk logic in the project be
tested exhaustively with hand-built objects and no PDF parsing at all.

The finding is a structural fact, not an inference: *this* span, at *this* bbox,
was painted at sequence N, and a filled opaque shape painted later at sequence M > N
covers X% of it, and the span's text is still extractable. Every number in a
`Finding` is re-derivable by hand from the file.
"""

from .models import (
    BBox,
    CoveredSpan,
    Finding,
    PageContent,
    ShapeObject,
    TextSpan,
    Verdict,
)

CANDIDATE_MIN_AREA = 200.0
"""pt². A shape has to be big enough to actually hide a word. 200 pt² is roughly
20x10 pt — one short word at 10 pt type. Below this a filled path is a rule, an
underline, a bullet, or a vector-traced glyph fragment; the Phase 1 corpus survey
found real glyph-fragment fills at 15-51 pt². Raising the floor much further would
start missing genuinely small redactions (a covered initial, a short ID)."""

COVERAGE_THRESHOLD = 0.85
"""Fraction of a text span's own bbox that must fall inside the covering region.
Not 1.0: a hand-drawn box routinely clips a descender or an accent while still
hiding the word. Not much lower: at ~0.5 a box overlapping an adjacent column
would start implicating text it does not actually hide."""

OPACITY_THRESHOLD = 0.5
"""Fill alpha at or above which a shape counts as a visual cover. A
semi-transparent 'redaction' is still counted, because it is arguably the worse
failure — visually leaky *and* programmatically extractable."""


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection_area(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def coverage(inner: BBox, outer: BBox) -> float:
    """Fraction of `inner` that lies inside `outer`, in 0..1.

    A degenerate `inner` (zero width or height) has no area to cover, so it returns
    0.0 and is never flagged. That direction is chosen deliberately: an
    undecidable geometry should produce a missed detection, never an accusation.
    """
    area = _area(inner)
    if area <= 0.0:
        return 0.0
    return _intersection_area(inner, outer) / area


def is_redaction_candidate(shape: ShapeObject) -> bool:
    """Could this shape plausibly be someone's attempt at a redaction box?

    Note what is *not* tested: fill colour. The plan suggested restricting
    candidates to near-black/near-white fills, but paint order plus coverage
    already establish that the text is hidden, and colour only hints at intent. A
    dark-blue box over a name leaks exactly as much as a black one. The fill is
    reported as evidence instead of used as a filter. See DECISIONS.md.
    """
    if shape.fill_color is None:
        return False  # a stroke-only outline covers nothing
    if shape.alpha is not None and shape.alpha < OPACITY_THRESHOLD:
        return False
    # alpha is None for a fill with no explicit constant-alpha setting; PDF's
    # default alpha is 1.0, so that reads as fully opaque.
    return _area(shape.bbox) >= CANDIDATE_MIN_AREA


def _covered_span(span: TextSpan, region: BBox) -> CoveredSpan | None:
    ratio = coverage(span.bbox, region)
    if ratio < COVERAGE_THRESHOLD:
        return None
    return CoveredSpan(
        text=span.text,
        bbox=span.bbox,
        coverage=ratio,
        paint_order=span.paint_order,
        render_mode=span.render_mode,
    )


def _describe(shape: ShapeObject) -> str:
    fill = shape.fill_color
    rgb = "unfilled" if fill is None else f"rgb({fill[0]:.2f}, {fill[1]:.2f}, {fill[2]:.2f})"
    alpha = 1.0 if shape.alpha is None else shape.alpha
    return f"fill {rgb}, opacity {alpha:.2f}"


def _repainted_after(span: TextSpan, shape: ShapeObject, page: PageContent) -> bool:
    """Is this span's text painted again on the same page *after* the covering shape?

    Presentation software builds a slide by painting a state, covering it with an
    opaque rectangle, and painting the next state on top. The buried copy really is
    covered and really is extractable — but the identical text is right there on
    the page, so nothing is hidden and calling it a leak is wrong.

    Matching on exact text rather than geometry is deliberate: the rebuilt copy is
    usually nudged to a different position, so a positional test would miss it.
    """
    wanted = span.text.strip()
    return any(
        other.paint_order > shape.paint_order and other.text.strip() == wanted
        for other in page.spans
    )


def _shape_findings(page: PageContent) -> tuple[list[Finding], set[int]]:
    """Findings from filled vector shapes, plus the paint orders of spans accounted for."""
    findings: list[Finding] = []
    accounted: set[int] = set()

    for shape in page.shapes:
        if not is_redaction_candidate(shape):
            continue

        hidden: list[CoveredSpan] = []
        ambiguous: list[CoveredSpan] = []
        for span in page.spans:
            covered = _covered_span(span, shape.bbox)
            if covered is None:
                continue
            if span.paint_order < shape.paint_order:
                if _repainted_after(span, shape, page):
                    continue
                hidden.append(covered)
            elif span.paint_order == shape.paint_order:
                ambiguous.append(covered)
            # span painted after the shape: the shape is underneath it, e.g. a
            # table cell or a highlight. Not a cover.

        if hidden:
            accounted.update(span.paint_order for span in hidden)
            invisible = sum(1 for span in hidden if span.render_mode == 3)
            note = (
                f"; {invisible} of them use invisible render mode but remain extractable"
                if invisible
                else ""
            )
            findings.append(
                Finding(
                    page_number=page.page_number,
                    verdict=Verdict.FAKE_REDACTION,
                    reason=(
                        f"{len(hidden)} text span(s) painted before an opaque shape "
                        f"({_describe(shape)}) that covers "
                        f"{min(s.coverage for s in hidden):.0%}-"
                        f"{max(s.coverage for s in hidden):.0%} of each, and whose text "
                        f"is still extractable{note}"
                    ),
                    shape=shape,
                    covered=tuple(hidden),
                )
            )
        elif ambiguous:
            accounted.update(span.paint_order for span in ambiguous)
            findings.append(
                Finding(
                    page_number=page.page_number,
                    verdict=Verdict.UNCERTAIN,
                    reason=(
                        f"{len(ambiguous)} text span(s) share a paint-order index with "
                        f"the shape covering them ({_describe(shape)}), so which was "
                        f"drawn first cannot be established"
                    ),
                    shape=shape,
                    covered=tuple(ambiguous),
                )
            )

    return findings, accounted


def _image_findings(page: PageContent, accounted: set[int]) -> list[Finding]:
    """Text under a raster image: position is known, paint order is not.

    PyMuPDF gives no usable sequence number for images (docs/spike-notes.md), so we
    cannot say whether the image is above or below the text. Reporting CLEAN here
    would be a false assurance, so it is UNCERTAIN — but only when an image
    *substantially* covers a span, otherwise every logo and figure in every real
    document would trip it.
    """
    findings = []
    for image in page.images:
        hidden = [
            covered
            for span in page.spans
            if span.paint_order not in accounted
            and (covered := _covered_span(span, image.bbox)) is not None
        ]
        if hidden:
            findings.append(
                Finding(
                    page_number=page.page_number,
                    verdict=Verdict.UNCERTAIN,
                    reason=(
                        f"{len(hidden)} text span(s) lie under a raster image; PDF "
                        f"images carry no recoverable paint order, so whether the "
                        f"image hides this text cannot be determined — check by hand"
                    ),
                    covered=tuple(hidden),
                )
            )
    return findings


def detect_page(page: PageContent) -> list[Finding]:
    """Classify one page. Always returns at least one finding."""
    unreliable = (
        Finding(
            page_number=page.page_number,
            verdict=Verdict.UNCERTAIN,
            reason=f"page could not be fully analysed: {page.error}",
        )
        if page.error
        else None
    )
    if unreliable is not None and not page.spans and not page.shapes:
        return [unreliable]

    if not page.spans:
        # No text layer. We cannot tell "properly redacted" from "never had text",
        # and a scanned page is out of scope by design (no OCR).
        if page.images:
            return [
                Finding(
                    page_number=page.page_number,
                    verdict=Verdict.UNCERTAIN,
                    reason=(
                        "no extractable text on this page, but it contains images — "
                        "likely a scan, which this tool cannot audit"
                    ),
                )
            ]
        return [
            Finding(
                page_number=page.page_number,
                verdict=Verdict.CLEAN,
                reason="page contains no extractable text, so nothing can be hidden in it",
            )
        ]

    findings, accounted = _shape_findings(page)
    findings.extend(_image_findings(page, accounted))
    if unreliable is not None:
        # Reported alongside any real findings, never instead of them: what we did
        # extract may be sound, but absence of evidence on a page we could not
        # fully read is not evidence of absence.
        findings.append(unreliable)
    if findings:
        return findings

    return [
        Finding(
            page_number=page.page_number,
            verdict=Verdict.CLEAN,
            reason=(
                "no opaque shape is painted over extractable text on this page"
                if page.shapes
                else "no redaction-shaped objects on this page"
            ),
        )
    ]


def detect_document(pages: list[PageContent]) -> list[Finding]:
    return [finding for page in pages for finding in detect_page(page)]


__all__ = [
    "CANDIDATE_MIN_AREA",
    "COVERAGE_THRESHOLD",
    "OPACITY_THRESHOLD",
    "coverage",
    "detect_document",
    "detect_page",
    "is_redaction_candidate",
]
