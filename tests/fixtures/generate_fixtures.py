"""Reproducibly built fixture PDFs.

Every fixture is generated in code rather than checked in as a binary blob, so
that what each one contains is readable and auditable, and so a test failure is
never explained away by "the PDF must be weird".

Each function returns PDF bytes. Run this module directly to also write them to
disk next to this file (useful for eyeballing one in a viewer).
"""

import pymupdf

PAGE_W, PAGE_H = 400.0, 300.0
SECRET = "SECRET-DATA-42"
TEXT_ORIGIN = (50.0, 60.0)  # baseline, not top-left
FONT_SIZE = 12
COVER = (45.0, 45.0, 250.0, 68.0)  # fully encloses the text drawn at TEXT_ORIGIN
BLACK = (0.0, 0.0, 0.0)
YELLOW = (1.0, 1.0, 0.0)


def _finish(doc: pymupdf.Document) -> bytes:
    data = doc.tobytes()
    doc.close()
    return data


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def fake_redacted(rotation: int = 0) -> bytes:
    """Text drawn first, opaque black box painted over it. The bug we hunt."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=BLACK)
    if rotation:
        page.set_rotation(rotation)
    return _finish(doc)


def clean() -> bytes:
    """Text, no covering shape at all."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    return _finish(doc)


def properly_redacted() -> bytes:
    """The right way: the text object is genuinely removed, then a box is drawn."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.add_redact_annot(pymupdf.Rect(*COVER), fill=BLACK)
    page.apply_redactions()
    return _finish(doc)


def text_over_shape() -> bytes:
    """Box drawn first, text on top — a legitimate highlight or table cell."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=YELLOW)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    return _finish(doc)


def outlined_text() -> bytes:
    """Stroke-only rectangle around text: a border, never a cover."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*COVER), color=BLACK, fill=None, width=1)
    return _finish(doc)


def framed_text() -> bytes:
    """A page border drawn *after* the text: one path, outer rect + inner rect.

    Only the ring between them is filled, but the path's reported bounding box is
    the outer rectangle. Regression fixture for the largest single source of false
    positives found in the real-document survey.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(10.0, 10.0, PAGE_W - 10.0, PAGE_H - 10.0))
    shape.draw_rect(pymupdf.Rect(20.0, 20.0, PAGE_W - 20.0, PAGE_H - 20.0))
    shape.finish(fill=BLACK, even_odd=True, color=None)
    shape.commit()
    return _finish(doc)


def clipped_cover() -> bytes:
    """A big opaque rect drawn after the text, but clipped to a region far from it.

    Its unclipped bbox swallows the text; the clip means it paints nothing there.
    Regression fixture for the second false-positive class.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)

    # Raw content stream: PDF user space is y-up, so the text drawn at top-down
    # y=45..68 lives at y=232..255 here. The clip admits only y=0..100.
    xref = page.get_contents()[0]
    existing = doc.xref_stream(xref)
    doc.update_stream(
        xref,
        existing + b"\nq\n0 0 200 100 re W n\n0 0 0 rg\n45 232 205 23 re f\nQ\n",
    )
    return _finish(doc)


def slide_build() -> bytes:
    """A presentation build: text, an opaque white wipe, then the same text again.

    The first copy really is covered, but the content is plainly visible on the
    page, so this must not be reported as a leak.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(0.0, 0.0, PAGE_W, PAGE_H), color=None, fill=(1.0, 1.0, 1.0))
    page.insert_text((60.0, 120.0), SECRET, fontsize=FONT_SIZE)
    return _finish(doc)


def overlapping_covers() -> bytes:
    """Three separate opaque boxes, each independently covering the same text.

    Each is its own path, so the frame rule (which is per-path) does not apply —
    all three are genuine covers and each is genuine evidence.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    for inset in (0.0, 3.0, 6.0):
        page.draw_rect(
            pymupdf.Rect(
                COVER[0] - inset, COVER[1] - inset, COVER[2] + inset, COVER[3] + inset
            ),
            color=None,
            fill=BLACK,
        )
    return _finish(doc)


def semi_transparent_cover() -> bytes:
    """A 60%-opaque box over text: visually leaky *and* extractable."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=BLACK, fill_opacity=0.6)
    return _finish(doc)


def barely_transparent_cover() -> bytes:
    """A 20%-opaque tint over text — a highlight, not a cover."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=BLACK, fill_opacity=0.2)
    return _finish(doc)


def filled_table() -> bytes:
    """A legitimate table: every cell shaded first, text written into it after."""
    doc = pymupdf.open()
    page = _new_page(doc)
    for row in range(4):
        top = 40.0 + row * 30.0
        page.draw_rect(
            pymupdf.Rect(40.0, top, 360.0, top + 28.0),
            color=None,
            fill=(0.85, 0.9, 0.95) if row % 2 else (0.7, 0.78, 0.88),
        )
    for row in range(4):
        page.insert_text((50.0, 60.0 + row * 30.0), f"Row {row} {SECRET}", fontsize=FONT_SIZE)
    return _finish(doc)


def cropped_page() -> bytes:
    """CropBox smaller than MediaBox: coordinates shift to the crop origin."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=BLACK)
    page.set_cropbox(pymupdf.Rect(20.0, 20.0, PAGE_W - 20.0, PAGE_H - 20.0))
    return _finish(doc)


def tilted_cover() -> bytes:
    """A cover rotated 30 degrees by a `cm` matrix, fully over the text.

    Documents a KNOWN LIMITATION: MuPDF reports a transformed rectangle as four
    line segments rather than an `re`, so it is not a detectable solid rectangle.
    """
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        doc.xref_stream(xref)
        + b"\nq\n0.866 0.5 -0.5 0.866 20 200 cm\n0 0 0 rg\n0 0 240 40 re f\nQ\n",
    )
    return _finish(doc)


def corrupt_middle_page() -> bytes:
    """Three pages, each with a real leak; page 2's content stream is mangled.

    MuPDF recovers silently rather than raising, so without the warning check this
    page extracts as empty and gets reported CLEAN.
    """
    doc = pymupdf.open()
    for i in range(3):
        page = _new_page(doc)
        page.insert_text(TEXT_ORIGIN, f"{SECRET}-{i + 1}", fontsize=FONT_SIZE)
        page.draw_rect(pymupdf.Rect(*COVER), color=None, fill=BLACK)
    data = _finish(doc)

    doc = pymupdf.open("pdf", data)
    xref = doc[1].get_contents()[0]
    doc.update_stream(xref, b"\nq q q BT /nonexistent 12 Tf ( unterminated \n")
    return _finish(doc)


def nested_xobject() -> bytes:
    """Text inside a form XObject, with the cover drawn on the outer page."""
    inner = pymupdf.open()
    inner_page = inner.new_page(width=200.0, height=100.0)
    inner_page.insert_text((20.0, 50.0), SECRET, fontsize=FONT_SIZE)
    inner_bytes = _finish(inner)

    doc = pymupdf.open()
    page = _new_page(doc)
    src = pymupdf.open("pdf", inner_bytes)
    page.show_pdf_page(pymupdf.Rect(50.0, 50.0, 250.0, 150.0), src, 0)
    page.draw_rect(pymupdf.Rect(45.0, 45.0, 260.0, 160.0), color=None, fill=BLACK)
    src.close()
    return _finish(doc)


def image_cover() -> bytes:
    """Text hidden under a raster image rather than a vector shape."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10))
    pix.clear_with(0)
    page.insert_image(pymupdf.Rect(*COVER), pixmap=pix)
    return _finish(doc)


def blank_page() -> bytes:
    doc = pymupdf.open()
    _new_page(doc)
    return _finish(doc)


def encrypted() -> bytes:
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(TEXT_ORIGIN, SECRET, fontsize=FONT_SIZE)
    data = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret"
    )
    doc.close()
    return data


ALL = {
    "clean": clean,
    "properly_redacted": properly_redacted,
    "fake_redacted": fake_redacted,
    "text_over_shape": text_over_shape,
    "outlined_text": outlined_text,
    "framed_text": framed_text,
    "clipped_cover": clipped_cover,
    "slide_build": slide_build,
    "overlapping_covers": overlapping_covers,
    "semi_transparent_cover": semi_transparent_cover,
    "barely_transparent_cover": barely_transparent_cover,
    "filled_table": filled_table,
    "cropped_page": cropped_page,
    "tilted_cover": tilted_cover,
    "corrupt_middle_page": corrupt_middle_page,
    "nested_xobject": nested_xobject,
    "image_cover": image_cover,
    "blank_page": blank_page,
    "encrypted": encrypted,
}


if __name__ == "__main__":
    from pathlib import Path

    here = Path(__file__).parent
    for name, build in ALL.items():
        (here / f"{name}.pdf").write_bytes(build())
        print(f"wrote {name}.pdf")
