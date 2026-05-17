"""Autopilot CLI.

    python -m app.autopilot prospect --seed "best SEO tool" --limit 10
    python -m app.autopilot audit --batch-size 20
    python -m app.autopilot report          # M3 stub
    python -m app.autopilot mail            # M4 stub
    python -m app.autopilot site_reaudit    # weekly re-audit cron entrypoint
    python -m app.autopilot run --seed "X"  # chain everything
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv(override=False)

from app.autopilot.audit_runner import run_pending  # noqa: E402
from app.autopilot.contact_finder import default_finder  # noqa: E402
from app.autopilot.email_sequence import tick as sequence_tick  # noqa: E402
from app.autopilot.outbox_mailer import send_pending as mail_send_pending  # noqa: E402
from app.autopilot.prospector import discover  # noqa: E402
from app.autopilot.report_builder import build_pending as build_reports_pending  # noqa: E402
from app.autopilot.seed_queries import pick_next_seed  # noqa: E402
from app.autopilot.site_runner import run_weekly_reaudit  # noqa: E402
from app.db.base import dispose_engine, get_sessionmaker  # noqa: E402
from app.db.models import Prospect, ProspectStatus  # noqa: E402
from app.integrations.linear import LinearNotifier  # noqa: E402
from app.integrations.slack import SlackNotifier  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _cmd_prospect(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        seed = await _resolve_seed(session, args)
        if seed is None:
            print("prospect: no seed specified and no enabled catalog entries; abort")
            return 2
        new = await discover(session, seed, limit=args.limit)
        await session.commit()
    print(f"prospect: inserted {len(new)} new prospects for seed {seed!r}")
    return 0


async def _resolve_seed(session, args: argparse.Namespace) -> str | None:
    """Pick the query string to feed `discover()`.

    `--seed` is explicit. `--auto-seed` rotates through the catalog.
    Explicit `--seed` always wins so an operator can override the cron.
    """
    if getattr(args, "seed", None):
        return args.seed
    if getattr(args, "auto_seed", False):
        chosen = await pick_next_seed(session)
        if chosen is None:
            return None
        print(f"prospect: auto-seed chose {chosen.slug!r} ({chosen.vertical}, "
              f"locale={chosen.locale})")
        return chosen.text
    return None


async def _cmd_audit(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        audited = await run_pending(
            session,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            fanout_concurrency=args.fanout_concurrency,
        )
        await session.commit()
    print(f"audit: audited {audited} prospects")
    return 0


async def _cmd_report(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        built = await build_reports_pending(
            session, limit=args.limit, write_pdf=not args.no_pdf
        )
        await session.commit()
    print(f"report: built {built} reports")
    return 0


async def _cmd_contacts(args: argparse.Namespace) -> int:
    """Walk audited-but-uncontacted prospects, find emails, insert Contact rows."""
    finder = default_finder()
    sm = get_sessionmaker()
    found = 0
    async with sm() as session:
        prospects = (
            await session.execute(
                select(Prospect)
                .where(Prospect.status == ProspectStatus.audited)
                .limit(args.limit)
            )
        ).scalars().all()
        for p in prospects:
            contacts = await finder.find(p)
            for c in contacts:
                session.add(c)
                found += 1
        await session.commit()
    print(f"contacts: discovered {found} contacts across {len(prospects)} prospects")
    return 0


async def _cmd_mail(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        stats = await mail_send_pending(
            session,
            min_score_cutoff=args.cutoff,
            max_sends=args.max_sends,
            dry_run=args.dry_run,
        )
        await session.commit()
    print(
        f"mail: eligible={stats.eligible} sent={stats.sent} "
        f"skipped={stats.skipped} failed={stats.failed}"
    )
    return 0


async def _cmd_sequence(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        results = await sequence_tick(
            session,
            max_per_variant=args.max_sends,
            dry_run=args.dry_run,
        )
        await session.commit()
    if not results:
        print("sequence: nothing due")
        return 0
    for variant, stats in results.items():
        print(
            f"sequence[{variant}]: eligible={stats.eligible} sent={stats.sent} "
            f"skipped={stats.skipped} failed={stats.failed}"
        )
    return 0


async def _cmd_site_reaudit(args: argparse.Namespace) -> int:
    """Re-audit every tracked Site + dispatch score-drop notifications.

    Wire this to a weekly cron (or call it as a one-shot from the admin
    panel). Notifiers self-skip when their env vars aren't set so dev
    runs don't accidentally page production channels.
    """
    notifiers = (SlackNotifier(), LinearNotifier())
    sm = get_sessionmaker()
    async with sm() as session:
        results = await run_weekly_reaudit(
            session,
            pages_per_site=args.pages_per_site,
            concurrency=args.concurrency,
            notifiers=notifiers,
        )
        await session.commit()
    total_alerts = sum(len(r.alerts) for r in results)
    delivered = sum(
        1
        for r in results
        for d in r.notify_results
        if d.result.delivered
    )
    failed = sum(
        1
        for r in results
        for d in r.notify_results
        if not d.result.delivered
    )
    print(
        f"site_reaudit: sites={len(results)} alerts={total_alerts} "
        f"notify_delivered={delivered} notify_failed={failed}"
    )
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    for step in (_cmd_prospect, _cmd_audit, _cmd_contacts,
                 _cmd_report, _cmd_mail, _cmd_sequence):
        rc = await step(args)
        if rc != 0:
            return rc
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.autopilot", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prospect = sub.add_parser("prospect", help="Run SerpAPI discovery")
    p_prospect.add_argument("--seed", default=None,
                            help="Explicit search query. Required unless --auto-seed.")
    p_prospect.add_argument(
        "--auto-seed", action="store_true",
        help="Rotate through app/autopilot/seed_queries.SEED_QUERIES; "
             "picks the least-recently-used slug, stamps usage in DB.",
    )
    p_prospect.add_argument("--limit", type=int, default=10)
    p_prospect.set_defaults(func=_cmd_prospect)

    p_audit = sub.add_parser("audit", help="Audit queued prospects")
    p_audit.add_argument("--batch-size", type=int, default=20)
    p_audit.add_argument("--concurrency", type=int, default=5)
    p_audit.add_argument("--fanout-concurrency", type=int, default=2)
    p_audit.set_defaults(func=_cmd_audit)

    p_report = sub.add_parser("report", help="Build HTML/PDF reports for audited prospects")
    p_report.add_argument("--limit", type=int, default=50)
    p_report.add_argument("--no-pdf", action="store_true",
                          help="Skip WeasyPrint PDF; HTML only.")
    p_report.set_defaults(func=_cmd_report)

    p_contacts = sub.add_parser("contacts", help="Discover contact emails for audited prospects")
    p_contacts.add_argument("--limit", type=int, default=50)
    p_contacts.set_defaults(func=_cmd_contacts)

    p_mail = sub.add_parser("mail", help="Send cold outreach emails")
    p_mail.add_argument("--cutoff", type=int, default=70,
                        help="Only mail prospects with AEO score below this.")
    p_mail.add_argument("--max-sends", type=int, default=30,
                        help="Hard daily cap during deliverability warm-up.")
    p_mail.add_argument("--dry-run", action="store_true",
                        help="Render + log + insert Outreach row but do not send.")
    p_mail.set_defaults(func=_cmd_mail)

    p_reaudit = sub.add_parser(
        "site_reaudit",
        help="Re-audit every tracked Site + dispatch Slack/Linear alerts",
    )
    p_reaudit.add_argument("--pages-per-site", type=int, default=50)
    p_reaudit.add_argument("--concurrency", type=int, default=4)
    p_reaudit.set_defaults(func=_cmd_site_reaudit)

    p_seq = sub.add_parser("sequence", help="Run state-aware follow-up drip")
    p_seq.add_argument("--max-sends", type=int, default=30)
    p_seq.add_argument("--dry-run", action="store_true")
    p_seq.set_defaults(func=_cmd_sequence)

    p_run = sub.add_parser("run", help="prospect → audit → report → mail")
    p_run.add_argument("--seed", default=None,
                       help="Explicit search query. Required unless --auto-seed.")
    p_run.add_argument(
        "--auto-seed", action="store_true",
        help="Rotate through SEED_QUERIES instead of requiring --seed.",
    )
    p_run.add_argument("--limit", type=int, default=10)
    p_run.add_argument("--batch-size", type=int, default=20)
    p_run.add_argument("--concurrency", type=int, default=5)
    p_run.add_argument("--fanout-concurrency", type=int, default=2)
    p_run.add_argument("--no-pdf", action="store_true")
    p_run.add_argument("--cutoff", type=int, default=70)
    p_run.add_argument("--max-sends", type=int, default=30)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    return parser

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    async def _run() -> int:
        try:
            return await args.func(args)
        finally:
            await dispose_engine()

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
