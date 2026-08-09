"""Command-line entry point: argument parsing, orchestration, and exit codes.

A thin shell over `core`. Everything decidable lives in the pure modules; this file
only wires them together and decides how to say the result out loud.
"""

import argparse
import sys
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

from .core import report_html, report_json
from .core.detector import detect_document
from .core.extractor import extract_document
from .core.loader import DEFAULT_MAX_FILE_SIZE_MB, DEFAULT_MAX_PAGES, LoadError, load
from .core.models import Finding, ScanReport, Verdict

EXIT_CLEAN = 0
EXIT_LEAK = 1
EXIT_ERROR = 2
EXIT_UNCERTAIN = 3
"""Scan completed and found no leak, but some pages could not be audited.

Deliberately distinct from EXIT_CLEAN: reporting "all clear" for a document whose
pages we could not read would be exactly the false assurance this tool exists to
prevent. See DECISIONS.md.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trueredact",
        description="Detect text left extractable underneath 'redaction' shapes in a PDF.",
        epilog=(
            f"exit codes: {EXIT_CLEAN} no leak found, {EXIT_LEAK} leak found, "
            f"{EXIT_ERROR} scan could not run, {EXIT_UNCERTAIN} no leak found but "
            "some pages could not be audited"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan a PDF for fake redactions")
    scan.add_argument("pdf", help="path to the PDF file to audit")
    scan.add_argument("--json", metavar="PATH", help="write a JSON report to PATH")
    scan.add_argument(
        "--html", metavar="PATH", help="write a self-contained HTML report to PATH"
    )
    scan.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"refuse documents longer than this (default: {DEFAULT_MAX_PAGES})",
    )
    scan.add_argument(
        "--max-file-size-mb",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE_MB,
        help=f"refuse files larger than this (default: {DEFAULT_MAX_FILE_SIZE_MB})",
    )
    scan.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="list every page, including clean ones, and show full tracebacks",
    )

    ui = sub.add_parser(
        "ui", help="open a drag-and-drop window for checking files without the terminal"
    )
    ui.add_argument(
        "--port", type=int, default=0, help="port to listen on (default: pick a free one)"
    )
    ui.add_argument(
        "--no-browser", action="store_true", help="print the address instead of opening it"
    )
    ui.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"refuse documents longer than this (default: {DEFAULT_MAX_PAGES})",
    )
    ui.add_argument(
        "--max-file-size-mb",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE_MB,
        help=f"refuse files larger than this (default: {DEFAULT_MAX_FILE_SIZE_MB})",
    )
    return parser


def build_report(doc: pymupdf.Document, file_path: str) -> ScanReport:
    """Extract and detect over an already-opened document."""
    return ScanReport(
        file_path=file_path,
        page_count=doc.page_count,
        findings=tuple(detect_document(extract_document(doc))),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def scan_file(path: str, *, max_pages: int, max_file_size_mb: int) -> ScanReport:
    """Load, extract, detect, close. Raises LoadError if the document won't open.

    The HTML report needs the document still open to render page previews, so
    `main` manages the document's lifetime itself rather than calling this.
    """
    doc = load(path, max_pages=max_pages, max_file_size_mb=max_file_size_mb)
    try:
        return build_report(doc, str(path))
    finally:
        doc.close()


def _render_finding(finding: Finding) -> list[str]:
    label = {
        Verdict.FAKE_REDACTION: "LEAK",
        Verdict.UNCERTAIN: "?",
        Verdict.CLEAN: "ok",
    }[finding.verdict]
    lines = [f"{label:<4}  page {finding.page_number}: {finding.reason}"]
    if finding.recovered_text:
        # Only call it "hidden" where we established that it is.
        caption = "hidden text" if finding.verdict is Verdict.FAKE_REDACTION else "text in question"
        lines.append(f"        {caption}: {finding.recovered_text!r}")
    if finding.shape is not None:
        x0, y0, x1, y1 = finding.shape.bbox
        lines.append(f"        region: ({x0:.1f}, {y0:.1f}) to ({x1:.1f}, {y1:.1f})")
    return lines


def render_summary(report: ScanReport, *, verbose: bool) -> str:
    leaks = report.of_verdict(Verdict.FAKE_REDACTION)
    unsure = report.of_verdict(Verdict.UNCERTAIN)
    clean = report.of_verdict(Verdict.CLEAN)

    lines = [f"scanned {report.file_path} ({report.page_count} page(s))", ""]
    shown = report.findings if verbose else leaks + unsure
    for finding in shown:
        lines.extend(_render_finding(finding))
    if shown:
        lines.append("")

    if leaks:
        pages = sorted({f.page_number for f in leaks})
        lines.append(
            f"FAKE REDACTION FOUND: {len(leaks)} on page(s) "
            f"{', '.join(str(p) for p in pages)}. The text above is still in the file."
        )
    else:
        lines.append("no fake redaction found")
    if unsure:
        lines.append(f"{len(unsure)} page-level finding(s) could not be audited")
    lines.append(f"{len(clean)} page(s) clean")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # MuPDF's *error* channel is owned by the extractor's callback, which turns it
    # into an UNCERTAIN finding with context — so errors no longer reach stderr at
    # all. Its *warning* channel is separate, still prints, and is pure noise here:
    # 3.26% of real pages emit cosmetic font warnings the tool deliberately
    # ignores. Silenced unless the user asks to see them.
    if not getattr(args, "verbose", False):
        pymupdf.TOOLS.mupdf_display_warnings(False)

    if args.command == "ui":
        # Imported here so the common `scan` path does not pay for the server.
        from . import web

        return web.serve(
            port=args.port,
            open_browser=not args.no_browser,
            max_pages=args.max_pages,
            max_file_size_mb=args.max_file_size_mb,
        )

    doc = None
    try:
        doc = load(
            args.pdf,
            max_pages=args.max_pages,
            max_file_size_mb=args.max_file_size_mb,
        )
        report = build_report(doc, str(args.pdf))

        # Written while the document is still open: HTML previews need to render
        # pages from it.
        if args.json:
            report_json.write(report, args.json)
            print(f"wrote JSON report to {Path(args.json)}")
        if args.html:
            report_html.write(report, doc, args.html)
            print(f"wrote HTML report to {Path(args.html)}")
    except LoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"error: could not write report: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - never show a raw traceback by default
        if args.verbose:
            traceback.print_exc()
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print("re-run with -v for a full traceback", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if doc is not None:
            doc.close()

    print(render_summary(report, verbose=args.verbose))

    if report.has_leak:
        return EXIT_LEAK
    if report.has_uncertainty:
        return EXIT_UNCERTAIN
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
