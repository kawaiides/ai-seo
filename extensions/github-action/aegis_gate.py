"""Self-contained AEGIS AEO gate.

Mirrors `cli/aegis_ci.py` but as a single file so the Docker action can
build from its own directory without copying the parent repo.

Reads inputs from CLI flags + a handful of `AEGIS_*` env vars surfaced
by the `action.yml` wrapper. Exits non-zero when any target scores
below the threshold so the workflow step fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

try:
    import httpx
except ImportError:  # pragma: no cover - explicit guidance on a clean CI image
    sys.stderr.write("aegis_gate requires `httpx`. Install with `pip install httpx`.\n")
    raise SystemExit(2)

DEFAULT_THRESHOLD = 65
DEFAULT_TIMEOUT_SECONDS = 30.0
EXIT_BELOW_THRESHOLD = 1
EXIT_AUTH = 3
EXIT_NETWORK = 4
EXIT_USAGE = 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aegis-gate")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    p.add_argument("--api-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return p.parse_args(argv)


def _split_targets(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.replace("\n", " ").split():
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def _audit_one(
    client: httpx.Client,
    api_url: str,
    api_key: str,
    target: str,
    paste: bool,
) -> dict:
    if paste:
        body = Path(target).read_text(encoding="utf-8", errors="replace")
        payload = {"input_type": "text", "input_value": body}
        label = target
    else:
        payload = {"input_type": "url", "input_value": target}
        label = target
    r = client.post(
        f"{api_url.rstrip('/')}/api/v1/audit",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AEGIS-GitHubAction/0.1.0",
        },
        json=payload,
    )
    if r.status_code == 401 or r.status_code == 403:
        raise SystemExit(EXIT_AUTH)
    r.raise_for_status()
    data = r.json()
    data["_label"] = label
    return data


def _set_github_output(name: str, value: str) -> None:
    """Write to $GITHUB_OUTPUT so workflow `outputs.<name>` resolves."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    sanitised = str(value).replace("\n", "\\n")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={sanitised}\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    targets = _split_targets(os.environ.get("AEGIS_TARGETS"))
    if not targets:
        sys.stderr.write("aegis-gate: no targets provided\n")
        return EXIT_USAGE

    paste = (os.environ.get("AEGIS_PASTE", "false").lower() == "true")
    fail_on_error = (os.environ.get("AEGIS_FAIL_ON_ERROR", "true").lower() == "true")

    results: list[dict] = []
    min_score = 100
    failed_count = 0

    with httpx.Client(timeout=args.timeout) as client:
        for tgt in targets:
            try:
                data = _audit_one(client, args.api_url, args.api_key, tgt, paste)
            except SystemExit:
                raise
            except (httpx.HTTPError, OSError) as e:
                sys.stderr.write(f"aegis-gate: {tgt}: {type(e).__name__}: {e}\n")
                if fail_on_error:
                    return EXIT_NETWORK
                continue

            score = int(data.get("aeo_score") or 0)
            band = data.get("band") or ""
            below = score < args.threshold
            if below:
                failed_count += 1
            if score < min_score:
                min_score = score
            results.append({
                "target": tgt,
                "score": score,
                "band": band,
                "below_threshold": below,
            })
            marker = "✗" if below else "✓"
            print(f"{marker} {tgt}: score={score} band={band!r}")

    _set_github_output("min-score", min_score if results else 0)
    _set_github_output("failed-count", failed_count)
    _set_github_output(
        "report",
        json.dumps(
            {"threshold": args.threshold, "results": results}, separators=(",", ":")
        ),
    )

    if failed_count > 0:
        sys.stderr.write(
            f"aegis-gate: {failed_count}/{len(results)} targets below "
            f"threshold ({args.threshold})\n"
        )
        return EXIT_BELOW_THRESHOLD
    return 0


if __name__ == "__main__":
    sys.exit(main())
