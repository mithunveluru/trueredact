# Security Policy

## Supported versions

The `main` branch is the only supported version. TrueRedact is a local tool with
no server component and no auto-update; upgrading means pulling and reinstalling.

## Reporting a vulnerability

Report privately through GitHub's [private vulnerability
reporting](https://github.com/mithunveluru/trueredact/security/advisories/new).
Please do not open a public issue for a security problem.

Include the PDF or a minimal file that reproduces it if you can. A reproducer is
worth more than a description here, because almost everything in scope is a
parsing question.

Expect an acknowledgement within a week.

## Scope

TrueRedact parses untrusted, potentially adversarial PDFs by design — that is what
the tool is for. The following are in scope:

- A crafted PDF that makes the tool hang, exhaust memory, or crash.
- A crafted PDF whose recovered text escapes escaping in the HTML report.
- Anything reachable over the loopback socket that `trueredact ui` binds, from a
  page in the user's browser: a bypass of the `Host` check, the per-run token, the
  size cap, or the connection timeout.
- Any code path that initiates an outbound network connection. There should be
  none, and one would be a serious finding.
- A path where a leak the detector established is not reported to the user.

Out of scope:

- Vulnerabilities in MuPDF itself. Report those to
  [the MuPDF project](https://mupdf.com/); we track the pin and will bump it.
- A missed detection covered by a documented limitation in the README's "What it
  does not do". Those are scope boundaries, not vulnerabilities — but tell us
  anyway if you have a real-world sample, because recall evidence is what the
  project most lacks.

## Auditing hostile documents

The real attack surface is MuPDF, a large C library being fed attacker-controlled
bytes. No Python-side care changes that. The mitigations are a pinned dependency,
`pip-audit` in CI, and caps enforced before parsing. If you are auditing documents
from an untrusted source, run this inside a container or a VM.
