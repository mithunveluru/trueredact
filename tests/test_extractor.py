import pymupdf
import pytest
from fixtures import generate_fixtures as fx

from trueredact.core.extractor import extract_document, extract_page


def content(pdf_bytes: bytes, page_number: int = 1):
    doc = pymupdf.open("pdf", pdf_bytes)
    try:
        return extract_page(doc.load_page(page_number - 1), page_number)
    finally:
        doc.close()


def test_fake_redacted_yields_one_span_under_one_shape():
    page = content(fx.fake_redacted())

    assert page.error is None
    assert (page.width, page.height) == (fx.PAGE_W, fx.PAGE_H)

    (span,) = page.spans
    assert span.text == fx.SECRET
    assert span.render_mode == 0
    assert span.opacity == 1.0

    (shape,) = page.shapes
    assert shape.bbox == fx.COVER
    assert shape.fill_color == (0.0, 0.0, 0.0)
    assert shape.alpha == 1.0

    # The whole point: the text was painted before the box that hides it.
    assert span.paint_order < shape.paint_order


def test_paint_order_distinguishes_a_highlight_from_a_cover():
    """Same geometry as fake_redacted, opposite draw order — must invert."""
    page = content(fx.text_over_shape())
    (span,) = page.spans
    (shape,) = page.shapes
    assert shape.bbox == fx.COVER
    assert span.paint_order > shape.paint_order


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotation_leaves_coordinates_and_order_untouched(rotation):
    """Locks the spike finding that no rotation transform is needed.

    Both source APIs report unrotated mediabox coordinates, so a rotated page must
    extract identically to an unrotated one. If PyMuPDF ever changes this, the
    extractor needs a rotation matrix and this test is the alarm.
    """
    upright = content(fx.fake_redacted())
    rotated = content(fx.fake_redacted(rotation=rotation))
    assert rotated.spans == upright.spans
    assert rotated.shapes == upright.shapes


def test_form_xobject_content_is_flattened_into_page_space():
    """Locks the spike finding that no XObject recursion is needed."""
    page = content(fx.nested_xobject())

    (span,) = page.spans
    (shape,) = page.shapes
    assert span.text == fx.SECRET
    assert span.paint_order < shape.paint_order

    # Text authored at (20, 50) inside the XObject, which is placed at (50, 50):
    # it must surface in outer-page coordinates, not the XObject's own.
    assert span.bbox[0] > 50.0
    x0, y0, x1, y1 = span.bbox
    sx0, sy0, sx1, sy1 = shape.bbox
    assert sx0 <= x0 and sy0 <= y0 and x1 <= sx1 and y1 <= sy1


def test_stroke_only_path_is_not_extracted_as_a_shape():
    """A ShapeObject means a solid filled rectangle; an outline paints no region."""
    assert content(fx.outlined_text()).shapes == ()


def test_frame_path_is_not_extracted_as_a_solid_cover():
    """Outer rect + inner rect fills only the ring between them.

    The path's reported bbox is the outer rectangle, so taking it at face value
    made every bordered page look like a full-page cover.
    """
    page = content(fx.framed_text())
    (span,) = page.spans
    assert span.text == fx.SECRET
    assert not any(
        shape.bbox[0] <= span.bbox[0] and span.bbox[2] <= shape.bbox[2]
        for shape in page.shapes
    ), "the frame must not be reported as a rectangle covering the text"


def test_clipped_fill_is_reduced_to_the_region_it_actually_paints():
    """The rect's own bbox covers the text; its clip means it paints nowhere near it."""
    page = content(fx.clipped_cover())
    (span,) = page.spans
    for shape in page.shapes:
        assert shape.bbox[1] >= span.bbox[3] or shape.bbox[3] <= span.bbox[1], (
            f"clipped fill {shape.bbox} should not overlap the text at {span.bbox}"
        )


def test_image_cover_is_extracted_but_carries_no_paint_order():
    page = content(fx.image_cover())

    assert page.shapes == ()  # an image is not a drawing
    (image,) = page.images
    assert image.bbox == fx.COVER
    assert not hasattr(image, "paint_order")

    (span,) = page.spans
    assert span.text == fx.SECRET


def test_blank_page_extracts_empty_without_error():
    page = content(fx.blank_page())
    assert (page.spans, page.shapes, page.images) == ((), (), ())
    assert page.error is None


def test_bbox_is_normalized_even_if_the_pdf_stores_corners_reversed():
    doc = pymupdf.open()
    page = doc.new_page(width=fx.PAGE_W, height=fx.PAGE_H)
    # x1 < x0 and y1 < y0 on the way in.
    page.draw_rect(pymupdf.Rect(250.0, 68.0, 45.0, 45.0), color=None, fill=(0, 0, 0))
    data = doc.tobytes()
    doc.close()

    (shape,) = content(data).shapes
    x0, y0, x1, y1 = shape.bbox
    assert x0 <= x1 and y0 <= y1


def test_extract_document_numbers_pages_from_one():
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=fx.PAGE_W, height=fx.PAGE_H)
    data = doc.tobytes()
    doc.close()

    pages = extract_document(pymupdf.open("pdf", data))
    assert [p.page_number for p in pages] == [1, 2, 3]
