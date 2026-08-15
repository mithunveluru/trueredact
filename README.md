# TrueRedact

An offline tool that detects **fake PDF redactions** — the common failure where an
opaque black box is drawn *over* sensitive text instead of removing it, leaving the
text fully extractable. Reads the PDF's object structure, not its rendered pixels:
every finding is a reproducible structural fact, not an inference. No OCR, no AI,
and nothing ever leaves your machine.

Use it from a terminal, or drag a file onto a window.

## Install

Python 3.11+. One runtime dependency (PyMuPDF), pinned.

```bash
./install.sh
```

This installs the `trueredact` command for your user and adds a **TrueRedact**
entry to your applications menu, so the drag-and-drop window can be opened
without a terminal. Nothing is installed system-wide and nothing needs root.

Uses `pipx` when available, otherwise a private environment under
`~/.local/share/trueredact` — Debian and Ubuntu refuse user-site installs into
the system Python (PEP 668), and a dedicated environment sidesteps that without
touching it.

Remove it again with `./install.sh --uninstall`.

<details>
<summary>Manual install instead</summary>

```bash
python -m venv .venv && source .venv/bin/activate
pip install .                # or: pip install -e ".[dev]" to develop
```
</details>

## Demo

```bash
./demo.sh
```

Builds three PDFs that look identical when printed — one clean, one redacted
properly, one redacted the way everyone actually does it — scans each, and then
proves the point by dumping the "redacted" text straight back out of the file.
Runs in under a second.

## The easy way: drag and drop

Open **TrueRedact** from your applications menu, or run:

```bash
trueredact ui
```

A window opens in your browser. Drop a PDF on it and you get a plain-English
answer — *Not safe to send*, *Nothing hidden found*, or *Could not fully check
this file* — plus the exposed text and a button to view the page with the leak
highlighted, or save the full report. **Quit TrueRedact** at the bottom stops it,
so there is nothing left running afterwards.

The file never leaves your computer: the page is served from `127.0.0.1` only, and
the tool never makes an outbound connection to anything.

## Usage from the terminal

```bash
trueredact scan file.pdf                 # human-readable summary
trueredact scan file.pdf --json out.json # machine-readable report
trueredact scan file.pdf --html out.html # self-contained visual report
trueredact scan file.pdf -v              # list every page, including clean ones
```

The HTML report is one file with no external dependencies — inline CSS,
base64-embedded page previews, no JavaScript, nothing to fetch. It shows each
flagged page with the covering shape and the still-extractable text highlighted
on top of it, which is the artifact to hand to someone who doesn't read JSON.

Example, on a PDF where a black box was drawn over a name instead of removing it:

```text
scanned leaky.pdf (1 page(s))

LEAK  page 1: 1 text span(s) painted before an opaque shape (fill rgb(0.00, 0.00,
      0.00), opacity 1.00) that covers 100%-100% of each, and whose text is still
      extractable
        hidden text: 'John Smith, SSN 000-00-0000'
        region: (45.0, 45.0) to (250.0, 68.0)

FAKE REDACTION FOUND: 1 on page(s) 1. The text above is still in the file.
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | no fake redaction found, and every page was audited |
| `1` | fake redaction found |
| `2` | the scan could not run (missing file, not a PDF, encrypted, over a limit) |
| `3` | no fake redaction found, but some pages could not be audited |

`3` is separate from `0` on purpose. A scanned PDF with no text layer cannot be
checked at all, and saying "all clear" about a page nobody read would be worse
than saying nothing. In CI, fail on `1` and warn on `3`.

### Limits

`--max-pages` (default 500) and `--max-file-size-mb` (default 100) are checked
before parsing begins, so a pathological file cannot hang the process.

> **Status: MVP complete** (all 6 phases), plus a local drag-and-drop UI and
> detection of unapplied `/Redact` marks added afterwards. Read
> [docs/FINAL-REVIEW.md](./docs/FINAL-REVIEW.md) for what is and isn't proven —
> in particular, a leak result is strong evidence but a clean result is weaker:
> detection has been validated for precision on 4,481 real pages, while recall has
> only been demonstrated against PDFs this project generated.

## What it does not do

- No OCR. A scanned page with no text layer is reported `uncertain`, never guessed at.
- Redactions drawn as non-rectangular filled paths are not detected — including
  rectangles painted under a rotating transform, which PDF records as line segments.
- Text hidden under a raster **image** is reported `uncertain` — PDF images carry no
  recoverable paint order, so we cannot tell whether the image is above or below.
- Redaction *marks* left unapplied (`/Redact`) and black boxes drawn as `/Square`
  annotations **are** detected. Other annotation types are not examined.
- No outbound network access, ever. `scan` opens no socket at all; `ui` listens on
  `127.0.0.1` so your own browser can reach it, and sends nothing anywhere.
  See [docs/DECISIONS.md](./docs/DECISIONS.md).

**It finds the most common redaction mistake. It does not prove a document is
safe.** A clean result means nothing suspicious was found, not that nothing is
there.

### Auditing hostile documents

The tool parses untrusted PDFs with MuPDF, a large C library — that is its real
attack surface, and no Python-side care changes it. The dependency is version-pinned
and audited in CI (`pip-audit`, currently zero known vulnerabilities). If you are
auditing documents from an untrusted source, run this inside a container or VM.

## Development

```bash
ruff check .
pytest
```
