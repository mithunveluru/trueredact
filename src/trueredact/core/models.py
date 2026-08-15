"""Domain types shared by extraction and detection.

This module deliberately imports nothing from PyMuPDF. The detector consumes only
these types, which is what makes the highest-risk logic in the project testable
with plain Python objects and no PDF parsing.

All coordinates are **unrotated, CropBox-relative page space**. PyMuPDF's
`get_texttrace()` and `get_drawings()` both report in that space: they ignore
`/Rotate`, and they measure from the crop origin rather than the media one. Text
and shapes are therefore directly comparable without any transform — but painting
one of these boxes onto a *rendered* page does need the rotation applied. See
docs/spike-notes.md and `core/report_html.py`.
"""

from dataclasses import dataclass
from enum import Enum

# x0, y0, x1, y1 — normalized so x0 <= x1 and y0 <= y1, in unrotated
# CropBox-relative page space.
BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class TextSpan:
    bbox: BBox
    text: str
    paint_order: int
    render_mode: int
    """PDF text rendering mode. 0 = filled/visible, 3 = invisible.

    Mode 3 text is invisible when rendered but still extractable, so it is still
    a leak when it sits under a redaction shape — kept, not filtered.
    """
    opacity: float


@dataclass(frozen=True, slots=True)
class ShapeObject:
    bbox: BBox
    fill_color: tuple[float, float, float] | None
    """RGB in 0..1, or None for a stroke-only path (an outline, never a cover)."""
    alpha: float | None
    """Fill opacity in 0..1. None — not 1.0 — when the path has no fill at all."""
    paint_order: int


@dataclass(frozen=True, slots=True)
class ImageBox:
    """A raster image placed on the page.

    Carries no paint order on purpose: PyMuPDF's `get_image_info()['number']` is an
    index, not a sequence number, and collides with real `seqno` values on most
    real-world pages (docs/spike-notes.md). So we know *where* an image is but not
    whether it sits above or below a given text span.
    """

    bbox: BBox


@dataclass(frozen=True, slots=True)
class PageContent:
    """Everything the detector needs about one page.

    `paint_order` values are only comparable *within* one page — PyMuPDF's sequence
    counter resets on every page.
    """

    page_number: int
    spans: tuple[TextSpan, ...] = ()
    shapes: tuple[ShapeObject, ...] = ()
    images: tuple[ImageBox, ...] = ()
    redactions: tuple[BBox, ...] = ()
    """Bboxes of `/Redact` annotations still present on the page.

    Bare boxes, with no paint order and no fill: annotations paint after the whole
    content stream, so one is above the page's text by definition, and a `/Redact`
    annotation paints nothing at all. Its mere presence is the evidence."""
    error: str | None = None
    """Set when extraction failed. Keeps "we could not read this page" distinct
    from "this page was empty" — the former must never be reported as clean."""


class Verdict(str, Enum):
    CLEAN = "clean"
    FAKE_REDACTION = "fake_redaction"
    UNCERTAIN = "uncertain"
    """Used wherever the evidence genuinely does not settle the question: a page we
    could not parse, a page with no text layer to audit, an ambiguous paint order,
    or text under an image. Never a hedge on a case we can actually decide."""


@dataclass(frozen=True, slots=True)
class CoveredSpan:
    """A text span found hidden beneath something, with the arithmetic that proves it."""

    text: str
    bbox: BBox
    coverage: float
    """Fraction of this span's own bbox that lies inside the covering region, 0..1."""
    paint_order: int
    render_mode: int


@dataclass(frozen=True, slots=True)
class Finding:
    """One verdict about one page, carrying the evidence behind it.

    There is deliberately no `confidence` score. Every number here is a measured
    structural fact a reader can re-derive from the file by hand; a weighted
    heuristic collapsed into a single 0..1 value would read as a calibrated
    probability while being nothing of the sort. See DECISIONS.md.
    """

    page_number: int
    verdict: Verdict
    reason: str
    shape: ShapeObject | None = None
    """The covering shape, when one is implicated — its bbox is the flagged region,
    and its fill and opacity are part of the evidence."""
    covered: tuple[CoveredSpan, ...] = ()

    @property
    def recovered_text(self) -> str | None:
        """The hidden text, joined in the order the producer painted it.

        Paint order is used rather than a geometric top-to-bottom sort on purpose:
        it is the order the document's own producer wrote the text in, and it needs
        no line-grouping tolerance constant that we could not justify.
        """
        if not self.covered:
            return None
        return " ".join(span.text for span in self.covered)


@dataclass(frozen=True, slots=True)
class ScanReport:
    file_path: str
    page_count: int
    findings: tuple[Finding, ...]
    generated_at: str
    """ISO 8601, UTC."""

    @property
    def has_leak(self) -> bool:
        return any(f.verdict is Verdict.FAKE_REDACTION for f in self.findings)

    @property
    def has_uncertainty(self) -> bool:
        return any(f.verdict is Verdict.UNCERTAIN for f in self.findings)

    def of_verdict(self, verdict: Verdict) -> list[Finding]:
        return [f for f in self.findings if f.verdict is verdict]
