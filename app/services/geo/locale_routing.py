"""Per-locale egress hints for GEO probing.

Returning the canonical country code for each supported locale lets a
deployment route the actual outbound LLM call through a regional egress
(Cloudflare Workers `services.workers.dev` region pin, or a per-country
Lambda) so the answer engine sees an IP geo-located to the queried
locale. This matters because Perplexity / ChatGPT / Gemini routinely
inject locale-aware retrieval — a US-egress probe asking a Hindi-locale
query still gets US sources back.

This module is the *policy* layer: the mapping `locale → country → hint`.
The actual egress wiring lives outside the Python process (proxy /
worker). The hint shows up in the probe response so the operator can
verify which region was supposed to be used.
"""

from __future__ import annotations

from dataclasses import dataclass


# ISO 639-1 (and a couple of region-tagged forms) → ISO 3166-1 alpha-2.
LOCALE_TO_COUNTRY: dict[str, str] = {
    "en": "US",
    "en-us": "US",
    "en-gb": "GB",
    "en-in": "IN",
    "en-au": "AU",
    "en-ca": "CA",
    "es": "ES",
    "es-mx": "MX",
    "fr": "FR",
    "fr-ca": "CA",
    "de": "DE",
    "it": "IT",
    "pt": "PT",
    "pt-br": "BR",
    "nl": "NL",
    "sv": "SE",
    "da": "DK",
    "no": "NO",
    "hi": "IN",
    "ta": "IN",
    "te": "IN",
    "kn": "IN",
    "bn": "IN",
    "ml": "IN",
    "ja": "JP",
    "ko": "KR",
    "zh": "CN",
    "zh-tw": "TW",
}


@dataclass(frozen=True)
class EgressHint:
    """Routing hint surfaced to operators + the LLM proxy.

    `region_header`: the value the operator's outbound proxy should pin
    egress to. Conventionally a Cloudflare colo region or AWS region.
    `country`: ISO 3166-1 alpha-2 country code.
    """

    locale: str
    country: str
    region_header: str


# Country → preferred region pin. Falls back to "global".
_COUNTRY_TO_REGION: dict[str, str] = {
    "US": "wnam",
    "CA": "wnam",
    "MX": "wnam",
    "GB": "weur",
    "FR": "weur",
    "DE": "weur",
    "IT": "weur",
    "ES": "weur",
    "PT": "weur",
    "NL": "weur",
    "SE": "weur",
    "DK": "weur",
    "NO": "weur",
    "IN": "apac",
    "JP": "apac",
    "KR": "apac",
    "AU": "apac",
    "CN": "apac",
    "TW": "apac",
    "BR": "enam",
}


def country_for_locale(locale: str | None) -> str | None:
    """Best-effort locale → country code.

    Accepts bare ISO 639-1 (`"en"`), region-tagged (`"en-IN"`), and any
    casing. Returns `None` for unsupported locales rather than guessing
    so callers can decide whether to default to `US` or skip routing.
    """
    if not locale:
        return None
    code = locale.strip().lower().replace("_", "-")
    if code in LOCALE_TO_COUNTRY:
        return LOCALE_TO_COUNTRY[code]
    base = code.split("-", 1)[0]
    return LOCALE_TO_COUNTRY.get(base)


def egress_hint(locale: str | None) -> EgressHint | None:
    country = country_for_locale(locale)
    if country is None:
        return None
    region = _COUNTRY_TO_REGION.get(country, "global")
    return EgressHint(
        locale=locale.strip().lower() if locale else "",
        country=country,
        region_header=region,
    )
