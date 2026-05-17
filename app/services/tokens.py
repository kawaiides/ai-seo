"""Signed-token utilities for public report URLs and post-checkout sessions.

Single source of truth for `REPORT_SIGNING_KEY` and `SESSION_SIGNING_KEY`.
Wraps `itsdangerous` so callers never touch the raw serializer.
"""

from __future__ import annotations

import os
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


REPORT_TOKEN_TTL_SECONDS = 90 * 24 * 3600  # 90 days
SESSION_TOKEN_TTL_SECONDS = 365 * 24 * 3600  # 1 year


class TokenError(Exception):
    """Generic decode failure (bad signature, malformed, expired)."""


def _key(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise RuntimeError(
            f"{env_var} is not set. Generate a 32+ byte random string and put it in .env."
        )
    return value


def _report_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_key("REPORT_SIGNING_KEY"), salt="aegis-report-v1")


def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_key("SESSION_SIGNING_KEY"), salt="aegis-session-v1")


def sign_report(audit_id: int, token_jti: str) -> str:
    return _report_serializer().dumps({"aid": audit_id, "jti": token_jti})


def decode_report(token: str, max_age: int = REPORT_TOKEN_TTL_SECONDS) -> dict[str, Any]:
    try:
        return _report_serializer().loads(token, max_age=max_age)
    except SignatureExpired as e:
        raise TokenError("report token expired") from e
    except BadSignature as e:
        raise TokenError("report token invalid") from e


def sign_session(user_id: str) -> str:
    return _session_serializer().dumps({"uid": user_id})


def decode_session(token: str, max_age: int = SESSION_TOKEN_TTL_SECONDS) -> dict[str, Any]:
    try:
        return _session_serializer().loads(token, max_age=max_age)
    except SignatureExpired as e:
        raise TokenError("session token expired") from e
    except BadSignature as e:
        raise TokenError("session token invalid") from e
