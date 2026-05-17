"""Heading hierarchy auto-fix.

Pure-deterministic restructure that resolves the four violations Check B
flags:

  - `missing_h1`     → promote the first heading to H1.
  - `multiple_h1`    → keep the first H1; demote the rest to H2.
  - `pre_h1_tag`     → demote every heading before the first H1 to one
                       level below H1 (i.e. H2), preserving order.
  - `level_skipped`  → collapse any H{n} → H{n+2+} jump down to H{n+1}.

The function operates on the `(level, text)` list emitted by the parser
so it stays decoupled from BeautifulSoup re-serialisation concerns. A
caller that wants emitted HTML can run `to_html(fixed)` on the result.
"""

from __future__ import annotations

from typing import Any

HTag = tuple[int, str]


def autofix_headings(h_tags: list[HTag]) -> dict[str, Any]:
    """Apply all four fixes in dependency order and report what changed.

    Returns a dict with `original`, `fixed`, and `operations` so the
    response surface can show users a transparent diff of the changes.
    """
    operations: list[dict[str, Any]] = []
    working = list(h_tags)

    working, ops = _promote_first_to_h1(working)
    operations.extend(ops)

    working, ops = _demote_pre_h1(working)
    operations.extend(ops)

    working, ops = _demote_extra_h1s(working)
    operations.extend(ops)

    working, ops = _collapse_skipped_levels(working)
    operations.extend(ops)

    return {
        "original": list(h_tags),
        "fixed": working,
        "operations": operations,
    }


def to_html(h_tags: list[HTag]) -> str:
    """Render `(level, text)` pairs back into a heading-only HTML snippet.

    Useful for callers that want a copy-paste-ready block; not a full
    document rebuild (body content is the user's responsibility).
    """
    return "\n".join(
        f"<h{level}>{_escape(text)}</h{level}>" for level, text in h_tags
    )


def _promote_first_to_h1(h_tags: list[HTag]) -> tuple[list[HTag], list[dict[str, Any]]]:
    if not h_tags:
        return h_tags, []
    if any(level == 1 for level, _ in h_tags):
        return h_tags, []
    first_level, first_text = h_tags[0]
    new_list = [(1, first_text)] + list(h_tags[1:])
    return new_list, [
        {
            "rule": "promote_to_h1",
            "description": (
                f"No H1 in source; promoted first heading "
                f"(H{first_level} '{first_text}') to H1."
            ),
            "from_level": first_level,
            "to_level": 1,
            "index": 0,
        }
    ]


def _demote_pre_h1(h_tags: list[HTag]) -> tuple[list[HTag], list[dict[str, Any]]]:
    if not h_tags:
        return h_tags, []
    first_h1 = next((i for i, (lvl, _) in enumerate(h_tags) if lvl == 1), None)
    if first_h1 is None or first_h1 == 0:
        return h_tags, []
    ops: list[dict[str, Any]] = []
    new_list: list[HTag] = []
    for i, (level, text) in enumerate(h_tags):
        if i < first_h1 and level == 1:
            # Already covered by `_demote_extra_h1s`; leave untouched.
            new_list.append((level, text))
            continue
        if i < first_h1:
            new_list.append((2, text))
            if level != 2:
                ops.append(
                    {
                        "rule": "demote_pre_h1",
                        "description": (
                            f"Heading H{level} '{text}' appeared before the first "
                            "H1; demoted to H2."
                        ),
                        "from_level": level,
                        "to_level": 2,
                        "index": i,
                    }
                )
        else:
            new_list.append((level, text))
    return new_list, ops


def _demote_extra_h1s(h_tags: list[HTag]) -> tuple[list[HTag], list[dict[str, Any]]]:
    ops: list[dict[str, Any]] = []
    seen_first = False
    new_list: list[HTag] = []
    for i, (level, text) in enumerate(h_tags):
        if level == 1:
            if not seen_first:
                seen_first = True
                new_list.append((level, text))
            else:
                new_list.append((2, text))
                ops.append(
                    {
                        "rule": "demote_extra_h1",
                        "description": (
                            f"Extra H1 '{text}' demoted to H2 — documents should "
                            "have exactly one H1."
                        ),
                        "from_level": 1,
                        "to_level": 2,
                        "index": i,
                    }
                )
        else:
            new_list.append((level, text))
    return new_list, ops


def _collapse_skipped_levels(
    h_tags: list[HTag],
) -> tuple[list[HTag], list[dict[str, Any]]]:
    ops: list[dict[str, Any]] = []
    new_list: list[HTag] = []
    prev_level: int | None = None
    for i, (level, text) in enumerate(h_tags):
        target_level = level
        if prev_level is not None and target_level > prev_level + 1:
            target_level = prev_level + 1
            ops.append(
                {
                    "rule": "collapse_skip",
                    "description": (
                        f"Heading skipped from H{prev_level} to H{level} "
                        f"('{text}'); collapsed to H{target_level}."
                    ),
                    "from_level": level,
                    "to_level": target_level,
                    "index": i,
                }
            )
        new_list.append((target_level, text))
        prev_level = target_level
    return new_list, ops


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
