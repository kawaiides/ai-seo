"""Observability bootstrap — Sentry + structured logging.

`init_sentry()` is a no-op when `SENTRY_DSN` is unset, so dev runs +
tests don't ship spurious events. Production deploys set the DSN via
Terraform Secrets Manager (see `infra/terraform/secrets.tf`).

We import `sentry_sdk` lazily so the dependency stays optional — if
the SDK isn't installed and DSN isn't set, this module is a quiet
no-op. If DSN is set but the SDK isn't installed, we log a warning so
the operator sees the misconfig instead of silently dropping errors.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def _sentry_enabled() -> bool:
    return bool(os.environ.get("SENTRY_DSN"))


def init_sentry() -> bool:
    """Initialise Sentry if `SENTRY_DSN` is set. Returns True on success."""
    if not _sentry_enabled():
        return False
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore[import-not-found]
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration  # type: ignore[import-not-found]
    except ImportError:
        log.warning(
            "SENTRY_DSN set but sentry_sdk not installed; "
            "errors will not be reported. `pip install 'sentry-sdk[fastapi]'`."
        )
        return False

    env = os.environ.get("AEGIS_ENV", "prod")
    release = os.environ.get("AEGIS_RELEASE") or os.environ.get("GIT_SHA")
    traces_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    profiles_rate = float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=env,
        release=release,
        traces_sample_rate=traces_rate,
        profiles_sample_rate=profiles_rate,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        # Don't ship request bodies — they often contain user content
        # (URLs being audited, BYOK keys in headers, etc.).
        send_default_pii=False,
    )
    log.info("Sentry initialised (env=%s release=%s)", env, release or "?")
    return True


def capture_exception(exc: BaseException, **scope_tags: Any) -> None:
    """Forward an exception to Sentry if the SDK is configured.

    Used by the unhandled-exception handler in app/main.py so we don't
    rely on Sentry's middleware to catch errors raised inside our own
    handler chain. Safe to call when Sentry isn't initialised.
    """
    if not _sentry_enabled():
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        return
    with sentry_sdk.push_scope() as scope:
        for k, v in scope_tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_exception(exc)
