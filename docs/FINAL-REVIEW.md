# TrueRedact — Final Engineering Review

A senior-engineer review of the finished MVP, written to be defended rather than
to sell. The short answer to "is this reliable enough for its intended use" is at
the bottom, and it is qualified.

---

## What was built

A single offline Python CLI, ~1,355 lines of source and ~1,658 lines of tests.

```text
PDF → loader (validate, cap) → extractor (PyMuPDF → domain types)
    → detector (pure, deterministic) → stdout / JSON / HTML
```

| Module | Lines | Role |
|---|---|---|
| `core/loader.py` | 79 | open + validate, enforce caps before parsing |
| `core/extractor.py` | 299 | PyMuPDF → `PageContent`; the only PDF-aware module |
| `core/detector.py` | 291 | the algorithm; no I/O, no PyMuPDF import |
| `core/models.py` | 148 | frozen dataclasses shared by both sides |
| `core/report_json.py` | 61 | the external data contract |
| `core/report_html.py` | 284 | self-contained visual report |
| `cli.py` | 193 | argument parsing, orchestration, exit codes |

128 tests, all passing; `ruff` clean. 19 fixtures, every one generated from source
code rather than committed as a binary.

Delivered against the plan: detection, `--json`, `--html`, rotation, nested
XObjects, exit codes, per-page fault isolation, a fixture suite, and a demo.

---

## What was experimentally validated

This is the part that matters, and it is where the project spent most of its
effort.

**The load-bearing assumption.** PyMuPDF's `seqno` is a single page-local counter
shared by `get_texttrace()` and `get_drawings()`, reproducing content-stream order
exactly. Validated across **4,481 pages / 192 documents / 33 producers** — Word
2010–2024, Google Docs (Skia), LibreOffice, macOS Quartz, Acrobat Distiller, Adobe
PDF Library, pdfTeX, ReportLab, iText, OpenPDF, FPDF, TCPDF, Ghostscript,
Aspose.Words, PowerPoint, iLovePDF. Text↔shape `seqno` collisions occur on 0.25% of
pages, and only 0.07% involve a *filled* shape — all of them 15–51 pt² glyph
fragments, an order of magnitude below the 200 pt² candidate floor. The reserved
fallback (hand-writing a content-stream parser) was not needed.

**Two planned mechanisms were measured unnecessary and deleted**: the rotation
transform (both APIs already report unrotated coordinates, byte-identical across
`/Rotate` 0/90/180/270) and XObject recursion (MuPDF flattens form XObjects into
page space with `seqno` correctly interleaved). Both are pinned by regression tests
so a future MuPDF change fails loudly.

**False positives were measured, not assumed.** The first working detector was run
over the corpus and every finding ground-truthed by rendering the page:

| Stage | Findings | Documents |
|---|---|---|
| first working detector | 1,598 | 14 |
| + clip-aware and frame-aware extraction | 44 | 7 |
| + repainted-text rule | **10** | **4** |

All 10 survivors were verified structurally correct. **Zero confirmed algorithm
false positives across 4,481 real pages.**

**The coordinate mapping for the HTML report** was verified against actual rendered
pixel positions at all four rotations before the report code was written.

**Packaging** was verified in a clean-room copy (no `.git`, no `.venv`, no built
fixtures) with a non-editable `pip install`: console script on PATH, 128 tests pass
against the installed package, `demo.sh` runs in 0.6 s.

### What was NOT validated — read this before trusting the tool

**The corpus contained no known real-world fake redaction.** Every document tested
was an ordinary file already on the development machine — resumes, lecture slides,
textbooks, job descriptions. None was a court filing or government release with a
confirmed, documented redaction failure.

The consequence is asymmetric and important:

- **Precision is well evidenced.** 4,481 real pages, zero confirmed false positives.
- **Recall is evidenced only synthetically.** Every true positive the tool has ever
  caught was a PDF this project generated, or an incidental artifact (a covered
  page number, a form placeholder under a photo). It has never been shown to catch
  a fake redaction produced by a human using Acrobat, Word, or Preview with intent
  to redact.

That gap is the single largest open risk. See "Next sensible improvements".

---

## Known limitations

All are deliberate scope boundaries, each recorded in `DECISIONS.md` with the
alternative that was rejected.

| Limitation | Consequence |
|---|---|
| **No OCR.** A page with no text layer cannot be audited | Reported `UNCERTAIN`, never guessed. ~19% of real-world pages |
| **Raster-image covers are not detected.** `get_image_info()['number']` is an index, not a paint order — measured wrong | Text substantially covered by an image is `UNCERTAIN`, not `FAKE_REDACTION` |
| **Only rectangles count.** Non-`re` filled paths are skipped | A redaction drawn as a polygon, a rounded blob, **or a rectangle under a rotating transform** is missed |
| **Annotation-based redaction is not examined** | `/Redact` and `/Square` annotations are invisible to the tool; only content-stream drawing is read |
| **No incremental-update forensics** | A prior, un-redacted revision left in the file bytes is not detected |
| **Single-threaded, one file per invocation** | No batch or folder scanning |
| **`seqno` ties are unresolvable** | Reported `UNCERTAIN` rather than guessed (0.07% of pages) |

---

## False-positive and false-negative risks

### False positives — low, and measured

Zero confirmed across 4,481 pages. The residual risk is *semantic*, not technical:
the tool reports the structural fact "extractable text sits under an opaque
rectangle painted later", which is occasionally true of things nobody meant to
redact — a covered page number, a form placeholder label under a pasted photo. All
10 real-corpus findings are of this kind. The tool deliberately refuses to judge
whether a covered string *matters*; it reports the evidence and the recovered text.

Users scanning slide decks and forms will see some of these. The recovered text
makes them trivially dismissible by eye.

### False negatives — the larger risk, and less measured

Ranked by my estimate of real-world likelihood:

1. **The repainted-text rule.** If a covered string also appears later on the same
   page, the finding is suppressed. This removed 34 false positives, but it will
   also suppress a genuine redaction of a name that happens to recur on the page —
   for example a document redacting one instance of a name it prints elsewhere.
   This is the most aggressive heuristic in the codebase and the one I would
   revisit first with real-world redaction samples.
2. **Non-rectangular and tilted covers.** A redaction box drawn at an angle is
   recorded as four line segments and is invisible. Plausible in tools that let a
   user rotate an annotation.
3. **Image covers.** Downgraded to `UNCERTAIN` — honest, but a user skimming for
   red text will miss it.
4. **Annotation-based redaction.** Entirely out of scope.
5. **The 85% coverage threshold.** A box covering 80% of a span hides most of it
   but is not reported. Chosen to avoid implicating adjacent columns; untested
   against real redaction geometry.
6. **The 200 pt² area floor.** A redaction of a single covered initial or a short
   ID could fall below it.

The tool errs toward false negatives by design at every ambiguous point — a `seqno`
tie, a degenerate bbox, a frame path. For a forensics tool that is the correct
direction (never accuse on ambiguous evidence), but it means **a clean result is
weaker evidence than a leak result**. `exit 0` means "we found nothing", not "there
is nothing".

---

## Important architectural decisions

1. **`seqno` over a hand-written content-stream parser** — measured, not assumed;
   saved implementing a chunk of a PDF interpreter to solve a 0.07% problem the
   area filter already excludes.
2. **The detector is pure.** No I/O, no PyMuPDF import. 48 of its tests use plain
   Python objects. When an integration test fails, the bug is in extraction — that
   separation paid for itself repeatedly.
3. **`ShapeObject` means "a solid filled rectangle as actually painted"** —
   clip-intersected, from the path's own `re` items, frames and strokes excluded.
   Putting this in the extractor rather than the detector kept the algorithm pure
   while eliminating 99% of false positives.
4. **No `confidence` score.** Findings publish the measurements instead. An
   uncalibrated 0–1 number would read as a probability and undermine every
   verifiable number beside it.
5. **A fourth exit code (`3`)** for "completed, but not everything could be
   audited". Three codes could not express the difference between "nothing
   suspicious" and "we could not check part of this", and 49 of 192 corpus
   documents fall in the latter bucket.
6. **`UNCERTAIN` is a real verdict, not a hedge** — used for unparseable pages, no
   text layer, ambiguous paint order, and image covers. Never for a case the tool
   can actually decide.

---

## Remaining technical debt

Honest inventory, worst first.

1. **CI has never actually run.** `.github/workflows/ci.yml` exists and is
   plausible, but there is no git remote and no commit has been pushed. It is
   unverified configuration. The repository is `git init`-ed with **zero commits**.
2. **HTML report size is unbounded in code.** Bounded in practice (worst real case
   0.52 MB, and previews are per-page and leak-only), but a document with hundreds
   of genuinely flagged pages would produce a very large file.
3. **`--json` and `--html` share one error handler**, so the message says "could not
   write report" without naming which. The failing path is in the message.
4. **`PageContent.width`/`height` are still unconsumed.** Correct now, but nothing
   reads them; they are there for the reporters and currently unused.
5. **No test exercises the real size/page caps at scale** — only that they trigger.
6. **Extraction is serialized across threads** by the diagnostics lock. Correct, but
   it means page-level parallelism would gain nothing while that stands. Per-file
   parallelism (the natural first optimization if batch scanning arrives) is
   unaffected, since each process gets its own MuPDF context.

### Resolved after the first draft of this review

Both of the items previously ranked worst have been fixed rather than documented:

- ~~String-matching MuPDF's warning text.~~ Replaced with a callback on MuPDF's
  **error channel**, which separates parse failures from cosmetic font warnings
  structurally. The decision no longer depends on message wording at all, so there
  is nothing left to silently reword; a test asserts that `extract_page` contains no
  message-text branching. Measured: the error channel fires on 1 page in 4,419
  (0.02%) versus 144 (3.26%) for warnings, and re-running the corpus changed exactly
  one page's verdict — a broken colour profile the old check had missed. Missing
  callback support now fails loudly at import instead of degrading silently.
- ~~The warning buffer is process-global.~~ The capture window is serialized with a
  lock, making concurrent use correct. Verified with a test that forces the
  interleaving deterministically — the first version of that test drove real
  concurrent extraction and passed with the lock removed, so it was measuring
  nothing and was replaced.

---

## Performance observations

Measured over 4,481 pages, not estimated.

| Metric | Value |
|---|---|
| Mean | ~15 ms/page |
| Worst observed | ~70 ms/page (531-page vector-heavy prospectus) |
| Typical 50-page document | well under 1 s |
| Full corpus (192 docs, 4,481 pages) | ~75 s |
| Test suite | 0.6 s |
| Demo | 0.6 s |

The architecture's target ("10–50 pages well under 3 seconds") is met with
substantial margin. **No optimization was performed and none is warranted** —
detection is O(shapes × spans) per page and the heavy lifting is inside MuPDF's C
core. Adding a spatial index would be pure complexity against a profiler that has
nothing to say.

---

## Security observations

**Verified, not asserted:**

- **No network capability.** Grepped: no `socket`, `urllib`, `http`, `requests`,
  or any transport import anywhere in `src/`. The complete import surface is
  `argparse`, `base64`, `html`, `json`, `sys`, `traceback`, `dataclasses`,
  `datetime`, `enum`, `pathlib`, `collections.abc`, and `pymupdf`.
- **No dynamic execution.** No `eval`, `exec`, `subprocess`, `os.system`,
  `__import__`.
- **Dependency audit clean.** `pip-audit` reports **zero known vulnerabilities in
  PyMuPDF 1.28.2**. Now wired into CI.
- **Caps enforced before parsing.** Size and page-count are checked from `stat` and
  the header before MuPDF does real work.
- **Untrusted output is escaped.** Recovered PDF text goes into an HTML document;
  it is `html.escape`d, and a test embeds `<script>alert(1)</script>` in a PDF and
  asserts it comes out inert. The report contains no JavaScript and no external
  references — asserted by test.
- **Per-page fault isolation.** One malformed page cannot abort a scan.
- **Encrypted documents are refused up front**, not caught later by accident.

**Residual risk, stated plainly:** the real attack surface is MuPDF itself. This
tool feeds attacker-controlled bytes into a large C library, and no amount of
Python-side care changes that. The mitigations are the version pin, the
pre-parse caps, and `pip-audit` in CI. A user auditing genuinely hostile documents
should run this in a sandbox. That belongs in the README and is not there yet.

---

## What should NOT be built yet

- **OCR / image analysis.** Would roughly double the surface and replace verifiable
  structural facts with inference — the opposite of what makes this tool worth
  trusting.
- **A web UI, even localhost-only.** A second surface to build, test, and secure,
  for an audience the HTML report already serves.
- **Any database.** The tool is stateless: one file in, one report out. There is no
  consumer for stored history, so any schema now would be a guess.
- **Batch/folder scanning with parallelism.** No measured need; a shell `for` loop
  covers it, and per-file parallelism would only matter at a corpus scale nobody
  has asked for.
- **Confidence scores, in any form.** Settled; re-adding one would reintroduce the
  false precision deliberately removed.
- **A hand-written content-stream parser.** Validated as unnecessary. Only
  revisit if image paint order becomes a real requirement.

---

## Next sensible improvements

In priority order, by value per unit of risk removed:

1. **Test against real, documented redaction failures.** The single highest-value
   next step, and the one that closes the biggest evidence gap. Publicly known
   botched-redaction PDFs exist (court filings, government releases). Until the
   tool catches one it did not generate itself, recall is unproven.
2. **Redact something by hand in Acrobat, Word, Preview, and LibreOffice** using
   each tool's "black box" affordance, and confirm detection. Cheap, and directly
   attacks the recall gap and the repainted-text rule's blast radius.
3. **Annotation-based redaction** (`/Redact`, `/Square`). A self-contained second
   candidate source walking `/Annots`, and a real-world pattern the tool is
   currently blind to.
4. **Image paint order** via `Do`-operator parsing, upgrading image covers from
   `UNCERTAIN` to a real verdict. Bounded work, well understood.
5. **Push to a remote and confirm CI is green.** Cheap, removes unverified config.
6. **A sandboxing note in the README** for users auditing hostile documents.
7. **Incremental-update byte forensics** — a distinct algorithm, worth its own
   phase if pursued.

---

## Final architecture assessment

The architecture is sound and, more importantly, it is *small*. Seven modules, one
runtime dependency, no framework, no persistence, no network, no concurrency, no
plugin system, no configuration layer. Every abstraction present is load-bearing.

The one structural decision that carried the most weight is the **purity of the
detector**. Because it imports nothing and touches no I/O, its 48 unit tests run on
plain Python objects in milliseconds, and every ambiguity in PDF semantics — clips,
fill rules, frames, coordinate spaces, rotation — was forced into the extractor
where it belongs. When 1,598 false positives appeared, the fix was localized to
extraction and the algorithm did not change at all. That is the boundary paying for
itself.

The blueprint was followed where it was right and overridden where evidence
contradicted it: two planned mechanisms were deleted as unnecessary, one planned
field (`confidence`) was removed as dishonest, and one exit code was added because
three could not express the truth. Each override is recorded with its evidence.

What I would do differently: I would have gone looking for the false positives
*before* declaring the detection algorithm done, rather than after. The Phase 2
"complete" moment came before the corpus run, and the corpus run invalidated it.
The lesson is that a detector's definition of done includes its false-positive
rate on real input, not just green fixtures.

---

## Is this actually reliable enough for its intended MVP use?

**For its stated MVP purpose — a fast, offline, pre-send check on documents you
redacted yourself — yes, with one honest caveat.**

What supports that:

- The core assumption is validated on 4,481 real pages across 33 producers, not
  assumed.
- Zero confirmed false positives on that corpus. When it says "leak", it has been
  right every time it has been checked.
- Every finding is a reproducible structural fact with the recovered text attached,
  so a user can confirm it in seconds without trusting the tool.
- It refuses to guess. Unauditable pages get `UNCERTAIN` and a distinct exit code
  rather than a false all-clear.
- It cannot leak the document being audited, because it has no code path that opens
  a socket.

The caveat, stated as plainly as I can:

> **A `FAKE_REDACTION` result is strong evidence. A clean result is weak evidence.**
> Recall has been demonstrated only against PDFs this project generated. The tool
> has never been shown to catch a fake redaction made by a human with real
> redaction software, and it is blind by construction to image covers, annotation
> redactions, tilted boxes, and leftover incremental-update revisions.

So: **trustworthy as a positive detector, not yet trustworthy as a clean bill of
health.** It should be described to users that way — "this finds the most common
redaction mistake" — and not as "this proves your document is safe". The README's
"What it does not do" section carries that message and should stay prominent.

I would not call this production-ready for high-stakes legal use — a law firm
clearing a filing before release needs the recall evidence in item 1 above first.
For an individual checking their own résumé or a journalist sanity-checking a
document before publishing, it is genuinely useful today, and its failure mode is
silence rather than a wrong accusation.
