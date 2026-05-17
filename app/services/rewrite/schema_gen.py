"""Schema.org JSON-LD snippet generator.

Detects the document's likely intent (`FAQPage`, `HowTo`, or `Article`)
and asks the LLM to emit a populated JSON-LD block keyed off the
content's actual sentences. The emitted block is back-validated against
`SchemaMarkupCheck`'s "populated_types" rule — we never return a block
that wouldn't lift Check E's score.

The intent detector is a deterministic pre-pass: it inspects the H-tag
list and the body for FAQ-style (questions in headings) or HowTo-style
(imperative how-to headings, numbered steps) markers. Falls back to
`Article` for everything else.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Literal

from pydantic import ValidationError

from app.models.schemas import LLMSchemaGenResponse, SchemaGenResult
from app.services.aeo_checks.schema_markup import (
    REQUIRED_PROPS,
    SchemaMarkupCheck,
    _is_populated,
    _types_of,
)
from app.services.content_parser import ParsedContent
from app.services.fanout_engine import extract_json
from app.services.llm_client import LLMClient, LLMUnavailableError, get_llm_client

MAX_RETRIES = 2
BASE_BACKOFF_SECONDS = 0.5
MAX_BODY_CHARS = 6000  # cap context size to keep latency / cost predictable

DetectedIntent = Literal["FAQPage", "HowTo", "Article"]

QUESTION_HEADING_RE = re.compile(r"\?\s*$")
HOWTO_HEADING_RE = re.compile(r"^(how (?:to|do|does|can)\b|step\s*\d+\b)", re.IGNORECASE)


SYSTEM_PROMPT_TEMPLATE = """You generate Schema.org JSON-LD blocks for AEO.

You output ONLY a single JSON object. The first character must be `{{` and the last must be `}}`. No markdown fences, no prose.

The JSON object has exactly one key:
  - "json_ld":   a Schema.org JSON-LD object. Must include `"@context": "https://schema.org"` and `"@type": "{intent}"`.

Required population per intent:
  - FAQPage: include a `mainEntity` array of at least 3 `Question` objects.
    Each Question has `name` (the question), and `acceptedAnswer` with
    `@type: "Answer"` and a `text` populated from the source content.
  - HowTo: include `name` (a short how-to title), and a `step` array of
    at least 3 `HowToStep` objects, each with `name` and `text` drawn
    from the source content.
  - Article: include `headline`, `author` (Person with `name`), and
    `articleBody` summarising the source. `dateModified` is optional —
    omit if not stated in the source content.

Hard rules:
  - Every value populated MUST come from the SOURCE_CONTENT below. Do
    not invent facts, dates, authors, or quotations.
  - If the source genuinely lacks enough material to populate the
    minimum required properties, return a valid JSON-LD object with the
    `@type` set anyway — but populate as many properties as the source
    supports. Do NOT fabricate.
  - Do NOT wrap output in ```json fences or include any additional keys
    at the top level. The JSON must validate against
    `{{"json_ld": <object>}}`.
"""


USER_PROMPT_TEMPLATE = """INTENT: {intent}

SOURCE_CONTENT:
\"\"\"{source}\"\"\"

Emit the JSON object as described."""


def detect_intent(content: ParsedContent) -> DetectedIntent:
    """Pick the JSON-LD type that fits the content's structure best.

    `FAQPage` wins if ≥2 headings end in `?`. `HowTo` wins if ≥2 headings
    look like how-to/step phrasing. Otherwise `Article`.
    """
    h_texts = [text for _, text in content.h_tags]
    body = content.body_text or ""

    faq_hits = sum(1 for t in h_texts if QUESTION_HEADING_RE.search(t))
    howto_hits = sum(1 for t in h_texts if HOWTO_HEADING_RE.search(t))

    if faq_hits >= 2:
        return "FAQPage"
    if howto_hits >= 2:
        return "HowTo"
    # Soft signals: any "how to" in body H1, or "step 1 … step 2 …" sequence.
    if h_texts and HOWTO_HEADING_RE.search(h_texts[0]):
        return "HowTo"
    if re.search(r"\bstep\s*1\b.+\bstep\s*2\b", body, re.IGNORECASE | re.DOTALL):
        return "HowTo"
    return "Article"


async def generate_schema(
    content: ParsedContent,
    *,
    intent: DetectedIntent | None = None,
    client: LLMClient | None = None,
) -> SchemaGenResult:
    """Generate a populated JSON-LD block and back-check it via Check E."""
    if not content.body_text or not content.body_text.strip():
        raise ValueError("content.body_text is empty")

    client = client or get_llm_client()
    chosen_intent = intent or detect_intent(content)
    source_excerpt = content.body_text[:MAX_BODY_CHARS]

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(intent=chosen_intent)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        intent=chosen_intent, source=source_excerpt.replace('"""', '"​""')
    )

    last_detail = "no attempts made"
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = await client.generate_json(system_prompt, user_prompt)
            parsed = extract_json(raw)
            validated = LLMSchemaGenResponse.model_validate(parsed)
        except LLMUnavailableError as e:
            last_detail = f"{e.detail} (attempt {attempt})"
        except json.JSONDecodeError as e:
            last_detail = f"JSONDecodeError on attempt {attempt}: {e.msg}"
        except ValidationError as e:
            errs = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg'][:80]}"
                for err in e.errors()[:3]
            )
            last_detail = f"Schema validation failed on attempt {attempt}: {errs}"
        else:
            populated, types_emitted = _back_check(validated.json_ld, chosen_intent)
            if populated:
                json_text = json.dumps(validated.json_ld, indent=2, ensure_ascii=False)
                html_snippet = (
                    f'<script type="application/ld+json">\n{json_text}\n</script>'
                )
                return SchemaGenResult(
                    intent=chosen_intent,
                    types_emitted=types_emitted,
                    json_ld=validated.json_ld,
                    html_snippet=html_snippet,
                    model_used=client.model_id,
                    populated=True,
                )
            last_detail = (
                f"Back-check failed on attempt {attempt}: emitted {types_emitted} "
                f"but required props for {chosen_intent} not populated"
            )

        if attempt < MAX_RETRIES + 1:
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            jitter = backoff * random.uniform(-0.2, 0.2)
            await asyncio.sleep(max(0.0, backoff + jitter))

    raise LLMUnavailableError(last_detail)


def _back_check(json_ld: dict, intent: DetectedIntent) -> tuple[bool, list[str]]:
    """Confirm the emitted JSON-LD would lift Check E to its `populated` band.

    Reuses `_is_populated` and `_types_of` from the check itself so the
    two stay in lockstep. If Check E's contract changes, the rewrite
    contract changes with it.
    """
    types = _types_of(json_ld)
    populated_ok = _is_populated(intent, [json_ld])
    # Extra defence: minimum cardinality on the key collection per intent.
    required_keys = REQUIRED_PROPS.get(intent, ())
    if populated_ok:
        for key in required_keys:
            val = json_ld.get(key)
            if isinstance(val, list) and len(val) < 3 and intent in {"FAQPage", "HowTo"}:
                # FAQPage/HowTo with <3 anchor entries is barely populated;
                # we want a meaningful block, not a one-item stub.
                populated_ok = False
                break
    return populated_ok, types
