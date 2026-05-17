"""Unit tests for autopilot/outbox_mailer.

aiosmtplib.send is monkeypatched; no real SMTP server needed. The
database layer is not exercised — `send_pending` is tested via direct
template rendering plus a focused candidate-selection contract test
using a FakeSession that records SQL intent.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.autopilot import outbox_mailer
from app.autopilot.outbox_mailer import (
    RenderedEmail,
    SendStats,
    _attach_name,
    _build_message,
    render_email,
)
from app.autopilot.report_builder import RenderContext
from app.db.models import Audit, Contact, Prospect


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch):
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)


def _ctx() -> RenderContext:
    return RenderContext(
        audit_id=99,
        domain="example.com",
        url="https://example.com/post",
        target_keyword="best vector db",
        aeo_score=42,
        band="Significant Gaps",
        failed_checks=[
            {"check_id": "readability", "name": "Snippet readability",
             "score": 0, "max_score": 20, "recommendation": "Shorten sentences",
             "details": {"flesch_kincaid_grade": 12.4}}
        ],
        top_missing_comparative=[
            {"type": "comparative", "query": "pgvector vs Pinecone"},
            {"type": "comparative", "query": "Weaviate vs Qdrant"},
        ],
        missing_gap_types=["how_to"],
        readability_warning="Flesch–Kincaid grade 12.4 — above threshold.",
        report_url="https://aegis.test/r/abc123",
        checkout_url="https://aegis.test/?audit=99",
    )


# ---- template rendering ----


def test_render_email_subject_text_html() -> None:
    rendered = render_email(_ctx())

    assert "example.com" in rendered.subject
    assert "42/100" in rendered.subject

    assert "https://aegis.test/r/abc123" in rendered.text
    assert "pgvector vs Pinecone" in rendered.text
    assert "Flesch" in rendered.text

    assert "<a href=\"https://aegis.test/r/abc123\"" in rendered.html
    assert "pgvector vs Pinecone" in rendered.html
    assert "<img" in rendered.html  # tracking pixel embedded


def test_render_email_personalizes_with_contact_name() -> None:
    env = outbox_mailer._jinja()
    ctx_dict = _attach_name(_ctx(), "Alice")
    text = env.get_template("cold_outreach.txt.j2").render(ctx=ctx_dict)
    assert "Hi Alice" in text


def test_render_email_no_name_falls_back_to_bare_greeting() -> None:
    env = outbox_mailer._jinja()
    ctx_dict = _attach_name(_ctx(), None)
    text = env.get_template("cold_outreach.txt.j2").render(ctx=ctx_dict)
    assert "Hi," in text


def test_body_hash_is_stable() -> None:
    r1 = render_email(_ctx())
    r2 = render_email(_ctx())
    assert r1.body_hash() == r2.body_hash()
    assert len(r1.body_hash()) == 64


def test_build_message_has_text_and_html_parts() -> None:
    rendered = RenderedEmail(
        subject="hi", text="plain text body", html="<p>html body</p>"
    )
    msg = _build_message("from@a.com", "to@b.com", rendered)

    assert msg["From"] == "from@a.com"
    assert msg["To"] == "to@b.com"
    assert msg["Subject"] == "hi"
    payloads = [p.get_content() for p in msg.iter_parts()]
    assert any("plain text body" in p for p in payloads)
    assert any("<p>html body</p>" in p for p in payloads)


# ---- send_pending end-to-end with FakeSession ----


class FakeSession:
    """Records execute() calls and the order in which they happened."""

    def __init__(
        self,
        *,
        candidates: list[tuple[Contact, Audit, Prospect]],
    ):
        self._candidates = candidates
        self._reserve_counter = 0
        self.updates: list[dict[str, Any]] = []

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "select " in text and " contact " in text and " audit " in text:
            return _Rows([tuple(r) for r in self._candidates])
        if "insert into outreach" in text:
            # First call succeeds; if you want to test the "already exists"
            # branch, override this counter directly in the test.
            self._reserve_counter += 1
            return _Row([self._reserve_counter])
        if "update outreach" in text:
            # Capture which values were written — sent_at / body_hash on success,
            # error on failure.
            self.updates.append({"stmt": text})
            return _Empty()
        return _Empty()


class _Rows:
    def __init__(self, items):
        self._items = items

    def all(self):
        return [_Row(x) for x in self._items]


class _Row:
    def __init__(self, value):
        self._value = list(value) if isinstance(value, (list, tuple)) else [value]

    def __iter__(self):
        return iter(self._value)

    def __getitem__(self, idx):
        return self._value[idx]


class _Empty:
    def first(self):
        return None

    def all(self):
        return []


def _make_pair(score: int = 42, contact_id: int = 1, audit_id: int = 10):
    p = Prospect(url="https://ex.com/a", domain="ex.com", target_keyword="x")
    p.id = 7
    c = Contact(prospect_id=p.id, email="a@ex.com", source="manual_csv")
    c.id = contact_id
    a = Audit(prospect_id=p.id, aeo_score=score, band="Significant Gaps")
    a.id = audit_id
    a.token_jti = uuid.UUID("00000000-0000-0000-0000-000000000001")
    a.fanout_payload = None
    a.missing_gap_types = None
    a.failed_checks = None
    return c, a, p


class _RowReturning(_Empty):
    def __init__(self, value):
        self._value = value

    def first(self):
        return _Row([self._value])


class FakeSessionInsertable(FakeSession):
    """Allows test to control which insert_outreach calls return a row vs None."""

    def __init__(self, *, candidates, reserve_results):
        super().__init__(candidates=candidates)
        self._reserve_queue = list(reserve_results)

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "insert into outreach" in text:
            value = self._reserve_queue.pop(0) if self._reserve_queue else None
            if value is None:
                return _Empty()
            return _RowReturning(value)
        return await super().execute(stmt)


@pytest.mark.asyncio
async def test_send_pending_sends_one_message(monkeypatch):
    sent: list[Any] = []

    async def _fake_send(message, **kwargs):
        sent.append(message)

    monkeypatch.setattr(outbox_mailer.aiosmtplib, "send", _fake_send)

    c, a, p = _make_pair()
    session = FakeSessionInsertable(
        candidates=[(c, a, p)],
        reserve_results=[101],
    )
    stats = await outbox_mailer.send_pending(session, dry_run=False)

    assert stats.sent == 1
    assert stats.eligible == 1
    assert stats.skipped == 0
    assert stats.failed == 0
    assert len(sent) == 1
    assert sent[0]["To"] == "a@ex.com"
    # Two updates: nothing (insert succeeded), then sent_at + body_hash
    assert any("update outreach" in u["stmt"] for u in session.updates)


@pytest.mark.asyncio
async def test_send_pending_dry_run_does_not_call_smtp(monkeypatch):
    sent: list[Any] = []

    async def _fake_send(*a, **kw):
        sent.append(True)

    monkeypatch.setattr(outbox_mailer.aiosmtplib, "send", _fake_send)

    c, a, p = _make_pair()
    session = FakeSessionInsertable(
        candidates=[(c, a, p)],
        reserve_results=[1],
    )
    stats = await outbox_mailer.send_pending(session, dry_run=True)
    assert stats.sent == 1
    assert sent == []   # never invoked SMTP


@pytest.mark.asyncio
async def test_send_pending_skips_when_reserve_collides(monkeypatch):
    """ON CONFLICT DO NOTHING returns no id → we skip this pair."""

    async def _fake_send(*a, **kw):
        raise AssertionError("must not send when reservation collided")

    monkeypatch.setattr(outbox_mailer.aiosmtplib, "send", _fake_send)

    c, a, p = _make_pair()
    session = FakeSessionInsertable(
        candidates=[(c, a, p)],
        reserve_results=[None],
    )
    stats = await outbox_mailer.send_pending(session, dry_run=False)
    assert stats.sent == 0
    assert stats.skipped == 1


@pytest.mark.asyncio
async def test_send_pending_records_error_on_smtp_failure(monkeypatch):
    async def _angry_send(*a, **kw):
        raise RuntimeError("smtp 451 try again")

    monkeypatch.setattr(outbox_mailer.aiosmtplib, "send", _angry_send)

    c, a, p = _make_pair()
    session = FakeSessionInsertable(
        candidates=[(c, a, p)],
        reserve_results=[42],
    )
    stats = await outbox_mailer.send_pending(session, dry_run=False)

    assert stats.sent == 0
    assert stats.failed == 1
    assert stats.errors and "RuntimeError" in stats.errors[0]


@pytest.mark.asyncio
async def test_send_pending_respects_max_sends_cap(monkeypatch):
    async def _fake_send(*a, **kw):
        pass

    monkeypatch.setattr(outbox_mailer.aiosmtplib, "send", _fake_send)

    pairs = [_make_pair(contact_id=i, audit_id=100 + i) for i in range(5)]
    session = FakeSessionInsertable(
        candidates=pairs,
        reserve_results=[1, 2, 3, 4, 5],
    )
    stats = await outbox_mailer.send_pending(session, dry_run=True, max_sends=2)
    assert stats.sent == 2
    assert stats.eligible == 5
