"""HTML/text content parsing for the AEO scorer.

Single entry point: parse raw HTML or plain text into a `ParsedContent`
with the four fields the three AEO checks need (first_paragraph, h_tags,
body_text, raw_soup). Also exposes `fetch_url` for the API endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

BOILERPLATE_TAGS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "noscript",
    "form",
    "iframe",
    "svg",
    "button",
)

DEFAULT_USER_AGENT = "AEGIS/1.0 (+https://example.invalid)"


class URLFetchError(Exception):
    """Raised when fetching a URL fails (timeout, HTTP error, network error)."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ContentParseError(Exception):
    """Raised when content cannot be parsed into anything usable."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class ParsedContent:
    raw: str
    soup: BeautifulSoup | None
    first_paragraph: str
    h_tags: list[tuple[int, str]]
    body_text: str


async def fetch_url(url: str, timeout: float = 10.0) -> str:
    """Fetch a URL and return its body text.

    Wraps timeouts and HTTP errors in `URLFetchError` with a human-readable
    detail string suitable for the API's 422 response envelope.
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.TimeoutException as e:
        raise URLFetchError(f"Connection timeout after {timeout}s") from e
    except httpx.HTTPStatusError as e:
        raise URLFetchError(
            f"HTTP {e.response.status_code} from upstream"
        ) from e
    except httpx.HTTPError as e:
        raise URLFetchError(f"Network error: {type(e).__name__}: {e}") from e


def parse(raw: str, input_type: str) -> ParsedContent:
    """Parse raw input into a `ParsedContent`.

    For `input_type="url"`, `raw` is the response body string.
    For `input_type="text"`, `raw` is the user-pasted content (HTML or plain).

    Plain-text inputs (no <p> and no h-tags) are handled honestly:
    `h_tags` is empty and `first_paragraph` comes from a `\\n\\n` split.
    """
    raw = raw or ""
    if not raw.strip():
        raise ContentParseError("input is empty")

    soup = BeautifulSoup(raw, "html.parser")
    h_tag_elements = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    p_elements = soup.find_all("p")

    if not h_tag_elements and not p_elements:
        # Treat as plain text: BeautifulSoup will have wrapped naked text
        # but found no real structure. Fall back to plain-text handling.
        return ParsedContent(
            raw=raw,
            soup=None,
            first_paragraph=_extract_first_paragraph_plain(raw),
            h_tags=[],
            body_text=_normalize_whitespace(raw),
        )

    return ParsedContent(
        raw=raw,
        soup=soup,
        first_paragraph=_extract_first_paragraph_html(soup, raw),
        h_tags=_collect_h_tags(soup),
        body_text=_extract_body_text(soup),
    )


def _extract_first_paragraph_html(soup: BeautifulSoup, raw: str) -> str:
    p = soup.find("p")
    if p is not None:
        return _normalize_whitespace(p.get_text(separator=" "))
    # No <p> tag despite some structure (e.g., only headings). Fall back to
    # the plain-text first-block heuristic over the soup's stripped text,
    # not the raw HTML, so we never leak tags into first_paragraph.
    text = soup.get_text(separator="\n")
    return _extract_first_paragraph_plain(text)


def _extract_first_paragraph_plain(text: str) -> str:
    blocks = re.split(r"\n\s*\n+", text.strip())
    for block in blocks:
        cleaned = _normalize_whitespace(block)
        if cleaned:
            return cleaned
    return ""


def _collect_h_tags(soup: BeautifulSoup) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(tag.name[1])
        out.append((level, _normalize_whitespace(tag.get_text(separator=" "))))
    return out


def _extract_body_text(soup: BeautifulSoup) -> str:
    """Pick the most representative content container and return its text.

    Preference order: <article>, then <main>, then <body> with boilerplate
    tags stripped, then the whole document.
    """
    container = soup.find("article") or soup.find("main") or soup.find("body") or soup

    # Work on a clone so we don't mutate the original soup (the parser may be
    # reused or inspected by callers later).
    container_str = str(container)
    container_soup = BeautifulSoup(container_str, "html.parser")
    for tag_name in BOILERPLATE_TAGS:
        for el in container_soup.find_all(tag_name):
            el.decompose()

    return _normalize_whitespace(container_soup.get_text(separator=" "))


def _normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
