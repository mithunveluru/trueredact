import pytest
from fixtures import generate_fixtures as fx

from redaction_xray.core.loader import LoadError, load


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(fx.fake_redacted())
    return path


def test_opens_a_valid_pdf(pdf):
    doc = load(pdf)
    assert doc.page_count == 1
    doc.close()


def test_missing_file(tmp_path):
    with pytest.raises(LoadError, match="cannot read"):
        load(tmp_path / "nope.pdf")


def test_directory_is_not_a_file(tmp_path):
    with pytest.raises(LoadError, match="not a file"):
        load(tmp_path)


def test_empty_file(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(LoadError, match="empty"):
        load(path)


def test_non_pdf_content_is_rejected_regardless_of_extension(tmp_path):
    path = tmp_path / "actually_text.pdf"
    path.write_bytes(b"this is not a PDF, it just claims to be" * 10)
    with pytest.raises(LoadError, match="not a valid PDF"):
        load(path)


def test_header_may_be_preceded_by_junk(tmp_path):
    """The spec allows leading bytes before %PDF-, so we search a window."""
    path = tmp_path / "offset.pdf"
    path.write_bytes(b"\n" * 32 + fx.fake_redacted())
    doc = load(path)
    assert doc.page_count == 1
    doc.close()


def test_oversized_file(pdf):
    with pytest.raises(LoadError, match="over the 0 MB limit"):
        load(pdf, max_file_size_mb=0)


def test_too_many_pages(pdf):
    with pytest.raises(LoadError, match="over the 0 page limit"):
        load(pdf, max_pages=0)


def test_encrypted_pdf_is_rejected_up_front(tmp_path):
    """Encrypted docs open fine and only fail at page load — catch it in the loader."""
    path = tmp_path / "locked.pdf"
    path.write_bytes(fx.encrypted())
    with pytest.raises(LoadError, match="encrypted"):
        load(path)


def test_size_is_checked_before_parsing(tmp_path):
    """A huge non-PDF must be rejected on size, never handed to the parser."""
    path = tmp_path / "big.bin"
    path.write_bytes(b"\0" * (2 * 1024 * 1024))
    with pytest.raises(LoadError, match="over the 1 MB limit"):
        load(path, max_file_size_mb=1)
