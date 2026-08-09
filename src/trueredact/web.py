"""Local drag-and-drop UI for people who do not use a terminal.

Binds to the loopback interface only and makes no outbound calls: the document
being audited never leaves the machine. See DECISIONS.md for why a socket exists
at all, given the rest of the tool has none.

Deliberately built on `http.server` rather than a web framework. One page and one
endpoint do not justify a dependency, and keeping the runtime requirements at
exactly one library is worth more here than routing sugar.
"""

import json
import secrets
import tempfile
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cli import build_report
from .core import report_html
from .core.loader import DEFAULT_MAX_FILE_SIZE_MB, DEFAULT_MAX_PAGES, LoadError, load
from .core.models import ScanReport, Verdict

_MAX_UPLOAD_MB = DEFAULT_MAX_FILE_SIZE_MB


def plain_summary(report: ScanReport) -> dict:
    """Translate a report into the plain-English shape the page renders.

    The forensic vocabulary the CLI uses — paint order, coverage ratio, opacity —
    is exactly what a non-technical reader cannot act on. What they need is
    whether to send the file, and what is exposed if they do.
    """
    leaks = report.of_verdict(Verdict.FAKE_REDACTION)
    unsure = report.of_verdict(Verdict.UNCERTAIN)

    if leaks:
        pages = sorted({f.page_number for f in leaks})
        page_list = ", ".join(str(p) for p in pages)
        return {
            "status": "leak",
            "headline": "Not safe to send",
            "detail": (
                f"Text is hidden underneath a shape on page {page_list}. "
                "Anyone who opens this file can copy it straight out."
            ),
            "hidden": [f.recovered_text for f in leaks if f.recovered_text],
            "pages": pages,
        }

    if unsure:
        pages = sorted({f.page_number for f in unsure})
        return {
            "status": "unknown",
            "headline": "Could not fully check this file",
            "detail": (
                f"Page {', '.join(str(p) for p in pages)} could not be read — usually "
                "a scan or photo rather than real text. Check those pages yourself."
            ),
            "hidden": [],
            "pages": pages,
        }

    return {
        "status": "clean",
        "headline": "Nothing hidden found",
        "detail": (
            "No text is hidden under a shape in this file. This finds the most "
            "common redaction mistake, so it is not a guarantee the file is safe."
        ),
        "hidden": [],
        "pages": [],
    }


def scan_bytes(data: bytes, *, max_pages: int, max_file_size_mb: int) -> tuple[dict, str]:
    """Scan uploaded bytes, returning the plain summary and the full HTML report.

    The upload is written to a private temporary file rather than kept in memory
    or written anywhere predictable: the loader works on paths, and the client's
    filename is never used to build one.
    """
    with tempfile.TemporaryDirectory(prefix="trueredact-") as tmp:
        path = Path(tmp) / "upload.pdf"
        path.write_bytes(data)
        doc = load(path, max_pages=max_pages, max_file_size_mb=max_file_size_mb)
        try:
            report = build_report(doc, "uploaded file")
            return plain_summary(report), report_html.render(report, doc)
        finally:
            doc.close()


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrueRedact</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--muted:#5c6570;--line:#d8dce1;
      --leak:#c0261c;--warn:#9a6700;--ok:#1a7f37;--accent:#2d6cdf}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
     background:var(--bg);color:var(--ink);padding:2rem 1rem;
     font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{width:100%;max-width:38rem}
h1{font-size:1.5rem;margin:0 0 .25rem;text-align:center}
.tag{color:var(--muted);text-align:center;margin:0 0 1.75rem;font-size:.95rem}
#drop{background:var(--card);border:2px dashed var(--line);border-radius:12px;
      padding:3rem 1.5rem;text-align:center;cursor:pointer;transition:.15s}
#drop:hover,#drop:focus-visible{border-color:var(--accent);background:#fbfcff}
#drop.over{border-color:var(--accent);background:#eef4ff}
#drop:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
#drop b{display:block;font-size:1.1rem;margin-bottom:.35rem}
#drop span{color:var(--muted);font-size:.9rem}
input[type=file]{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.note{color:var(--muted);font-size:.85rem;text-align:center;margin-top:1rem}
.card{background:var(--card);border:1px solid var(--line);border-left-width:6px;
      border-radius:10px;padding:1.5rem;margin-top:1.5rem}
.card.leak{border-left-color:var(--leak)}
.card.unknown{border-left-color:var(--warn)}
.card.clean{border-left-color:var(--ok)}
.card.error{border-left-color:var(--muted)}
.card h2{margin:0 0 .5rem;font-size:1.2rem}
.card.leak h2{color:var(--leak)}
.card.unknown h2{color:var(--warn)}
.card.clean h2{color:var(--ok)}
.card p{margin:0 0 1rem;color:var(--muted)}
.exposed{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.95rem;
         background:#fdecea;border-radius:6px;padding:.7rem .9rem;margin:0 0 .6rem;
         color:var(--ink);white-space:pre-wrap;word-break:break-word}
.actions{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.25rem}
button{font:inherit;font-size:.92rem;padding:.55rem 1rem;border-radius:7px;
       border:1px solid var(--line);background:#fff;color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
button.link{background:none;border:none;color:var(--muted);text-decoration:underline;
            padding:.35rem;font-size:.85rem;cursor:pointer}
button.link:hover{color:var(--ink)}
.filename{font-size:.85rem;color:var(--muted);margin:0 0 .75rem;word-break:break-all}
[hidden]{display:none !important}
</style></head><body>
<main>
  <h1>TrueRedact</h1>
  <p class="tag">Check whether a PDF still contains text hidden under a black box.</p>

  <div id="drop" role="button" tabindex="0" aria-describedby="privacy">
    <b>Drop a PDF here</b>
    <span>or press Enter to choose one</span>
  </div>
  <input type="file" id="file" accept="application/pdf,.pdf">
  <p class="note" id="privacy">Nothing is uploaded. The file is checked on this
     computer and never sent anywhere.</p>

  <div id="result" role="status" aria-live="polite"></div>

  <p class="note"><button id="quit" class="link">Quit TrueRedact</button></p>
</main>
<script>
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const drop = document.getElementById("drop");
const input = document.getElementById("file");
const result = document.getElementById("result");
let reportHtml = null, currentName = "report";

const esc = s => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };

function show(kind, headline, detail, hidden, name, withReport) {
  reportHtml = withReport ? reportHtml : null;
  result.innerHTML =
    '<div class="card ' + kind + '">' +
    (name ? '<p class="filename">' + esc(name) + "</p>" : "") +
    "<h2>" + esc(headline) + "</h2>" +
    "<p>" + esc(detail) + "</p>" +
    hidden.map(t => '<div class="exposed">' + esc(t) + "</div>").join("") +
    (withReport
      ? '<div class="actions">' +
        '<button class="primary" id="view">View the page</button>' +
        '<button id="save">Save full report</button></div>'
      : "") +
    "</div>";
  if (withReport) {
    document.getElementById("view").onclick = () => openReport(false);
    document.getElementById("save").onclick = () => openReport(true);
  }
}

function openReport(download) {
  if (!reportHtml) return;
  const url = URL.createObjectURL(new Blob([reportHtml], { type: "text/html" }));
  if (download) {
    const a = document.createElement("a");
    a.href = url;
    a.download = currentName.replace(/\\.pdf$/i, "") + "-report.html";
    a.click();
  } else {
    window.open(url, "_blank");
  }
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function scan(file) {
  if (!file) return;
  currentName = file.name || "report";
  show("", "Checking\\u2026", file.name || "", [], "", false);
  try {
    const res = await fetch("/scan?t=" + encodeURIComponent(TOKEN), {
      method: "POST", body: file,
    });
    const data = await res.json();
    if (!res.ok) {
      show("error", "Could not check this file", data.error || "Unknown problem.", [], file.name, false);
      return;
    }
    reportHtml = data.report_html;
    show(data.status, data.headline, data.detail, data.hidden || [], file.name, true);
  } catch (e) {
    show("error", "Could not check this file", String(e), [], file.name, false);
  }
}

document.getElementById("quit").onclick = async () => {
  try { await fetch("/quit?t=" + encodeURIComponent(TOKEN), { method: "POST" }); } catch (e) {}
  document.body.innerHTML =
    '<main><h1>TrueRedact</h1>' +
    '<p class="tag">Stopped. You can close this tab.</p></main>';
};

drop.onclick = () => input.click();
drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
input.onchange = () => scan(input.files[0]);
["dragenter", "dragover"].forEach(t =>
  drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t =>
  drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => scan(e.dataTransfer.files[0]));
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "TrueRedact"
    sys_version = ""

    token: str = ""
    max_pages: int = DEFAULT_MAX_PAGES
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB

    def log_message(self, *args) -> None:
        """Silence per-request logging; the document name is not ours to print."""

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _loopback_host(self) -> bool:
        """Reject anything not addressed to loopback.

        A hostname that resolves to 127.0.0.1 lets a remote page reach this server
        through the browser and read its responses, since same-origin is keyed on
        the name rather than the address. Checking Host closes that.
        """
        host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]")
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_GET(self) -> None:
        if not self._loopback_host():
            self.send_error(HTTPStatus.FORBIDDEN, "non-loopback host")
            return
        if urllib.parse.urlparse(self.path).path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = _PAGE.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page has no external references; say so rather than rely on it.
        self.send_header("Content-Security-Policy", "default-src 'none'; "
                         "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "img-src data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._loopback_host():
            self.send_error(HTTPStatus.FORBIDDEN, "non-loopback host")
            return

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/scan", "/quit"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        # Without this, any page open in the same browser could post a file here.
        supplied = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
        if not self.token or not secrets.compare_digest(supplied, self.token):
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid session token"})
            return

        if parsed.path == "/quit":
            # Launched from a desktop icon there is no terminal to interrupt, so
            # the page needs a way to stop the server. Shutdown runs on its own
            # thread because it blocks until the serve loop exits.
            self._json(HTTPStatus.OK, {"stopped": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "bad Content-Length"})
            return

        # Checked before reading, so an oversized upload is refused rather than
        # buffered.
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "empty upload"})
            return
        if length > self.max_file_size_mb * 1024 * 1024:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"file is larger than the {self.max_file_size_mb} MB limit"},
            )
            return

        data = self.rfile.read(length)
        try:
            summary, report = scan_bytes(
                data, max_pages=self.max_pages, max_file_size_mb=self.max_file_size_mb
            )
        except LoadError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - one bad upload must not kill the server
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"unexpected {type(exc).__name__} while reading the file"},
            )
            return

        self._json(HTTPStatus.OK, {**summary, "report_html": report})


def build_server(
    *,
    port: int = 0,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB,
) -> tuple[ThreadingHTTPServer, str]:
    """Return a loopback-bound server and the URL that authorizes the page."""
    token = secrets.token_urlsafe(24)
    handler = type(
        "BoundHandler",
        (_Handler,),
        {"token": token, "max_pages": max_pages, "max_file_size_mb": max_file_size_mb},
    )
    # 127.0.0.1, never 0.0.0.0: the socket must not be reachable from the network.
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_port}/?t={token}"
    return server, url


def serve(
    *,
    port: int = 0,
    open_browser: bool = True,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB,
) -> int:
    server, url = build_server(
        port=port, max_pages=max_pages, max_file_size_mb=max_file_size_mb
    )
    # Flushed explicitly: launched from a desktop entry stdout is not a terminal
    # and would otherwise stay block-buffered, hiding the address a user needs if
    # the browser does not open on its own.
    print(f"TrueRedact is running at {url}", flush=True)
    print("Nothing leaves this computer. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


__all__ = ["build_server", "plain_summary", "scan_bytes", "serve"]
