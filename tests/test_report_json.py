import json

from trueredact.core.models import (
    CoveredSpan,
    Finding,
    ScanReport,
    ShapeObject,
    Verdict,
)
from trueredact.core.report_json import dumps, to_dict, write


def leak_finding():
    return Finding(
        page_number=2,
        verdict=Verdict.FAKE_REDACTION,
        reason="1 text span(s) painted before an opaque shape",
        shape=ShapeObject(
            bbox=(45.0, 45.0, 250.0, 68.0),
            fill_color=(0.0, 0.0, 0.0),
            alpha=1.0,
            paint_order=12,
        ),
        covered=(
            CoveredSpan(
                text="John Smith, SSN 000-00-0000",
                bbox=(50.0, 50.0, 200.0, 62.0),
                coverage=1.0,
                paint_order=7,
                render_mode=0,
            ),
        ),
    )


def report(findings=None):
    return ScanReport(
        file_path="/tmp/doc.pdf",
        page_count=3,
        findings=tuple(findings if findings is not None else [leak_finding()]),
        generated_at="2026-08-08T00:00:00Z",
    )


def test_schema_top_level_keys_are_stable():
    assert set(to_dict(report())) == {
        "file_path",
        "page_count",
        "generated_at",
        "has_leak",
        "summary",
        "findings",
    }


def test_finding_carries_the_evidence_and_no_confidence():
    (finding,) = to_dict(report())["findings"]
    assert set(finding) == {
        "page_number",
        "verdict",
        "reason",
        "recovered_text",
        "shape",
        "covered_spans",
    }
    assert "confidence" not in finding
    assert finding["verdict"] == "fake_redaction"
    assert finding["recovered_text"] == "John Smith, SSN 000-00-0000"
    assert finding["shape"]["bbox"] == [45.0, 45.0, 250.0, 68.0]
    assert finding["shape"]["paint_order"] == 12
    assert finding["covered_spans"][0]["coverage"] == 1.0
    assert finding["covered_spans"][0]["paint_order"] == 7


def test_summary_counts_every_verdict_even_when_zero():
    assert to_dict(report())["summary"] == {
        "clean": 0,
        "fake_redaction": 1,
        "uncertain": 0,
    }


def test_has_leak_reflects_findings():
    assert to_dict(report())["has_leak"] is True
    clean = Finding(page_number=1, verdict=Verdict.CLEAN, reason="nothing here")
    assert to_dict(report([clean]))["has_leak"] is False


def test_a_finding_without_a_shape_serializes_nulls_not_missing_keys():
    uncertain = Finding(page_number=1, verdict=Verdict.UNCERTAIN, reason="no text layer")
    (finding,) = to_dict(report([uncertain]))["findings"]
    assert finding["shape"] is None
    assert finding["recovered_text"] is None
    assert finding["covered_spans"] == []


def test_output_is_valid_json_and_deterministic():
    first = dumps(report())
    assert json.loads(first) == to_dict(report())
    assert dumps(report()) == first


def test_non_ascii_recovered_text_survives_a_round_trip(tmp_path):
    span = CoveredSpan(
        text="Ünïcode — 秘密", bbox=(0.0, 0.0, 1.0, 1.0), coverage=1.0, paint_order=0, render_mode=0
    )
    finding = Finding(
        page_number=1, verdict=Verdict.FAKE_REDACTION, reason="r", covered=(span,)
    )
    path = tmp_path / "out.json"
    write(report([finding]), path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["findings"][0]["covered_spans"][0]["text"] == "Ünïcode — 秘密"
