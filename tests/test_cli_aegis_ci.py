"""Unit tests for the aegis-ci CLI (Phase E)."""

from __future__ import annotations

import json

import httpx
import pytest

from cli.aegis_ci import (
    DEFAULT_THRESHOLD,
    EXIT_AUTH,
    EXIT_BELOW_THRESHOLD,
    EXIT_NETWORK,
    EXIT_USAGE,
    build_payload,
    parse_args,
    run,
)


def test_parse_args_defaults(monkeypatch):
    monkeypatch.delenv("AEGIS_API_URL", raising=False)
    monkeypatch.delenv("AEGIS_API_KEY", raising=False)
    ns = parse_args(["https://a/"])
    assert ns.threshold == DEFAULT_THRESHOLD
    assert ns.targets == ["https://a/"]
    assert ns.paste is False


def test_build_payload_url():
    assert build_payload("https://a/", paste=False) == {
        "input_type": "url",
        "input_value": "https://a/",
    }


def test_build_payload_paste(tmp_path):
    file = tmp_path / "draft.html"
    file.write_text("<h1>hi</h1>", encoding="utf-8")
    payload = build_payload(str(file), paste=True)
    assert payload["input_type"] == "text"
    assert "<h1>hi</h1>" in payload["input_value"]


def test_run_missing_url_returns_usage_exit(capsys, monkeypatch):
    monkeypatch.delenv("AEGIS_API_URL", raising=False)
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://a/"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "AEGIS_API_URL" in err


def test_run_missing_key_returns_auth_exit(capsys, monkeypatch):
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.delenv("AEGIS_API_KEY", raising=False)
    code = run(["https://a/"])
    assert code == EXIT_AUTH


def _patch_httpx_client(monkeypatch, handler):
    """Replace `httpx.Client` with a MockTransport-backed one for the test."""

    class _Patched(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("cli.aegis_ci.httpx.Client", _Patched)


def test_run_passes_when_score_above_threshold(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"aeo_score": 90, "band": "AEO Optimized", "checks": []}
        )

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://example.com/", "--threshold", "70", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert "score=90" in out
    summary = json.loads(out.split("{", 1)[1].rsplit("}", 1)[0].join(("{", "}")))
    assert summary["passed"] is True


def test_run_fails_when_score_below_threshold(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"aeo_score": 40, "band": "Significant Gaps", "checks": []}
        )

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://example.com/", "--threshold", "70"])
    assert code == EXIT_BELOW_THRESHOLD


def test_run_handles_401(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://example.com/"])
    err = capsys.readouterr().err
    assert code == EXIT_AUTH
    assert "rejected" in err


def test_run_handles_403_scope(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "scope_missing"})

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://example.com/"])
    err = capsys.readouterr().err
    assert code == EXIT_AUTH
    assert "audit:write" in err


def test_run_handles_network_error(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    _patch_httpx_client(monkeypatch, handler)
    monkeypatch.setenv("AEGIS_API_URL", "https://api.example.test")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_abcdef_xxx")
    code = run(["https://example.com/"])
    assert code == EXIT_NETWORK
