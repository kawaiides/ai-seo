"""aegis-ci — audit gate for CI pipelines.

Designed for a GitHub Action `run:` step. Reads URLs (or local files via
`--paste`) from the command line, calls `POST /api/v1/audit` with a
Bearer API key, and exits non-zero when any audit scores below the
configured threshold.

Usage (GitHub Actions):

  - name: AEGIS audit gate
    env:
      AEGIS_API_URL: https://aegis-autopilot.com
      AEGIS_API_KEY: ${{ secrets.AEGIS_API_KEY }}
    run: |
      python -m cli.aegis_ci --threshold 70 \
        https://staging.example.com/blog/$(echo "${{ github.head_ref }}" | tr / -)

Local smoke (against the dev server):

  AEGIS_API_URL=http://127.0.0.1:8000 \
  AEGIS_API_KEY=aegis_ak_… \
    python -m cli.aegis_ci --paste path/to/draft.html

The script is intentionally pip-install-free besides `httpx` — the
audit surface is the only AEGIS contract it touches.
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
    sys.stderr.write(
        "aegis-ci requires `httpx`. Install with `pip install httpx`.\n"
    )
    raise SystemExit(2)

DEFAULT_THRESHOLD = 65
DEFAULT_TIMEOUT_SECONDS = 30.0
EXIT_BELOW_THRESHOLD = 1
EXIT_AUTH = 3
EXIT_NETWORK = 4
EXIT_USAGE = 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aegis-ci",
        description="Audit one or more URLs / local HTML files via /api/v1/audit; fail the build below threshold.",
    )
    p.add_argument(
        "targets",
        nargs="+",
        help=(
            "URLs to audit. With --paste, each target is treated as a "
            "local file path; its contents are POSTed as raw text."
        ),
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum acceptable aeo_score (default: {DEFAULT_THRESHOLD})",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("AEGIS_API_URL"),
        help="Base URL for AEGIS (defaults to env AEGIS_API_URL).",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("AEGIS_API_KEY"),
        help="API key (defaults to env AEGIS_API_KEY).",
    )
    p.add_argument(
        "--paste",
        action="store_true",
        help="Treat targets as local file paths and POST their contents.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output a final JSON summary on stdout (in addition to row logs).",
    )
    return p.parse_args(argv)


def build_payload(target: str, *, paste: bool) -> dict:
    if paste:
        text = Path(target).read_text(encoding="utf-8")
        return {"input_type": "text", "input_value": text}
    return {"input_type": "url", "input_value": target}


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.api_url:
        sys.stderr.write("AEGIS_API_URL must be set (env or --api-url).\n")
        return EXIT_USAGE
    if not args.api_key:
        sys.stderr.write("AEGIS_API_KEY must be set (env or --api-key).\n")
        return EXIT_AUTH

    api_url = args.api_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "aegis-ci/0.1",
    }

    rows: list[dict] = []
    worst = 100
    with httpx.Client(timeout=args.timeout, headers=headers) as client:
        for target in args.targets:
            payload = build_payload(target, paste=args.paste)
            try:
                resp = client.post(f"{api_url}/api/v1/audit", json=payload)
            except httpx.HTTPError as e:
                sys.stderr.write(f"network error for {target}: {e}\n")
                return EXIT_NETWORK
            if resp.status_code == 401:
                sys.stderr.write("AEGIS_API_KEY rejected (401).\n")
                return EXIT_AUTH
            if resp.status_code == 403:
                sys.stderr.write(
                    "AEGIS_API_KEY lacks scope (403). Needs audit:write.\n"
                )
                return EXIT_AUTH
            if resp.status_code >= 400:
                sys.stderr.write(
                    f"audit failed for {target}: HTTP {resp.status_code} {resp.text[:200]}\n"
                )
                rows.append({"target": target, "ok": False, "status": resp.status_code})
                continue
            data = resp.json()
            score = int(data.get("aeo_score", 0))
            band = data.get("band", "")
            worst = min(worst, score)
            ok = score >= args.threshold
            rows.append(
                {
                    "target": target,
                    "ok": ok,
                    "aeo_score": score,
                    "band": band,
                    "threshold": args.threshold,
                }
            )
            symbol = "✓" if ok else "✗"
            sys.stdout.write(
                f"{symbol} {target}  score={score}  band={band}  threshold={args.threshold}\n"
            )

    summary = {
        "min_score": worst,
        "threshold": args.threshold,
        "rows": rows,
        "passed": all(r.get("ok") for r in rows),
    }
    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["passed"] else EXIT_BELOW_THRESHOLD


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
