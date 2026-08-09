"""Local UI tests, driven over a real loopback socket.

The security properties are tested first and hardest: this is the only component
that opens a socket, in a tool whose whole proposition is that the document never
leaves the machine.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest
from fixtures import generate_fixtures as fx

from trueredact.web import build_server, plain_summary, scan_bytes


@pytest.fixture
def server():
    srv, url = build_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, url
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def base_and_token(url):
    base, _, query = url.partition("?")
    return base.rstrip("/"), query.removeprefix("t=")


def post(url, data, *, host=None):
    request = urllib.request.Request(url, data=data, method="POST")
    if host:
        request.add_header("Host", host)
    return urllib.request.urlopen(request, timeout=10)


# ------------------------------------------------------------------- security


def test_binds_loopback_only(server):
    srv, _ = server
    assert srv.server_address[0] == "127.0.0.1", "must never be reachable off-host"


def test_scan_without_the_token_is_refused(server):
    _, url = server
    base, _ = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/scan", fx.fake_redacted())
    assert exc.value.code == 403


def test_scan_with_a_wrong_token_is_refused(server):
    _, url = server
    base, _ = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/scan?t=not-the-token", fx.fake_redacted())
    assert exc.value.code == 403


def test_non_loopback_host_header_is_refused(server):
    """Blocks DNS rebinding: same-origin keys on the name, not the address."""
    _, url = server
    base, token = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/scan?t={token}", fx.clean(), host="evil.example.com")
    assert exc.value.code == 403


def test_oversized_upload_is_refused_before_being_read(server):
    srv, url = server
    base, token = base_and_token(url)
    srv.RequestHandlerClass.max_file_size_mb = 0
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            post(f"{base}/scan?t={token}", fx.fake_redacted())
        assert exc.value.code == 413
    finally:
        srv.RequestHandlerClass.max_file_size_mb = 100


def test_page_declares_no_external_sources(server):
    _, url = server
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode()
        csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    for pattern in ("http://", "https://", "//cdn"):
        assert pattern not in body, f"page references something external: {pattern}"


# --------------------------------------------------------------------- routing


def test_unknown_paths_are_not_served(server):
    _, url = server
    base, _ = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/../etc/passwd", timeout=10)
    assert exc.value.code == 404


def test_empty_upload_is_rejected(server):
    _, url = server
    base, token = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/scan?t={token}", b"")
    assert exc.value.code == 400


def test_non_pdf_upload_reports_a_readable_error(server):
    _, url = server
    base, token = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/scan?t={token}", b"this is not a pdf" * 20)
    assert exc.value.code == 400
    assert "not a valid PDF" in json.loads(exc.value.read())["error"]


# ---------------------------------------------------------------- the verdicts


def scan_via_http(url, pdf_bytes):
    base, token = base_and_token(url)
    with post(f"{base}/scan?t={token}", pdf_bytes) as response:
        return json.loads(response.read())


def test_fake_redaction_is_reported_in_plain_language(server):
    _, url = server
    data = scan_via_http(url, fx.fake_redacted())
    assert data["status"] == "leak"
    assert data["headline"] == "Not safe to send"
    assert fx.SECRET in data["hidden"]
    assert "<!doctype html>" in data["report_html"]


def test_clean_file_is_reported_clean(server):
    _, url = server
    data = scan_via_http(url, fx.clean())
    assert data["status"] == "clean"
    assert data["hidden"] == []


def test_unauditable_file_is_not_called_clean(server):
    """The whole point of the third state survives the translation to plain English."""
    _, url = server
    data = scan_via_http(url, fx.image_cover())
    assert data["status"] == "unknown"
    assert "could not" in data["headline"].lower()


def test_quit_requires_the_token(server):
    _, url = server
    base, _ = base_and_token(url)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base}/quit", b"")
    assert exc.value.code == 403


def test_quit_stops_the_server():
    """Launched from a desktop icon there is no terminal to interrupt."""
    srv, url = build_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base, token = base_and_token(url)

    with post(f"{base}/quit?t={token}", b"") as response:
        assert json.loads(response.read())["stopped"] is True

    thread.join(timeout=5)
    assert not thread.is_alive(), "server did not stop"
    srv.server_close()


def test_the_page_itself_is_served(server):
    _, url = server
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode()
    assert response.status == 200
    assert "Drop a PDF here" in body


# ------------------------------------------------------- summary translation


@pytest.mark.parametrize(
    ("builder", "status"),
    [
        (fx.fake_redacted, "leak"),
        (fx.clean, "clean"),
        (fx.properly_redacted, "clean"),
        (fx.image_cover, "unknown"),
    ],
)
def test_plain_summary_matches_the_underlying_verdict(builder, status):
    summary, _ = scan_bytes(builder(), max_pages=500, max_file_size_mb=100)
    assert summary["status"] == status


def test_summary_never_promises_safety_on_a_clean_result():
    """A clean result is weaker evidence than a leak; the wording must not oversell."""
    summary, _ = scan_bytes(fx.clean(), max_pages=500, max_file_size_mb=100)
    assert "not a guarantee" in summary["detail"]


def test_uploaded_bytes_are_not_left_on_disk(tmp_path, monkeypatch):
    """The upload lives in a private temp dir that is removed after the scan."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    scan_bytes(fx.fake_redacted(), max_pages=500, max_file_size_mb=100)
    assert list(tmp_path.iterdir()) == [], "upload was left behind"


def test_plain_summary_is_pure(monkeypatch):
    """plain_summary must not need a document or touch disk."""
    from trueredact.core.models import Finding, ScanReport, Verdict

    report = ScanReport(
        file_path="x.pdf",
        page_count=1,
        findings=(Finding(page_number=1, verdict=Verdict.CLEAN, reason="nothing"),),
        generated_at="2026-01-01T00:00:00Z",
    )
    assert plain_summary(report)["status"] == "clean"
