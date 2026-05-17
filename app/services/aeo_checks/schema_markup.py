"""Check E — Schema.org markup.

Scans the original HTML for Schema.org structured data (JSON-LD and
microdata `itemtype`) and scores the presence of types that LLM answer
engines actively look for (FAQPage, HowTo, Article variants, Product,
Recipe, QAPage). Bonus credit when the high-value type also has its
expected anchor properties populated, since an empty `FAQPage` shell
contributes no extractable answers.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent

# Types LLMs disproportionately cite. Order matters only for reporting.
HIGH_VALUE_TYPES: frozenset[str] = frozenset(
    {
        "FAQPage",
        "QAPage",
        "HowTo",
        "Article",
        "NewsArticle",
        "BlogPosting",
        "TechArticle",
        "Product",
        "Recipe",
    }
)

# Required-anchor properties per high-value type. Presence of these in
# the parsed JSON-LD object is the "populated, not just declared" signal.
REQUIRED_PROPS: dict[str, tuple[str, ...]] = {
    "FAQPage": ("mainEntity",),
    "QAPage": ("mainEntity",),
    "HowTo": ("step",),
    "Article": ("headline",),
    "NewsArticle": ("headline",),
    "BlogPosting": ("headline",),
    "TechArticle": ("headline",),
    "Product": ("name",),
    "Recipe": ("recipeIngredient", "recipeInstructions"),
}


class SchemaMarkupCheck(BaseCheck):
    check_id: ClassVar[str] = "schema_markup"
    name: ClassVar[str] = "Schema.org Markup"
    max_score: ClassVar[int] = 20

    def run(self, content: ParsedContent) -> CheckResultModel:
        soup = content.soup
        if soup is None:
            return self._result(
                score=0,
                details={
                    "json_ld_blocks": 0,
                    "microdata_items": 0,
                    "types_found": [],
                    "high_value_types": [],
                    "populated_types": [],
                    "note": "Plain-text input — no HTML to scan for schema markup.",
                },
                recommendation=(
                    "Schema markup can only be detected in HTML. Submit the page "
                    "as a URL or paste the raw HTML to surface this signal."
                ),
            )

        json_ld_objects = _extract_json_ld(soup)
        microdata_types = _extract_microdata_types(soup)

        json_ld_types = sorted({
            t for obj in json_ld_objects for t in _types_of(obj)
        })
        all_types = sorted(set(json_ld_types) | set(microdata_types))
        high_value = sorted(t for t in all_types if t in HIGH_VALUE_TYPES)
        populated = sorted(
            t for t in high_value if _is_populated(t, json_ld_objects)
        )

        score = _score(all_types, high_value, populated)
        details = {
            "json_ld_blocks": len(json_ld_objects),
            "microdata_items": len(microdata_types),
            "types_found": all_types,
            "high_value_types": high_value,
            "populated_types": populated,
        }
        return self._result(score, details, _recommendation(score, all_types, high_value, populated))


def _extract_json_ld(soup: Any) -> list[dict[str, Any]]:
    """Return every JSON-LD payload object in the document.

    Handles three legal shapes: a bare object, a list of objects, and
    `@graph` containers. Malformed JSON blocks are skipped silently —
    the spec allows multiple blocks, so one broken block must not poison
    the rest.
    """
    out: list[dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        out.extend(_flatten_payload(payload))
    return out


def _flatten_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        flat: list[dict[str, Any]] = []
        for item in payload:
            flat.extend(_flatten_payload(item))
        return flat
    if isinstance(payload, dict):
        if "@graph" in payload and isinstance(payload["@graph"], list):
            return _flatten_payload(payload["@graph"])
        return [payload]
    return []


def _types_of(obj: dict[str, Any]) -> list[str]:
    raw = obj.get("@type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


def _extract_microdata_types(soup: Any) -> list[str]:
    out: list[str] = []
    for el in soup.find_all(attrs={"itemtype": True}):
        itemtype = el.get("itemtype", "")
        if not isinstance(itemtype, str):
            continue
        # itemtype is typically a full URL: https://schema.org/FAQPage
        type_name = itemtype.rstrip("/").rsplit("/", 1)[-1]
        if type_name:
            out.append(type_name)
    return out


def _is_populated(type_name: str, objs: list[dict[str, Any]]) -> bool:
    required = REQUIRED_PROPS.get(type_name)
    if not required:
        return False
    for obj in objs:
        if type_name not in _types_of(obj):
            continue
        if any(_value_present(obj.get(prop)) for prop in required):
            return True
    return False


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _score(
    all_types: list[str],
    high_value: list[str],
    populated: list[str],
) -> int:
    if not all_types:
        return 0
    if populated:
        return 20
    if high_value:
        return 14
    return 8


def _recommendation(
    score: int,
    all_types: list[str],
    high_value: list[str],
    populated: list[str],
) -> str | None:
    if score == 20:
        return None
    if not all_types:
        return (
            "No Schema.org markup detected. Add a JSON-LD block to the page head "
            "(Article, FAQPage, HowTo, or Product as appropriate) — LLM answer "
            "engines lean heavily on structured types."
        )
    if not high_value:
        present = ", ".join(all_types)
        return (
            f"Generic schema types present ({present}). Add a high-value type "
            "(FAQPage, HowTo, Article, or Product) so answer engines have an "
            "explicit hook for extraction."
        )
    if not populated:
        empty = ", ".join(high_value)
        return (
            f"High-value type(s) declared but empty: {empty}. Populate the "
            "required anchor properties (e.g. `mainEntity` on FAQPage, `step` "
            "on HowTo, `headline` on Article) so the markup is extractable."
        )
    return None
