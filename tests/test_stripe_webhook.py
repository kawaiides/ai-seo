"""HTTP tests for /webhooks/stripe.

Covers:
  * 400 on bad signature
  * 200 + Subscription row on activating event
  * Replay returns 200 no-op (idempotency via webhook_event PK)
  * payment_failed marks past_due + funnel at_risk + dunning Outreach
  * subscription.deleted marks canceled + funnel churned
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import payment_webhooks
from app.db.base import get_session
from app.db.models import (
    Audit,
    Contact,
    FunnelEvent,
    FunnelStage,
    Outreach,
    Processor,
    Subscription,
    SubscriptionStatus,
    User,
    UserPlan,
    WebhookEvent,
)
from app.main import app


SECRET = "whsec_unit_test"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)


def _sign(body: bytes, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    signed = f"{ts}.{body.decode('utf-8')}".encode("utf-8")
    mac = hmac.new(SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event(
    event_id: str = "evt_test_1",
    event_type: str = "checkout.session.completed",
    *,
    subscription_id: str = "sub_test_1",
    user_id: str | None = None,
    customer_email: str = "alice@x.com",
    current_period_end: int = 1_900_000_000,
) -> dict[str, Any]:
    obj = {
        "id": "cs_1",
        "object": "checkout.session",
        "subscription": subscription_id,
        "customer_email": customer_email,
        "current_period_end": current_period_end,
    }
    if user_id:
        obj["client_reference_id"] = user_id
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": obj},
    }


# ----------------------------------------------------------------------
# FakeDB: an in-memory stand-in just rich enough for the webhook handler
# ----------------------------------------------------------------------

class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def first(self):
        if self._v is None:
            return None
        return _Row(self._v if isinstance(self._v, (list, tuple)) else [self._v])

    def all(self):
        return [self._v] if self._v else []


class _Row:
    def __init__(self, value):
        self._v = list(value) if isinstance(value, (list, tuple)) else [value]

    def __iter__(self): return iter(self._v)
    def __getitem__(self, i): return self._v[i]


class FakeDB:
    """Routes by SQL fragment to fake rows the handler needs."""

    def __init__(self, *, user: User | None = None,
                 contact_audit: tuple[Contact, Audit] | None = None,
                 existing_webhook: WebhookEvent | None = None,
                 existing_subscription: Subscription | None = None):
        self.user = user
        self.contact_audit = contact_audit
        self.existing_webhook = existing_webhook
        self.existing_subscription = existing_subscription
        self.added: list[Any] = []
        self.executed: list[str] = []
        self.webhook_marked_processed = False

    def add(self, obj):
        self.added.append(obj)
        # Assign synthetic PK so downstream code sees a non-null id.
        if isinstance(obj, Subscription) and obj.id is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def execute(self, stmt):
        text = str(stmt).lower()
        self.executed.append(text)

        # ON CONFLICT DO NOTHING ... RETURNING webhook_event.id
        if "insert into webhook_event" in text:
            if self.existing_webhook is not None:
                return _Result(None)  # collision → no row returned
            return _Result(_Row(["evt_test_1"]))

        # SELECT WebhookEvent WHERE id = X (for _mark_processed)
        if "from webhook_event" in text:
            evt = WebhookEvent(
                id="evt_test_1",
                processor=Processor.stripe,
                event_type="x",
                payload={},
            )
            self.webhook_event_row = evt
            return _Result(evt)

        # SELECT user
        if "from user_" in text:
            return _Result(self.user)

        # SELECT subscription
        if "from subscription" in text:
            return _Result(self.existing_subscription)

        # SELECT Contact, Audit join (dunning lookup)
        if "from contact" in text and "join audit" in text:
            return _Result(self.contact_audit)

        # INSERT INTO outreach ... ON CONFLICT DO NOTHING (dunning)
        if "insert into outreach" in text:
            return _Result(None)

        # SELECT for FunnelEvent / etc.
        return _Result(None)


@pytest.fixture
def client_with_db():
    """Returns (client, fake_db_factory). Each test calls factory(...) to
    build a fresh FakeDB with the rows it needs."""
    state = {"db": None}

    async def _override():
        yield state["db"]

    app.dependency_overrides[get_session] = _override
    try:
        def _set(db):
            state["db"] = db
            return TestClient(app)
        yield _set
    finally:
        app.dependency_overrides.pop(get_session, None)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_bad_signature_returns_400(client_with_db):
    db = FakeDB()
    client = client_with_db(db)
    body = json.dumps(_event()).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert resp.status_code == 400


def test_missing_signature_header_returns_400(client_with_db):
    db = FakeDB()
    client = client_with_db(db)
    body = json.dumps(_event()).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body)
    assert resp.status_code == 400


def test_checkout_completed_creates_subscription(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="alice@x.com", plan=UserPlan.free)
    db = FakeDB(user=user)
    client = client_with_db(db)

    body = json.dumps(_event(user_id=str(user.id))).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": _sign(body)})

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1
    sub = subs[0]
    assert sub.external_id == "sub_test_1"
    assert sub.status == SubscriptionStatus.active
    assert sub.processor == Processor.stripe
    assert user.plan == UserPlan.pro_usd
    # Funnel converted event was recorded.
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.converted for e in fe)


def test_replay_event_is_no_op(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="alice@x.com")
    db = FakeDB(
        user=user,
        existing_webhook=WebhookEvent(
            id="evt_test_1", processor=Processor.stripe,
            event_type="checkout.session.completed", payload={},
        ),
    )
    client = client_with_db(db)
    body = json.dumps(_event(user_id=str(user.id))).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": _sign(body)})

    assert resp.status_code == 200
    assert resp.json() == {"received": True, "duplicate": True}
    # No Subscription added on replay.
    assert not any(isinstance(x, Subscription) for x in db.added)


def test_payment_failed_marks_past_due_and_enqueues_dunning(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="bob@x.com",
                plan=UserPlan.pro_usd)
    prospect_id = 99
    contact = Contact(prospect_id=prospect_id, email="bob@x.com")
    contact.id = 1
    audit = Audit(prospect_id=prospect_id, aeo_score=42, band="Significant Gaps")
    audit.id = 5
    audit.token_jti = uuid.UUID("00000000-0000-0000-0000-000000000abc")
    db = FakeDB(user=user, contact_audit=(contact, audit))
    client = client_with_db(db)

    body = json.dumps(
        _event(event_id="evt_pf", event_type="invoice.payment_failed",
               user_id=str(user.id))
    ).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": _sign(body)})

    assert resp.status_code == 200
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1 and subs[0].status == SubscriptionStatus.past_due
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.at_risk for e in fe)
    # Dunning INSERT was attempted.
    assert any("insert into outreach" in q for q in db.executed)


def test_subscription_deleted_marks_canceled_and_churned(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="c@x.com",
                plan=UserPlan.pro_usd)
    db = FakeDB(user=user)
    client = client_with_db(db)

    body = json.dumps(
        _event(event_id="evt_del", event_type="customer.subscription.deleted",
               user_id=str(user.id))
    ).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": _sign(body)})

    assert resp.status_code == 200
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1 and subs[0].status == SubscriptionStatus.canceled
    assert user.plan == UserPlan.free
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.churned for e in fe)


def test_event_without_subscription_id_is_skipped(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="d@x.com")
    db = FakeDB(user=user)
    client = client_with_db(db)
    bad_event = {
        "id": "evt_skip",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": {}},  # no subscription / id
    }
    body = json.dumps(bad_event).encode("utf-8")
    resp = client.post("/webhooks/stripe", content=body,
                       headers={"Stripe-Signature": _sign(body)})
    assert resp.status_code == 200
    assert not any(isinstance(x, Subscription) for x in db.added)
