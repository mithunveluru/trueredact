"""Open and validate a PDF before any real parsing work happens.

Input PDFs are untrusted, so the caps here are checked in cheapest-first order:
stat the file, sniff the header, and only then hand bytes to the PDF parser.
"""

from pathlib import Path

import pymupdf

DEFAULT_MAX_FILE_SIZE_MB = 100
DEFAULT_MAX_PAGES = 500

# The spec permits leading junk before the header, so search a window rather than
# requiring the file to start with it.
_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024


class LoadError(Exception):
    """A document that cannot be audited. The message is shown to the user verbatim.

    Deliberately one exception type, not a hierarchy: every caller does the same
    thing with it (print it, exit 2), so variants would be distinction without use.
    """


def load(
    path: str | Path,
    *,
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> pymupdf.Document:
    """Return an opened document, or raise LoadError with a user-facing message."""
    path = Path(path)

    try:
        stat = path.stat()
    except OSError as exc:
        raise LoadError(f"cannot read {path}: {exc.strerror or exc}") from exc
    if not path.is_file():
        raise LoadError(f"not a file: {path}")

    size_mb = stat.st_size / (1024 * 1024)
    if size_mb > max_file_size_mb:
        raise LoadError(
            f"file is {size_mb:.1f} MB, over the {max_file_size_mb} MB limit "
            f"(raise it with --max-file-size-mb)"
        )
    if stat.st_size == 0:
        raise LoadError(f"file is empty: {path}")

    with path.open("rb") as fh:
        head = fh.read(_HEADER_WINDOW)
    if _HEADER not in head:
        raise LoadError(f"not a valid PDF file (no {_HEADER.decode()} header): {path}")

    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise LoadError(f"could not open PDF: {type(exc).__name__}: {exc}") from exc

    # Encrypted documents open without complaint and only fail later, at page load.
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise LoadError(f"file is encrypted — cannot audit: {path}")

    if doc.page_count > max_pages:
        pages = doc.page_count
        doc.close()
        raise LoadError(
            f"document has {pages} pages, over the {max_pages} page limit "
            f"(raise it with --max-pages)"
        )
    if doc.page_count == 0:
        doc.close()
        raise LoadError(f"document has no pages: {path}")

    return doc
