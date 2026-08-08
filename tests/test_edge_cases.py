"""Phase 5 hardening: edge cases against real PDFs, end to end.

Several of these exist because they found bugs, not because they confirmed
expectations. Where a case is a known limitation, the test asserts the limitation
so it cannot regress silently into a wrong answer.
"""

import inspect
import threading
import time

import pymupdf
import pytest
from fixtures import generate_fixtures as fx

from trueredact.core.detector import detect_document, detect_page
from trueredact.core.extractor import extract_document, extract_page
from trueredact.core.models import Verdict


def content(pdf_bytes, page_number=1):
    doc = pymupdf.open("pdf", pdf_bytes)
    try:
        return extract_page(doc.load_page(page_number - 1), page_number)
    finally:
        doc.close()


def scan(pdf_bytes):
    doc = pymupdf.open("pdf", pdf_bytes)
    try:
        return detect_document(extract_document(doc))
    finally:
        doc.close()


def verdicts(pdf_bytes):
    return [f.verdict for f in scan(pdf_bytes)]


# ------------------------------------------------------ multiple / overlapping


def test_several_overlapping_covers_all_report():
    findings = scan(fx.overlapping_covers())
    assert all(f.verdict is Verdict.FAKE_REDACTION for f in findings)
    assert len(findings) >= 2
    assert all(f.recovered_text == fx.SECRET for f in findings)


# ------------------------------------------------------------------ opacity


def test_semi_transparent_cover_is_still_a_leak():
    (finding,) = scan(fx.semi_transparent_cover())
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.shape.alpha == pytest.approx(0.6)


def test_barely_transparent_tint_is_not_a_cover():
    assert verdicts(fx.barely_transparent_cover()) == [Verdict.CLEAN]


# ------------------------------------------------------------ false positives


def test_shaded_table_cells_under_their_own_text_are_clean():
    """The classic false-positive shape: every cell filled, text written after."""
    findings = scan(fx.filled_table())
    assert [f.verdict for f in findings] == [Verdict.CLEAN], [f.reason for f in findings]


# --------------------------------------------------------------- cropped pages


def test_cropped_page_reports_its_cropbox_dimensions():
    """MuPDF reports coordinates relative to the crop origin, so the page size
    must be the CropBox — using the MediaBox would describe a different space."""
    page = content(fx.cropped_page())
    assert (page.width, page.height) == (fx.PAGE_W - 40.0, fx.PAGE_H - 40.0)


def test_cropped_page_still_detects_the_leak_with_shifted_coordinates():
    (finding,) = scan(fx.cropped_page())
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == fx.SECRET
    # Everything shifted down-left by the 20pt crop origin.
    assert finding.shape.bbox == tuple(v - 20.0 for v in fx.COVER)


# ------------------------------------------------------- known limitation


def test_tilted_cover_is_not_detected_known_limitation():
    """A rotated rectangle is reported as four line segments, not an `re`.

    This asserts the limitation rather than the desired behaviour: if MuPDF ever
    starts reporting transformed rectangles as rectangles, this fails and the
    limitation can be removed from the docs.
    """
    page = content(fx.tilted_cover())
    assert page.shapes == (), "a transformed rect is expected to be unrecognisable"
    assert [f.verdict for f in detect_page(page)] == [Verdict.CLEAN]


# ------------------------------------------------------------ corrupted pages


def test_corrupt_page_is_uncertain_never_clean():
    """The bug this test was written for: MuPDF recovers from a mangled content
    stream without raising, so the page extracted as empty and reported CLEAN."""
    findings = scan(fx.corrupt_middle_page())
    by_page = {}
    for finding in findings:
        by_page.setdefault(finding.page_number, []).append(finding.verdict)

    assert Verdict.FAKE_REDACTION in by_page[1]
    assert Verdict.FAKE_REDACTION in by_page[3]
    assert Verdict.CLEAN not in by_page[2]
    assert Verdict.UNCERTAIN in by_page[2]


def test_one_corrupt_page_does_not_abort_the_scan():
    """Fault containment: pages 1 and 3 must still be fully analysed."""
    findings = scan(fx.corrupt_middle_page())
    recovered = {f.recovered_text for f in findings if f.recovered_text}
    assert f"{fx.SECRET}-1" in recovered
    assert f"{fx.SECRET}-3" in recovered


def test_benign_font_warnings_do_not_make_a_page_uncertain():
    """3.26% of real pages emit font warnings; they affect neither geometry nor text."""
    page = content(fx.fake_redacted())
    assert page.error is None


def test_unreliability_is_decided_by_the_error_channel_not_by_message_text():
    """The decision must not depend on how MuPDF words its messages.

    An earlier version substring-matched the warning text, so a reworded MuPDF
    message would have silently stopped flagging corrupt pages — reinstating the
    worst bug in this project. Nothing in the extractor may inspect message
    wording to reach a verdict.
    """
    from trueredact.core import extractor

    source = inspect.getsource(extractor)
    body = source[source.index("def extract_page") :]
    for phrase in ("syntax error", "may not be correct", "startswith", "in lowered"):
        assert phrase not in body, (
            f"extract_page appears to branch on message text ({phrase!r}); "
            "unreliability must be decided by the channel, not the wording"
        )


def test_a_benign_page_never_reports_an_error_even_under_repeated_scans():
    """Guards against leaked diagnostics: one bad page must not taint the next."""
    doc = pymupdf.open("pdf", fx.corrupt_middle_page())
    try:
        errors = [extract_page(doc.load_page(n), n + 1).error for n in range(3)]
    finally:
        doc.close()
    assert errors[0] is None
    assert errors[1] is not None
    assert errors[2] is None, "page 2's parse failure leaked onto page 3"


def test_diagnostic_capture_windows_do_not_overlap_across_threads():
    """MuPDF's error callback is process-global, so the capture window is serialized.

    Without that serialization one page's capture clears another's buffer
    mid-extraction, and a parse failure gets attributed to the wrong page — or
    dropped, which silently restores "corrupt page reported CLEAN".

    Timing is forced with events rather than left to chance: a race that only
    sometimes reproduces makes for a test that only sometimes tests anything.
    This one fails deterministically if the lock is removed.
    """
    from trueredact.core.extractor import _captured_errors, _mupdf_errors

    first_inside = threading.Event()
    second_attempted = threading.Event()
    first_got: list[str] = []
    second_got: list[str] = []

    def first():
        with _mupdf_errors() as collected:
            _captured_errors.append("error belonging to the first page")
            first_inside.set()
            # Hold the window open while the other thread tries to enter it.
            second_attempted.wait(timeout=2.0)
            time.sleep(0.05)
        first_got.extend(collected)

    def second():
        first_inside.wait(timeout=2.0)
        second_attempted.set()
        with _mupdf_errors() as collected:
            _captured_errors.append("error belonging to the second page")
        second_got.extend(collected)

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert first_got == ["error belonging to the first page"], (
        f"the first page's diagnostics were corrupted by the second: {first_got}"
    )
    assert second_got == ["error belonging to the second page"], (
        f"the second page's diagnostics were corrupted by the first: {second_got}"
    )


def test_a_leak_on_an_unreliable_page_is_reported_alongside_the_uncertainty():
    """Salvaged evidence is kept: reporting only UNCERTAIN would hide a true positive."""
    from trueredact.core.models import PageContent, ShapeObject, TextSpan

    page = PageContent(
        page_number=1,
        width=600.0,
        height=800.0,
        spans=(
            TextSpan(
                bbox=(10.0, 5.0, 60.0, 15.0),
                text="SECRET",
                paint_order=0,
                render_mode=0,
                opacity=1.0,
            ),
        ),
        shapes=(
            ShapeObject(
                bbox=(0.0, 0.0, 200.0, 20.0),
                fill_color=(0.0, 0.0, 0.0),
                alpha=1.0,
                paint_order=1,
            ),
        ),
        error="content stream did not fully parse",
    )
    found = detect_page(page)
    assert Verdict.FAKE_REDACTION in [f.verdict for f in found]
    assert Verdict.UNCERTAIN in [f.verdict for f in found]


# ------------------------------------------------------------------- unicode


def test_non_latin_hidden_text_is_recovered_intact():
    """Recovery must be faithful to what the file actually encodes.

    Note the fixture stays inside Latin-1: PyMuPDF's base-14 Helvetica cannot
    encode e.g. U+2014, so a fixture using one would embed a substituted glyph and
    the test would be measuring the *generator*, not the extractor.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=fx.PAGE_W, height=fx.PAGE_H)
    secret = "Ünïcödé señor Ç"
    page.insert_text(fx.TEXT_ORIGIN, secret, fontsize=fx.FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*fx.COVER), color=None, fill=(0, 0, 0))
    data = doc.tobytes()
    doc.close()

    (finding,) = scan(data)
    assert finding.verdict is Verdict.FAKE_REDACTION
    assert finding.recovered_text == secret
