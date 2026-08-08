"""HTML report tests.

Assertions are on structure and content, never on styling or exact markup — the
report is meant to be redesigned without breaking its tests.
"""

import re
from html.parser import HTMLParser
from typing import ClassVar

import pymupdf
import pytest
from fixtures import generate_fixtures as fx

from trueredact.cli import build_report
from trueredact.core.report_html import render, write


class _WellFormed(HTMLParser):
    """Minimal check that every non-void tag is closed in the right order."""

    VOID: ClassVar[set[str]] = {"meta", "img", "br", "hr", "input", "link"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closed while <{self.stack[-1]}> was open")
        else:
            self.stack.pop()


def report_for(pdf_bytes):
    doc = pymupdf.open("pdf", pdf_bytes)
    return build_report(doc, "sample.pdf"), doc


def html_for(pdf_bytes):
    report, doc = report_for(pdf_bytes)
    try:
        return render(report, doc)
    finally:
        doc.close()


def test_html_is_well_formed():
    parser = _WellFormed()
    parser.feed(html_for(fx.fake_redacted()))
    assert parser.errors == []
    assert parser.stack == []


def test_leak_report_shows_the_recovered_text_and_a_preview():
    page = html_for(fx.fake_redacted())
    assert fx.SECRET in page
    assert "Fake redaction found" in page
    assert page.count("data:image/png;base64,") == 1


def test_report_is_self_contained_with_no_external_references():
    """A report that fetches anything is a report that can leak the document."""
    page = html_for(fx.fake_redacted())
    assert "<script" not in page.lower()
    for pattern in ("http://", "https://", "//cdn", "src='/", 'src="/'):
        assert pattern not in page, f"external reference found: {pattern}"
    # The only src= in the document should be the inlined data URI.
    assert re.findall(r"src='([^']*)'", page) == [
        s for s in re.findall(r"src='([^']*)'", page) if s.startswith("data:image/png")
    ]


def test_clean_document_gets_no_previews_and_an_all_clear():
    page = html_for(fx.clean())
    assert "No fake redaction found" in page
    assert "data:image/png;base64," not in page


def test_uncertain_pages_are_listed_but_not_previewed():
    """Previews prove a leak; an unauditable page has nothing proven to show."""
    page = html_for(fx.image_cover())
    assert "could not be audited" in page.lower()
    assert "data:image/png;base64," not in page


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_highlight_lands_on_the_cover_for_every_page_rotation(rotation):
    """The extractor works unrotated; the preview is rendered rotated.

    Verified against the fixture's known geometry: on an upright 400x300 page the
    cover spans x 45..250 (11.25%..62.5%) and y 45..68 (15%..22.7%). Under 90 deg
    those swap. If the rotation matrix were dropped, these would not match.
    """
    page = html_for(fx.fake_redacted(rotation=rotation))
    match = re.search(
        r"class='hl shape' style='left:([\d.]+)%; top:([\d.]+)%; "
        r"width:([\d.]+)%; height:([\d.]+)%'",
        page,
    )
    assert match, "no shape highlight emitted"
    left, top, width, height = (float(g) for g in match.groups())

    long_side, short_side = 51.25, 7.67  # 205/400 and 23/300, as percentages
    if rotation in (0, 180):
        assert width == pytest.approx(long_side, abs=0.1)
        assert height == pytest.approx(short_side, abs=0.1)
    else:
        assert width == pytest.approx(short_side, abs=0.1)
        assert height == pytest.approx(long_side, abs=0.1)

    assert 0.0 <= left <= 100.0 and 0.0 <= top <= 100.0
    assert left + width <= 100.5 and top + height <= 100.5


def test_pdf_text_is_html_escaped():
    """Recovered text is untrusted input going into an HTML document."""
    doc = pymupdf.open()
    page = doc.new_page(width=fx.PAGE_W, height=fx.PAGE_H)
    page.insert_text(fx.TEXT_ORIGIN, "<script>alert(1)</script>", fontsize=fx.FONT_SIZE)
    page.draw_rect(pymupdf.Rect(*fx.COVER), color=None, fill=(0, 0, 0))
    data = doc.tobytes()
    doc.close()

    out = html_for(data)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_write_produces_a_utf8_file(tmp_path):
    report, doc = report_for(fx.fake_redacted())
    out = tmp_path / "report.html"
    try:
        write(report, doc, out)
    finally:
        doc.close()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert fx.SECRET in text


def test_one_preview_per_page_even_with_several_findings_on_it():
    """Two covers over two spans on one page must not embed the image twice."""
    doc = pymupdf.open()
    page = doc.new_page(width=fx.PAGE_W, height=fx.PAGE_H)
    page.insert_text((50.0, 60.0), "FIRST-SECRET", fontsize=fx.FONT_SIZE)
    page.insert_text((50.0, 160.0), "SECOND-SECRET", fontsize=fx.FONT_SIZE)
    page.draw_rect(pymupdf.Rect(45.0, 45.0, 250.0, 68.0), color=None, fill=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(45.0, 145.0, 250.0, 168.0), color=None, fill=(0, 0, 0))
    data = doc.tobytes()
    doc.close()

    out = html_for(data)
    assert "FIRST-SECRET" in out
    assert "SECOND-SECRET" in out
    assert out.count("data:image/png;base64,") == 1, "the page image was embedded twice"
    assert out.count("class='hl shape'") == 2, "each cover should still be outlined"
