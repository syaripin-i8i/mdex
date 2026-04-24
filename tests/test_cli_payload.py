from __future__ import annotations

from mdex import cli


def test_emit_payload_compact_and_pretty(capsys) -> None:
    payload = {"key": "value", "count": 1}

    cli._emit_payload(payload, pretty=False)
    compact = capsys.readouterr().out.strip()
    assert compact == '{"key":"value","count":1}'

    cli._emit_payload(payload, pretty=True)
    pretty = capsys.readouterr().out
    assert '"key": "value"' in pretty
    assert "\n" in pretty


def test_emit_payload_supports_stderr(capsys) -> None:
    cli._emit_payload({"error": "failure"}, stderr=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == '{"error":"failure"}'
