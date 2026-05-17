"""Unit tests for autopilot/contact_finder."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from httpx import Response

from app.autopilot.contact_finder import (
    HomepageMailtoFinder,
    ManualContactFinder,
    StubContactFinder,
    _extract_mailto,
    _is_useful_email,
    _root_of,
)
from app.db.models import Prospect


def _prospect(url: str = "https://example.com/blog/post-1") -> Prospect:
    p = Prospect(url=url, domain="example.com", target_keyword="x")
    p.id = 1
    return p


# ---- pure helpers ----


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/post", "https://example.com/"),
        ("http://example.com:8080/path?q=1", "http://example.com:8080/"),
        ("https://sub.example.com/a/b", "https://sub.example.com/"),
    ],
)
def test_root_of(url: str, expected: str) -> None:
    assert _root_of(url) == expected


@pytest.mark.parametrize(
    "addr,useful",
    [
        ("alice@example.com", True),
        ("Editor@example.com", True),
        ("noreply@example.com", False),
        ("no-reply@example.com", False),
        ("postmaster@example.com", False),
        ("abuse@example.com", False),
    ],
)
def test_is_useful_email(addr: str, useful: bool) -> None:
    assert _is_useful_email(addr.lower()) is useful


def test_extract_mailto_picks_anchors_and_inline() -> None:
    html = """
    <html><body>
      <a href="mailto:editor@example.com?subject=hi">Email us</a>
      <p>For press: press@example.com</p>
      <a href="mailto:noreply@example.com">opt-out</a>
    </body></html>
    """
    found = _extract_mailto(html)
    assert "editor@example.com" in found
    assert "press@example.com" in found
    assert "noreply@example.com" in found  # extraction picks all; banlist filters later


# ---- StubContactFinder ----


@pytest.mark.asyncio
async def test_stub_returns_empty() -> None:
    out = await StubContactFinder().find(_prospect())
    assert out == []


# ---- HomepageMailtoFinder ----


@pytest.mark.asyncio
@respx.mock
async def test_homepage_mailto_finder_happy_path() -> None:
    respx.get("https://example.com/").mock(
        return_value=Response(
            200,
            html='<a href="mailto:editor@example.com">Editor</a>'
                 '<p>noreply@example.com</p>',
        )
    )
    out = await HomepageMailtoFinder().find(_prospect())
    assert len(out) == 1
    assert out[0].email == "editor@example.com"
    assert out[0].source == "homepage_mailto"
    assert out[0].verified is False


@pytest.mark.asyncio
@respx.mock
async def test_homepage_mailto_finder_only_banlisted_returns_empty() -> None:
    respx.get("https://example.com/").mock(
        return_value=Response(200, html='<a href="mailto:noreply@example.com">x</a>')
    )
    out = await HomepageMailtoFinder().find(_prospect())
    assert out == []


@pytest.mark.asyncio
@respx.mock
async def test_homepage_mailto_finder_handles_4xx() -> None:
    respx.get("https://example.com/").mock(return_value=Response(404))
    out = await HomepageMailtoFinder().find(_prospect())
    assert out == []


@pytest.mark.asyncio
@respx.mock
async def test_homepage_mailto_finder_handles_network_error() -> None:
    respx.get("https://example.com/").mock(side_effect=__import__("httpx").ConnectError("boom"))
    out = await HomepageMailtoFinder().find(_prospect())
    assert out == []


# ---- ManualContactFinder ----


@pytest.mark.asyncio
async def test_manual_finder_reads_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text(
        "prospect_id,email,name,role\n"
        "1,alice@example.com,Alice,Editor\n"
        "2,bob@other.com,Bob,Author\n"
        "1,bad,,,\n",  # invalid email — skipped
        encoding="utf-8",
    )
    out = await ManualContactFinder(csv_path).find(_prospect())
    assert len(out) == 1
    assert out[0].email == "alice@example.com"
    assert out[0].name == "Alice"
    assert out[0].role == "Editor"
    assert out[0].source == "manual_csv"


@pytest.mark.asyncio
async def test_manual_finder_missing_file_returns_empty(tmp_path: Path) -> None:
    out = await ManualContactFinder(tmp_path / "nope.csv").find(_prospect())
    assert out == []
