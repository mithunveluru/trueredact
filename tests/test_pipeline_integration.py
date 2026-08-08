"""End-to-end: real PDF bytes -> loader -> extractor -> detector.

The detector's own tests prove the algorithm on hand-built objects. These prove the
extraction in front of it feeds the algorithm what it expects, on actual PDFs.
"""

import pytest
from fixtures import generate_fixtures as fx

from trueredact.core.detector import detect_document
from trueredact.core.extractor import extract_document
from trueredact.core.loader import load
from trueredact.core.models import Verdict


def scan(tmp_path, pdf_bytes, name="doc.pdf"):
    path = tmp_path / name
    path.write_bytes(pdf_bytes)
    doc = load(path)
    try:
        return detect_document(extract_document(doc))
    finally:
        doc.close()


def test_fake_redaction_is_caught_and_the_text_recovered(tmp_path):
    (finding,) = scan(tmp_path, fx.fake_redacted())
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.page_number == 1
    assert finding.recovered_text == fx.SECRET
    assert finding.shape is not None
    assert finding.shape.bbox == fx.COVER
    assert finding.covered[0].coverage == pytest.approx(1.0)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_fake_redaction_is_caught_on_rotated_pages(tmp_path, rotation):
    (finding,) = scan(tmp_path, fx.fake_redacted(rotation=rotation))
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == fx.SECRET


def test_fake_redaction_inside_a_form_xobject_is_caught(tmp_path):
    (finding,) = scan(tmp_path, fx.nested_xobject())
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == fx.SECRET


# --- the zero-false-positive bar: none of these may report FAKE_REDACTION ---


@pytest.mark.parametrize(
    "name",
    [
        "clean",
        "properly_redacted",
        "text_over_shape",
        "outlined_text",
        "blank_page",
        "framed_text",
        "clipped_cover",
        "slide_build",
    ],
)
def test_no_false_positives(tmp_path, name):
    findings = scan(tmp_path, fx.ALL[name]())
    assert [f.verdict for f in findings] == [Verdict.CLEAN], [f.reason for f in findings]


def test_properly_redacted_really_did_remove_the_text(tmp_path):
    """Guards the fixture itself: if apply_redactions() ever stopped removing the
    text object, the CLEAN result above would become a false negative, not a pass."""
    path = tmp_path / "proper.pdf"
    path.write_bytes(fx.properly_redacted())
    doc = load(path)
    try:
        (page,) = extract_document(doc)
        assert fx.SECRET not in "".join(span.text for span in page.spans)
        assert page.shapes, "the redaction box itself should still be present"
    finally:
        doc.close()


def test_text_under_an_image_is_reported_uncertain_not_clean(tmp_path):
    (finding,) = scan(tmp_path, fx.image_cover())
    assert finding.verdict is Verdict.UNCERTAIN
    assert finding.covered[0].text == fx.SECRET


def test_multipage_findings_carry_the_right_page_numbers(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open("pdf", fx.clean()))
    doc.insert_pdf(pymupdf.open("pdf", fx.fake_redacted()))
    doc.insert_pdf(pymupdf.open("pdf", fx.clean()))
    data = doc.tobytes()
    doc.close()

    findings = scan(tmp_path, data)
    assert [(f.page_number, f.verdict) for f in findings] == [
        (1, Verdict.CLEAN),
        (2, Verdict.FAKE_REDACTION),
        (3, Verdict.CLEAN),
    ]
