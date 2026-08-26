"""Serialize a ScanReport to the documented JSON contract.

This is an external interface, so the mapping is written out by hand rather than
derived from the dataclasses with `asdict`. Renaming an internal field must not
silently change the on-disk format that other people's scripts parse.
"""

import json
from pathlib import Path

from .models import Finding, ScanReport, Verdict


def _finding_to_dict(finding: Finding) -> dict:
    shape = finding.shape
    return {
        "page_number": finding.page_number,
        "verdict": finding.verdict.value,
        "reason": finding.reason,
        "recovered_text": finding.recovered_text,
        "shape": None
        if shape is None
        else {
            "bbox": list(shape.bbox),
            "fill_color": None if shape.fill_color is None else list(shape.fill_color),
            "alpha": shape.alpha,
            "paint_order": shape.paint_order,
        },
        "covered_spans": [
            {
                "text": span.text,
                "bbox": list(span.bbox),
                "coverage": span.coverage,
                "paint_order": span.paint_order,
                "render_mode": span.render_mode,
            }
            for span in finding.covered
        ],
    }


def to_dict(report: ScanReport) -> dict:
    return {
        "file_path": report.file_path,
        "page_count": report.page_count,
        "generated_at": report.generated_at,
        "has_leak": report.has_leak,
        "summary": {
            verdict.value: len(report.of_verdict(verdict)) for verdict in Verdict
        },
        "findings": [_finding_to_dict(f) for f in report.findings],
    }


def dumps(report: ScanReport) -> str:
    # ensure_ascii=False keeps recovered text readable
    return json.dumps(to_dict(report), indent=2, ensure_ascii=False) + "\n"


def write(report: ScanReport, path: str | Path) -> None:
    Path(path).write_text(dumps(report), encoding="utf-8")
