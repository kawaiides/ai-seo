"""Replay-validator + quality auditor for prompt-iteration runs.

Walks `tools/iteration_runs/<version>/<topic>/run_*.json`, replays each
raw_response through `LLMFanoutResponse.model_validate`, and audits the
quality dimensions v2 was designed to improve over v1.

Usage:
    .venv/bin/python tools/compare_versions.py v1 v2

Output is a side-by-side metric table printed to stdout. Numbers come
from the same on-disk raw responses, so this is a deterministic replay
- it does not call the LLM.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.schemas import LLMFanoutResponse  # noqa: E402

RUNS_ROOT = Path(__file__).parent / "iteration_runs"

YEAR_RE = re.compile(r"\b20(2[5-9]|3\d)\b")
NAMED_SOURCE_RE = re.compile(
    r"\b(G2|Capterra|Reddit|Gartner|Forrester|case stud(y|ies)|peer[- ]reviewed|"
    r"analyst report|Trustpilot|TrustRadius|Hacker News|HN)\b",
    re.IGNORECASE,
)
ACTION_VERB_RE = re.compile(r"^how to [a-z]+", re.IGNORECASE)
DEFINITIONAL_PREFIX_RE = re.compile(
    r"^(what is|what are|define|meaning of|difference between)\b",
    re.IGNORECASE,
)
COMPARATOR_RE = re.compile(r"\b(vs|versus|or)\b", re.IGNORECASE)
CAPITALIZED_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")


def load_runs(version: str) -> list[dict]:
    """Read every run_*.json under iteration_runs/<version>/."""
    root = RUNS_ROOT / version
    runs: list[dict] = []
    if not root.exists():
        return runs
    for path in sorted(root.glob("*/run_*.json")):
        with path.open() as f:
            data = json.load(f)
        data["_path"] = str(path.relative_to(RUNS_ROOT.parent.parent))
        runs.append(data)
    return runs


def words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def jaccard_distance(a: str, b: str) -> float:
    """1 - |A∩B| / |A∪B| on token sets. Library-free Levenshtein stand-in."""
    sa, sb = set(words(a)), set(words(b))
    if not sa and not sb:
        return 0.0
    return 1.0 - len(sa & sb) / len(sa | sb)


def named_competitor_present(query: str) -> bool:
    """Heuristic: comparator word + at least one capitalized proper-noun token."""
    if not COMPARATOR_RE.search(query):
        return False
    return bool(CAPITALIZED_RE.search(query))


def audit_run(run: dict) -> dict | None:
    """Return per-run metrics, or None if strict validation fails."""
    raw = run["raw_response"]
    target_query = run["target_query"]
    try:
        parsed = json.loads(raw)
        validated = LLMFanoutResponse.model_validate(parsed)
    except Exception as e:
        return {"strict_pass": False, "fail_reason": f"{type(e).__name__}: {str(e)[:120]}"}

    sub_queries = validated.sub_queries
    count = len(sub_queries)
    queries_by_type: dict[str, list[str]] = {}
    for sq in sub_queries:
        queries_by_type.setdefault(sq.type, []).append(sq.query)

    lengths = [len(words(sq.query)) for sq in sub_queries]

    # Composition-rule compliance (fraction within applicable type)
    def fraction(items: list[str], pred) -> float | None:
        return (sum(1 for q in items if pred(q)) / len(items)) if items else None

    comp_named = fraction(queries_by_type.get("comparative", []), named_competitor_present)
    trust_anchored = fraction(
        queries_by_type.get("trust_signals", []),
        lambda q: bool(YEAR_RE.search(q) or NAMED_SOURCE_RE.search(q)),
    )
    howto_verb = fraction(
        queries_by_type.get("how_to", []),
        lambda q: bool(ACTION_VERB_RE.search(q.strip())),
    )
    def_prefix = fraction(
        queries_by_type.get("definitional", []),
        lambda q: bool(DEFINITIONAL_PREFIX_RE.search(q.strip())),
    )

    # Intra-type diversity: mean pairwise Jaccard distance per type
    intra_diversity: list[float] = []
    for qs in queries_by_type.values():
        if len(qs) >= 2:
            intra_diversity.extend(jaccard_distance(a, b) for a, b in combinations(qs, 2))
    mean_intra_diversity = statistics.mean(intra_diversity) if intra_diversity else 0.0

    # Echo metrics
    target_lower = target_query.lower().strip()
    target_lead = " ".join(words(target_query)[:2])
    verbatim_echo = any(sq.query.lower().strip() == target_lower for sq in sub_queries)
    lead_overlap = sum(
        1 for sq in sub_queries
        if " ".join(words(sq.query)[:2]) == target_lead and target_lead
    ) / count

    return {
        "strict_pass": True,
        "count": count,
        "lengths": lengths,
        "mean_length": statistics.mean(lengths),
        "stdev_length": statistics.stdev(lengths) if len(lengths) > 1 else 0.0,
        "type_counts": {t: len(v) for t, v in queries_by_type.items()},
        "comp_named_competitor": comp_named,
        "trust_anchored": trust_anchored,
        "howto_action_verb": howto_verb,
        "def_prefix": def_prefix,
        "mean_intra_type_jaccard": mean_intra_diversity,
        "verbatim_echo": verbatim_echo,
        "target_lead_overlap": lead_overlap,
    }


def aggregate(version: str) -> dict:
    runs = load_runs(version)
    if not runs:
        return {"version": version, "n_runs": 0}

    audits = [audit_run(r) for r in runs]
    passes = [a for a in audits if a and a.get("strict_pass")]
    fails = [a for a in audits if a and not a.get("strict_pass")]

    def mean_of(key: str) -> float | None:
        vals = [a[key] for a in passes if a.get(key) is not None]
        return statistics.mean(vals) if vals else None

    def median_of(key: str) -> float | None:
        vals = [a[key] for a in passes if a.get(key) is not None]
        return statistics.median(vals) if vals else None

    return {
        "version": version,
        "n_runs": len(runs),
        "strict_passes": len(passes),
        "strict_pass_rate": len(passes) / len(runs),
        "fail_reasons": [a.get("fail_reason") for a in fails],
        "median_count": median_of("count"),
        "mean_count": mean_of("count"),
        "mean_query_length": mean_of("mean_length"),
        "mean_length_stdev": mean_of("stdev_length"),
        "mean_intra_type_jaccard": mean_of("mean_intra_type_jaccard"),
        "comp_named_competitor_rate": mean_of("comp_named_competitor"),
        "trust_anchored_rate": mean_of("trust_anchored"),
        "howto_action_verb_rate": mean_of("howto_action_verb"),
        "def_prefix_rate": mean_of("def_prefix"),
        "target_lead_overlap_rate": mean_of("target_lead_overlap"),
        "verbatim_echo_rate": sum(1 for a in passes if a["verbatim_echo"]) / len(passes)
            if passes else 0.0,
    }


def fmt(v) -> str:
    if v is None:
        return "  -  "
    if isinstance(v, float):
        return f"{v:6.3f}"
    return str(v)


def print_table(rows: list[dict]) -> None:
    cols = [
        ("version", "version"),
        ("n_runs", "runs"),
        ("strict_pass_rate", "strict_pass"),
        ("median_count", "median_n"),
        ("mean_count", "mean_n"),
        ("mean_query_length", "len_mean"),
        ("mean_length_stdev", "len_stdev"),
        ("mean_intra_type_jaccard", "intra_div"),
        ("comp_named_competitor_rate", "comp_named"),
        ("trust_anchored_rate", "trust_anchor"),
        ("howto_action_verb_rate", "howto_verb"),
        ("def_prefix_rate", "def_prefix"),
        ("target_lead_overlap_rate", "lead_echo"),
        ("verbatim_echo_rate", "verbatim_echo"),
    ]
    header = " | ".join(f"{label:>12}" for _, label in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print(" | ".join(f"{fmt(r.get(key)):>12}" for key, _ in cols))


def main() -> None:
    versions = sys.argv[1:] or ["v1", "v2"]
    rows = [aggregate(v) for v in versions]
    print_table(rows)
    for r in rows:
        if r["n_runs"] == 0:
            print(f"\n[!] {r['version']}: no runs found at {RUNS_ROOT / r['version']}")
        elif r["fail_reasons"]:
            print(f"\n{r['version']} failures:")
            for reason in r["fail_reasons"]:
                print(f"  - {reason}")


if __name__ == "__main__":
    main()
