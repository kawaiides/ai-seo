"""Blog scheduler — wakes hourly, generates a new article every 3 days.

Two ways to run:

1. In-process: started automatically from FastAPI startup if the env var
   `AEGIS_BLOG_AUTOGEN=1` is set. Survives one Uvicorn worker; if you run
   multiple workers, use option 2 instead.

2. Out-of-process (recommended for production): a cron job that runs
       .venv/bin/python -m app.autopilot.blog_scheduler --once
   on a 1-hour cadence. The `is_due()` check inside `run_if_due` decides
   whether the call actually generates anything, so over-scheduling is safe.

Either path leaves the same JSON file in `app/content/generated/`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow `python -m app.autopilot.blog_scheduler` to find the package
# when invoked from a system cron (no shell sourcing).
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=ROOT / ".env", override=False)

from app.services.blog_generator import (  # noqa: E402
    INTERVAL_HOURS,
    is_due,
    read_state,
    run_if_due,
)

log = logging.getLogger("aegis.blog_scheduler")

CHECK_EVERY_SECONDS = 60 * 60  # poll cadence inside the in-process loop


async def _loop(interval_hours: int) -> None:
    """In-process loop. Sleeps, checks `is_due`, generates when due."""
    log.info("blog_scheduler: in-process loop started (interval=%dh)", interval_hours)
    while True:
        try:
            await run_if_due(interval_hours=interval_hours)
        except Exception:
            log.exception("blog_scheduler: tick failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def start_in_process(interval_hours: int = INTERVAL_HOURS) -> asyncio.Task:
    """Schedule the loop on the running asyncio loop. Returns the Task so
    callers can cancel it on shutdown if they want to."""
    return asyncio.create_task(_loop(interval_hours), name="blog-scheduler")


async def main() -> int:
    ap = argparse.ArgumentParser(description="AEGIS blog auto-generator scheduler.")
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run the due-check once and exit (cron-friendly).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Bypass the interval check and generate now (admin override).",
    )
    ap.add_argument(
        "--status",
        action="store_true",
        help="Print scheduler state and exit. No LLM calls.",
    )
    ap.add_argument(
        "--interval-hours",
        type=int,
        default=INTERVAL_HOURS,
        help=f"Hours between generations (default {INTERVAL_HOURS}).",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.status:
        state = read_state()
        print(f"due now:           {is_due(args.interval_hours)}")
        print(f"interval (hours):  {args.interval_hours}")
        print(f"last generated:    {state.get('last_generated_at', '(never)')}")
        print(f"last slug:         {state.get('last_slug', '(never)')}")
        print(f"total generated:   {state.get('total_generated', 0)}")
        return 0

    if args.force:
        from app.services.blog_generator import generate_one, save_generated

        article = await generate_one()
        path = save_generated(article)
        print(f"generated: {article['slug']} (score={article['_meta']['final_score']}) → {path}")
        return 0

    if args.once:
        result = await run_if_due(args.interval_hours)
        if result is None:
            print("blog_scheduler: skipped (not due or generation failed)")
        else:
            print(f"blog_scheduler: generated {result['slug']} (score={result['_meta']['final_score']})")
        return 0

    # Persistent loop (analogous to `start_in_process` for use as a
    # standalone daemon: `python -m app.autopilot.blog_scheduler`).
    await _loop(args.interval_hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
