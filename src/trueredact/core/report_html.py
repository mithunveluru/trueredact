"""Render a ScanReport as one self-contained HTML file.

Self-contained means exactly that: inline CSS, base64-embedded page images, and no
JavaScript. The file opens from disk with no network access and nothing to fetch —
which is the same property the rest of the tool has, for the same reason.

Page previews are produced only for `FAKE_REDACTION` findings. The preview exists
to *prove* a leak; an `UNCERTAIN` page has nothing proven to show, and rendering
every unauditable page of a 500-page scan would produce a file nobody can open.
"""

import base64
import html
from pathlib import Path

import pymupdf

from .models import Finding, ScanReport, Verdict

PREVIEW_DPI = 110
"""Enough to read body text in the preview without making the page images the
dominant cost of the file. A letter page lands around 900x1200 px."""

_STYLE = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --muted:#5c6570;
        --leak:#c0261c; --unsure:#9a6700; --ok:#1a7f37; --line:#d8dce1; }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--ink);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width:60rem; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
h2 { font-size:1.1rem; margin:2rem 0 .75rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; word-break:break-all; }
.banner { padding:1rem 1.25rem; border-radius:8px; font-weight:600; margin-bottom:1.5rem; }
.banner.leak { background:#fdecea; color:var(--leak); border:1px solid #f3b7b1; }
.banner.unsure { background:#fdf6e3; color:var(--unsure); border:1px solid #e8d9a8; }
.banner.ok { background:#e9f7ee; color:var(--ok); border:1px solid #a9dfbb; }
.counts { display:flex; gap:1.5rem; flex-wrap:wrap; margin-bottom:1.5rem;
          color:var(--muted); font-size:.9rem; }
.counts b { color:var(--ink); font-size:1.25rem; display:block; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:1.25rem; margin-bottom:1.25rem; }
.card h3 { margin:0 0 .5rem; font-size:1rem; }
.reason { color:var(--muted); margin:0 0 1rem; }
.secret { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:1rem;
          background:#fdecea; border-left:3px solid var(--leak); padding:.6rem .8rem;
          margin:0 0 1rem; white-space:pre-wrap; word-break:break-word; }
.preview { position:relative; display:block; border:1px solid var(--line);
           background:#fff; overflow:hidden; }
.preview img { display:block; width:100%; height:auto; }
.hl { position:absolute; pointer-events:none; }
.hl.shape { outline:2px solid var(--leak); background:rgba(192,38,28,.12); }
.hl.span { outline:2px dashed #b78103; background:rgba(255,196,0,.28); }
.legend { display:flex; gap:1.25rem; flex-wrap:wrap; color:var(--muted);
          font-size:.85rem; margin:.6rem 0 0; }
.swatch { display:inline-block; width:.85rem; height:.85rem; vertical-align:-1px;
          margin-right:.35rem; border-radius:2px; }
table { border-collapse:collapse; width:100%; font-size:.88rem; margin-top:1rem; }
th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:600; }
td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
ul.plain { margin:0; padding-left:1.1rem; color:var(--muted); }
ul.plain li { margin-bottom:.4rem; }
footer { color:var(--muted); font-size:.85rem; margin-top:2.5rem;
         border-top:1px solid var(--line); padding-top:1rem; }
@media print { body { background:#fff; } .card { break-inside:avoid; } }
"""


def _esc(text: object) -> str:
    """Escape anything originating in the PDF. Untrusted input, HTML output."""
    return html.escape(str(text), quote=True)


def _preview_png(page: pymupdf.Page) -> str:
    pix = page.get_pixmap(dpi=PREVIEW_DPI)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _overlay_style(page: pymupdf.Page, bbox) -> str:
    """Place an unrotated, CropBox-relative bbox onto the rendered page, as percentages.

    The extractor works in unrotated, CropBox-relative space while the preview is
    rendered in display space, so `/Rotate` has to be applied here — this is the
    one place in the codebase where page rotation matters. Verified against actual
    rendered pixels at 0/90/180/270.
    """
    mapped = (pymupdf.Rect(*bbox) * page.rotation_matrix).normalize()
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        return "display:none"
    left = (mapped.x0 - rect.x0) / rect.width * 100
    top = (mapped.y0 - rect.y0) / rect.height * 100
    return (
        f"left:{left:.3f}%; top:{top:.3f}%; "
        f"width:{mapped.width / rect.width * 100:.3f}%; "
        f"height:{mapped.height / rect.height * 100:.3f}%"
    )


def _spans_table(finding: Finding) -> str:
    rows = "".join(
        f"<tr><td>{_esc(span.text)}</td>"
        f"<td class='num'>{span.coverage:.0%}</td>"
        f"<td class='num'>{span.paint_order}</td>"
        f"<td class='num'>{span.render_mode}</td></tr>"
        for span in finding.covered
    )
    return (
        "<table><thead><tr><th>hidden text</th><th>covered</th>"
        "<th>painted at</th><th>render mode</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _shape_facts(finding: Finding) -> str:
    shape = finding.shape
    if shape is None:
        return ""
    fill = shape.fill_color
    fill_text = (
        "none" if fill is None else f"rgb({fill[0]:.2f}, {fill[1]:.2f}, {fill[2]:.2f})"
    )
    alpha = 1.0 if shape.alpha is None else shape.alpha
    region = ", ".join(f"{v:.1f}" for v in shape.bbox)
    return (
        "<p class='legend'>"
        f"<span>region ({region})</span>"
        f"<span>fill {fill_text}</span>"
        f"<span>opacity {alpha:.2f}</span>"
        f"<span>painted at {shape.paint_order}</span>"
        "</p>"
    )


def _leak_card(
    page_number: int, findings: list[Finding], page: pymupdf.Page | None
) -> str:
    """One card per *page*, not per finding.

    A page can be flagged several times — overlapping covers, or several boxes.
    Embedding the same base64 preview once per finding would multiply the file
    size for no added evidence, so the page renders once and every finding's
    regions are overlaid on it.
    """
    parts = [
        "<div class='card'>",
        (
            f"<h3>Page {page_number} — fake redaction "
            f"({len(findings)} finding{'s' if len(findings) > 1 else ''})</h3>"
        ),
    ]

    if page is not None:
        overlays = []
        for finding in findings:
            if finding.shape is not None:
                style = _overlay_style(page, finding.shape.bbox)
                overlays.append(f"<span class='hl shape' style='{style}'></span>")
            overlays += [
                f"<span class='hl span' style='{_overlay_style(page, span.bbox)}'></span>"
                for span in finding.covered
            ]
        parts.append(
            "<div class='preview'>"
            f"<img alt='Page {page_number} preview' "
            f"src='data:image/png;base64,{_preview_png(page)}'>"
            + "".join(overlays)
            + "</div>"
            "<p class='legend'>"
            "<span><i class='swatch' style='background:rgba(192,38,28,.4);"
            "outline:2px solid #c0261c'></i>covering shape</span>"
            "<span><i class='swatch' style='background:rgba(255,196,0,.5);"
            "outline:2px dashed #b78103'></i>text still extractable underneath</span>"
            "</p>"
        )

    for finding in findings:
        parts.append(f"<p class='reason'>{_esc(finding.reason)}</p>")
        if finding.recovered_text:
            parts.append(f"<p class='secret'>{_esc(finding.recovered_text)}</p>")
        parts.append(_shape_facts(finding))
        if finding.covered:
            parts.append(_spans_table(finding))

    parts.append("</div>")
    return "".join(parts)


def _page_for(doc: pymupdf.Document, page_number: int) -> pymupdf.Page | None:
    """Load a page for preview. A page we cannot render still gets its card."""
    try:
        return doc.load_page(page_number - 1)
    except Exception:  # noqa: BLE001 - a broken preview must not lose the finding
        return None


def render(report: ScanReport, doc: pymupdf.Document) -> str:
    leaks = report.of_verdict(Verdict.FAKE_REDACTION)
    unsure = report.of_verdict(Verdict.UNCERTAIN)
    clean = report.of_verdict(Verdict.CLEAN)

    if leaks:
        pages = sorted({f.page_number for f in leaks})
        banner = (
            "<div class='banner leak'>Fake redaction found on page(s) "
            f"{_esc(', '.join(str(p) for p in pages))}. The text below is still "
            "present in the file and can be copied straight out of it.</div>"
        )
    elif unsure:
        banner = (
            "<div class='banner unsure'>No fake redaction found, but parts of this "
            "document could not be audited. See below.</div>"
        )
    else:
        banner = (
            "<div class='banner ok'>No fake redaction found. Every page was audited.</div>"
        )

    body = [
        "<main>",
        "<h1>TrueRedact report</h1>",
        (
            f"<p class='sub'>{_esc(report.file_path)} &middot; {report.page_count} "
            f"page(s) &middot; generated {_esc(report.generated_at)}</p>"
        ),
        banner,
        (
            "<div class='counts'>"
            f"<div><b>{len(leaks)}</b>fake redaction(s)</div>"
            f"<div><b>{len(unsure)}</b>could not audit</div>"
            f"<div><b>{len(clean)}</b>page(s) clean</div>"
            "</div>"
        ),
    ]

    if leaks:
        by_page: dict[int, list[Finding]] = {}
        for finding in leaks:
            by_page.setdefault(finding.page_number, []).append(finding)
        body.append("<h2>Findings</h2>")
        body += [
            _leak_card(number, found, _page_for(doc, number))
            for number, found in sorted(by_page.items())
        ]

    if unsure:
        body.append("<h2>Could not be audited</h2>")
        body.append("<div class='card'><ul class='plain'>")
        for finding in unsure:
            text = (
                f" — text in question: <code>{_esc(finding.recovered_text)}</code>"
                if finding.recovered_text
                else ""
            )
            body.append(
                f"<li>Page {finding.page_number}: {_esc(finding.reason)}{text}</li>"
            )
        body.append("</ul></div>")

    body += [
        (
            "<footer>Findings are structural facts read from the PDF's own objects — "
            "which text span sits at which coordinates, painted in which order, under "
            "which shape. No OCR, no image analysis, no machine learning, no network "
            "access. Text hidden under a raster image, and redactions drawn as "
            "non-rectangular paths, are not detected; pages with no text layer cannot "
            "be audited at all.</footer>"
        ),
        "</main>",
    ]

    return (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>TrueRedact — {_esc(Path(report.file_path).name)}</title>"
        f"<style>{_STYLE}</style></head><body>"
        + "".join(body)
        + "</body></html>\n"
    )


def write(report: ScanReport, doc: pymupdf.Document, path: str | Path) -> None:
    Path(path).write_text(render(report, doc), encoding="utf-8")
