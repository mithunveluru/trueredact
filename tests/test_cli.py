import json
import subprocess

import pytest
from fixtures import generate_fixtures as fx

from trueredact.cli import (
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_LEAK,
    EXIT_UNCERTAIN,
    main,
)


@pytest.fixture
def pdf(tmp_path):
    def build(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)

    return build


def test_installed_entry_point_help_exits_zero():
    """Covers the console-script wiring, which in-process tests cannot reach."""
    result = subprocess.run(
        ["trueredact", "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "scan" in result.stdout


def test_fake_redaction_exits_one_and_prints_the_hidden_text(pdf, capsys):
    code = main(["scan", pdf("bad.pdf", fx.fake_redacted())])
    out = capsys.readouterr().out
    assert code == EXIT_LEAK
    assert fx.SECRET in out
    assert "FAKE REDACTION FOUND" in out
    assert "page 1" in out


def test_clean_document_exits_zero(pdf, capsys):
    code = main(["scan", pdf("good.pdf", fx.clean())])
    assert code == EXIT_CLEAN
    assert "no fake redaction found" in capsys.readouterr().out


def test_properly_redacted_document_exits_zero(pdf):
    assert main(["scan", pdf("proper.pdf", fx.properly_redacted())]) == EXIT_CLEAN


def test_unauditable_page_exits_three_not_zero(pdf, capsys):
    """A document we could not fully audit must not report as all-clear."""
    code = main(["scan", pdf("scan.pdf", fx.image_cover())])
    assert code == EXIT_UNCERTAIN
    assert "could not be audited" in capsys.readouterr().out


def test_a_leak_outranks_uncertainty_in_the_exit_code(pdf, tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open("pdf", fx.fake_redacted()))
    doc.insert_pdf(pymupdf.open("pdf", fx.image_cover()))
    data = doc.tobytes()
    doc.close()
    assert main(["scan", pdf("both.pdf", data)]) == EXIT_LEAK


# --------------------------------------------------------------- error handling


def test_missing_file_exits_two_with_a_message_not_a_traceback(tmp_path, capsys):
    code = main(["scan", str(tmp_path / "nope.pdf")])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_non_pdf_file_exits_two(pdf, capsys):
    code = main(["scan", pdf("fake.pdf", b"definitely not a pdf" * 20)])
    assert code == EXIT_ERROR
    assert "not a valid PDF" in capsys.readouterr().err


def test_page_cap_is_enforced_and_names_the_flag(pdf, capsys):
    code = main(["scan", pdf("x.pdf", fx.clean()), "--max-pages", "0"])
    assert code == EXIT_ERROR
    assert "--max-pages" in capsys.readouterr().err


def test_size_cap_is_enforced_and_names_the_flag(pdf, capsys):
    code = main(["scan", pdf("x.pdf", fx.clean()), "--max-file-size-mb", "0"])
    assert code == EXIT_ERROR
    assert "--max-file-size-mb" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--json", "--html"])
def test_unwritable_report_destination_exits_two(pdf, tmp_path, capsys, flag):
    missing = tmp_path / "no-such-dir" / "report.out"
    code = main(["scan", pdf("bad.pdf", fx.fake_redacted()), flag, str(missing)])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "could not write report" in err
    assert "no-such-dir" in err, "the message must name the path that failed"


def test_no_arguments_is_a_usage_error_not_a_crash():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# ------------------------------------------------------------------ json output


def test_json_report_is_written_and_matches_the_schema(pdf, tmp_path):
    out = tmp_path / "report.json"
    code = main(["scan", pdf("bad.pdf", fx.fake_redacted()), "--json", str(out)])
    assert code == EXIT_LEAK

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["has_leak"] is True
    assert data["page_count"] == 1
    assert data["summary"]["fake_redaction"] == 1
    (finding,) = [f for f in data["findings"] if f["verdict"] == "fake_redaction"]
    assert finding["recovered_text"] == fx.SECRET
    assert finding["shape"]["fill_color"] == [0.0, 0.0, 0.0]
    assert "confidence" not in finding


def test_json_is_written_even_when_the_document_is_clean(pdf, tmp_path):
    out = tmp_path / "report.json"
    assert main(["scan", pdf("good.pdf", fx.clean()), "--json", str(out)]) == EXIT_CLEAN
    assert json.loads(out.read_text(encoding="utf-8"))["has_leak"] is False


# --------------------------------------------------------------------- verbose


def test_verbose_lists_clean_pages_and_plain_output_does_not(pdf, capsys):
    path = pdf("good.pdf", fx.clean())

    main(["scan", path])
    assert "ok    page 1" not in capsys.readouterr().out

    main(["scan", path, "-v"])
    assert "ok    page 1" in capsys.readouterr().out
