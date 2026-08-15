"""Detector unit tests: plain objects only, no PDF parsing anywhere in this file.

That separation is the point of the design — if these pass and an integration test
fails, the bug is in extraction, not in the algorithm.
"""

import pytest

from trueredact.core.detector import (
    CANDIDATE_MIN_AREA,
    COVERAGE_THRESHOLD,
    OPACITY_THRESHOLD,
    coverage,
    detect_page,
    is_redaction_candidate,
)
from trueredact.core.models import (
    ImageBox,
    PageContent,
    ShapeObject,
    TextSpan,
    Verdict,
)

# A 200x20 box at the origin has area 4000 pt², comfortably a candidate.
BOX = (0.0, 0.0, 200.0, 20.0)


def span(bbox=(10.0, 5.0, 60.0, 15.0), order=0, text="SECRET", render_mode=0):
    return TextSpan(bbox=bbox, text=text, paint_order=order, render_mode=render_mode, opacity=1.0)


def shape(bbox=BOX, order=1, fill=(0.0, 0.0, 0.0), alpha=1.0):
    return ShapeObject(bbox=bbox, fill_color=fill, alpha=alpha, paint_order=order)


def page(spans=(), shapes=(), images=(), redactions=(), error=None, number=1):
    return PageContent(
        page_number=number,
        spans=tuple(spans),
        shapes=tuple(shapes),
        images=tuple(images),
        redactions=tuple(redactions),
        error=error,
    )


def verdicts(content):
    return [f.verdict for f in detect_page(content)]


# --------------------------------------------------------------------- geometry


def test_coverage_of_fully_enclosed_box_is_one():
    assert coverage((10.0, 10.0, 20.0, 20.0), (0.0, 0.0, 100.0, 100.0)) == 1.0


def test_coverage_of_disjoint_boxes_is_zero():
    assert coverage((0.0, 0.0, 10.0, 10.0), (50.0, 50.0, 60.0, 60.0)) == 0.0


def test_edge_touching_boxes_do_not_overlap():
    """Shared edge, zero-area intersection — must not count as coverage."""
    assert coverage((0.0, 0.0, 10.0, 10.0), (10.0, 0.0, 20.0, 10.0)) == 0.0


def test_coverage_is_measured_against_the_inner_box_not_the_outer():
    # Half of the span is inside; the shape being huge must not inflate the ratio.
    assert coverage((0.0, 0.0, 10.0, 10.0), (5.0, 0.0, 1000.0, 1000.0)) == 0.5


def test_degenerate_zero_area_span_yields_zero_not_a_crash():
    assert coverage((5.0, 5.0, 5.0, 5.0), (0.0, 0.0, 100.0, 100.0)) == 0.0
    assert coverage((5.0, 5.0, 50.0, 5.0), (0.0, 0.0, 100.0, 100.0)) == 0.0


def test_zero_area_span_is_never_flagged():
    content = page(spans=[span(bbox=(10.0, 5.0, 10.0, 5.0))], shapes=[shape()])
    assert verdicts(content) == [Verdict.CLEAN]


# ------------------------------------------------------------------- candidates


def test_unfilled_outline_is_not_a_candidate():
    assert not is_redaction_candidate(shape(fill=None, alpha=None))


def test_shape_below_minimum_area_is_not_a_candidate():
    # 20x9 = 180 pt², just under the floor.
    assert not is_redaction_candidate(shape(bbox=(0.0, 0.0, 20.0, 9.0)))


def test_minimum_area_boundary_is_inclusive():
    side = CANDIDATE_MIN_AREA**0.5
    assert is_redaction_candidate(shape(bbox=(0.0, 0.0, side, side)))


def test_opacity_boundary_is_inclusive():
    assert is_redaction_candidate(shape(alpha=OPACITY_THRESHOLD))
    assert not is_redaction_candidate(shape(alpha=OPACITY_THRESHOLD - 0.01))


def test_missing_alpha_is_treated_as_opaque():
    """PDF's default constant alpha is 1.0, so None must not read as transparent."""
    assert is_redaction_candidate(shape(alpha=None))


def test_non_black_fill_is_still_a_candidate():
    """Colour is evidence, not a filter — a dark blue box hides just as much."""
    assert is_redaction_candidate(shape(fill=(0.1, 0.2, 0.6)))


# ------------------------------------------------------------------- the verdict


def test_text_painted_before_an_opaque_box_is_a_fake_redaction():
    (finding,) = detect_page(page(spans=[span(order=0)], shapes=[shape(order=1)]))
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == "SECRET"
    assert finding.shape is not None
    assert finding.covered[0].coverage == 1.0
    assert finding.covered[0].paint_order == 0


def test_text_painted_after_the_box_is_clean():
    """A highlight or a table cell: the shape is underneath, hiding nothing."""
    assert verdicts(page(spans=[span(order=5)], shapes=[shape(order=1)])) == [Verdict.CLEAN]


def test_identical_paint_order_is_uncertain_never_an_accusation():
    (finding,) = detect_page(page(spans=[span(order=3)], shapes=[shape(order=3)]))
    assert finding.verdict is Verdict.UNCERTAIN
    assert "cannot be established" in finding.reason


def test_partial_coverage_below_threshold_is_clean():
    # Span 100 wide, only 50 of it inside the 200x20 box -> 0.5 coverage.
    assert verdicts(page(spans=[span(bbox=(150.0, 5.0, 250.0, 15.0))], shapes=[shape()])) == [
        Verdict.CLEAN
    ]


def test_coverage_threshold_boundary_is_inclusive():
    """A span exactly at the threshold must flag; one hair under must not."""
    # 100pt-wide span; x from 100-w to 200 lands exactly COVERAGE_THRESHOLD inside.
    width = 100.0
    inside = width * COVERAGE_THRESHOLD
    at = span(bbox=(200.0 - inside, 5.0, 200.0 - inside + width, 15.0))
    assert verdicts(page(spans=[at], shapes=[shape()])) == [Verdict.FAKE_REDACTION]

    below = span(bbox=(200.0 - inside + 1.0, 5.0, 200.0 - inside + 1.0 + width, 15.0))
    assert verdicts(page(spans=[below], shapes=[shape()])) == [Verdict.CLEAN]


def test_transparent_shape_does_not_flag():
    assert verdicts(page(spans=[span()], shapes=[shape(alpha=0.2)])) == [Verdict.CLEAN]


def test_semi_transparent_shape_above_threshold_still_flags():
    assert verdicts(page(spans=[span()], shapes=[shape(alpha=0.6)])) == [Verdict.FAKE_REDACTION]


def test_invisible_render_mode_text_is_still_a_leak():
    content = page(spans=[span(render_mode=3)], shapes=[shape()])
    (finding,) = detect_page(content)
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert "invisible render mode" in finding.reason


def test_multiple_spans_under_one_shape_produce_one_finding():
    content = page(
        spans=[
            span(bbox=(10.0, 5.0, 60.0, 15.0), order=0, text="John"),
            span(bbox=(65.0, 5.0, 120.0, 15.0), order=1, text="Smith"),
        ],
        shapes=[shape(order=2)],
    )
    (finding,) = detect_page(content)
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == "John Smith"
    assert len(finding.covered) == 2


def test_two_shapes_over_two_spans_produce_two_findings():
    content = page(
        spans=[
            span(bbox=(10.0, 5.0, 60.0, 15.0), order=0, text="one"),
            span(bbox=(10.0, 105.0, 60.0, 115.0), order=1, text="two"),
        ],
        shapes=[
            shape(bbox=(0.0, 0.0, 200.0, 20.0), order=2),
            shape(bbox=(0.0, 100.0, 200.0, 120.0), order=3),
        ],
    )
    findings = detect_page(content)
    assert [f.verdict for f in findings] == [Verdict.FAKE_REDACTION] * 2
    assert [f.recovered_text for f in findings] == ["one", "two"]


def test_nested_shapes_both_covering_the_same_span_both_report():
    """Overlapping covers are each genuine evidence; neither is suppressed."""
    content = page(
        spans=[span(order=0)],
        shapes=[shape(bbox=(0.0, 0.0, 200.0, 20.0), order=1), shape(bbox=BOX, order=2)],
    )
    findings = detect_page(content)
    assert [f.verdict for f in findings] == [Verdict.FAKE_REDACTION] * 2


def test_unfilled_shape_over_text_is_clean():
    assert verdicts(page(spans=[span()], shapes=[shape(fill=None, alpha=None)])) == [Verdict.CLEAN]


def test_tiny_shape_over_text_is_clean():
    content = page(spans=[span(bbox=(1.0, 1.0, 5.0, 5.0))], shapes=[shape(bbox=(0.0, 0.0, 6.0, 6.0))])
    assert verdicts(content) == [Verdict.CLEAN]


# ------------------------------------------------- slide builds (repainted text)


def test_text_repainted_after_the_cover_is_not_a_leak():
    """Presentation builds: paint a state, white it out, paint the next state.

    The buried copy is genuinely covered, but the same text is visible on the page,
    so nothing is hidden. This was the largest remaining false-positive class in
    the real-document survey.
    """
    content = page(
        spans=[
            span(order=0, text="Creating Classes"),
            span(bbox=(10.0, 105.0, 60.0, 115.0), order=9, text="Creating Classes"),
        ],
        shapes=[shape(order=5, fill=(1.0, 1.0, 1.0))],
    )
    assert verdicts(content) == [Verdict.CLEAN]


def test_repaint_must_come_after_the_cover_to_clear_it():
    """Two copies both painted before the cover are both still hidden."""
    content = page(
        spans=[
            span(order=0, text="John Smith"),
            span(bbox=(10.0, 6.0, 60.0, 16.0), order=1, text="John Smith"),
        ],
        shapes=[shape(order=5)],
    )
    assert verdicts(content) == [Verdict.FAKE_REDACTION]


def test_different_text_painted_later_does_not_clear_a_cover():
    content = page(
        spans=[
            span(order=0, text="John Smith"),
            span(bbox=(10.0, 105.0, 60.0, 115.0), order=9, text="something else"),
        ],
        shapes=[shape(order=5)],
    )
    assert verdicts(content) == [Verdict.FAKE_REDACTION]


def test_repaint_matching_ignores_surrounding_whitespace():
    content = page(
        spans=[
            span(order=0, text="  Total "),
            span(bbox=(10.0, 105.0, 60.0, 115.0), order=9, text="Total"),
        ],
        shapes=[shape(order=5)],
    )
    assert verdicts(content) == [Verdict.CLEAN]


# ----------------------------------------------------------- pages we cannot judge


def test_extraction_failure_is_uncertain_never_clean():
    (finding,) = detect_page(page(error="MuPDFError: bad stream"))
    assert finding.verdict is Verdict.UNCERTAIN
    assert "bad stream" in finding.reason


def test_page_with_images_but_no_text_is_uncertain():
    (finding,) = detect_page(page(images=[ImageBox(bbox=BOX)]))
    assert finding.verdict is Verdict.UNCERTAIN
    assert "scan" in finding.reason


def test_genuinely_empty_page_is_clean_not_uncertain():
    """Nothing on the page at all means nothing can be hidden in it."""
    assert verdicts(page()) == [Verdict.CLEAN]


def test_text_under_an_image_is_uncertain():
    (finding,) = detect_page(page(spans=[span()], images=[ImageBox(bbox=BOX)]))
    assert finding.verdict is Verdict.UNCERTAIN
    assert "paint order" in finding.reason
    assert finding.covered[0].text == "SECRET"


def test_image_near_but_not_over_text_does_not_trip():
    """Otherwise every logo and figure in every real document would be UNCERTAIN."""
    content = page(spans=[span()], images=[ImageBox(bbox=(300.0, 300.0, 400.0, 400.0))])
    assert verdicts(content) == [Verdict.CLEAN]


def test_span_already_proven_leaked_is_not_also_reported_uncertain():
    content = page(spans=[span(order=0)], shapes=[shape(order=1)], images=[ImageBox(bbox=BOX)])
    assert verdicts(content) == [Verdict.FAKE_REDACTION]


# ------------------------------------------------------- /Redact annotations


def test_redaction_mark_over_text_is_a_leak_regardless_of_paint_order():
    """Annotations paint after the content stream, so there is no order to compare."""
    for order in (0, 99):
        content = page(spans=[span(order=order)], redactions=[BOX])
        assert verdicts(content) == [Verdict.FAKE_REDACTION], order


def test_redaction_mark_not_over_any_text_is_clean():
    content = page(spans=[span()], redactions=[(300.0, 300.0, 400.0, 400.0)])
    assert verdicts(content) == [Verdict.CLEAN]


def test_redaction_mark_below_the_coverage_threshold_is_not_flagged():
    """Same geometry rule as a drawn cover: a box grazing a span does not hide it.
    The span runs to x=260 but BOX stops at x=200, so 76% of it is covered."""
    content = page(spans=[span(bbox=(10.0, 5.0, 260.0, 15.0))], redactions=[BOX])
    assert verdicts(content) == [Verdict.CLEAN]


def test_redaction_mark_is_not_excused_by_the_text_appearing_again():
    """The repainted-text rule excuses slide rebuilds, not an explicit removal mark."""
    content = page(
        spans=[span(order=0), span(bbox=(10.0, 200.0, 60.0, 210.0), order=5)],
        redactions=[BOX],
    )
    assert verdicts(content) == [Verdict.FAKE_REDACTION]


def test_span_already_flagged_by_a_shape_is_not_reported_again_by_a_mark():
    content = page(spans=[span(order=0)], shapes=[shape(order=1)], redactions=[BOX])
    assert verdicts(content) == [Verdict.FAKE_REDACTION]


# ------------------------------------------------------------------- determinism


def test_detection_is_deterministic():
    content = page(
        spans=[span(order=0, text="a"), span(bbox=(10.0, 105.0, 60.0, 115.0), order=1, text="b")],
        shapes=[shape(order=2), shape(bbox=(0.0, 100.0, 200.0, 120.0), order=3)],
    )
    first = detect_page(content)
    for _ in range(5):
        assert detect_page(content) == first


@pytest.mark.parametrize("page_number", [1, 7, 500])
def test_page_number_is_carried_onto_every_finding(page_number):
    content = page(spans=[span()], shapes=[shape()], number=page_number)
    assert all(f.page_number == page_number for f in detect_page(content))
