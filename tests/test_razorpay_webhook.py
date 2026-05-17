"""HTTP tests for /webhooks/razorpay.

Mirrors Stripe webhook tests: bad sig 400, activate → Subscription +
funnel converted + plan upgrade, replay → 200 no-op, halted → past_due +
at_risk + dunning, cancelled → canceled + churned + downgrade.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

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

SECRET = "rzp_wh_unit_test"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _event(
    event_type: str = "subscription.activated",
    *,
    user_id: str | None = None,
    customer_email: str = "alice@x.com",
    sub_id: str = "sub_rzp_1",
) -> dict[str, Any]:
    notes: dict[str, str] = {"email": customer_email}
    if user_id:
        notes["user_id"] = user_id
    return {
        "event": event_type,
        "payload": {
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "current_end": 1_900_000_000,
                    "notes": notes,
                }
            }
        },
    }


# ----------------------------------------------------------------------
# FakeDB — same shape as Stripe webhook tests
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
    def __init__(self, *, user: User | None = None,
                 contact_audit: tuple[Contact, Audit] | None = None,
                 existing_webhook: WebhookEvent | None = None):
        self.user = user
        self.contact_audit = contact_audit
        self.existing_webhook = existing_webhook
        self.added: list[Any] = []
        self.executed: list[str] = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Subscription) and obj.id is None:
            obj.id = uuid.uuid4()

    async def flush(self): pass
    async def commit(self): pass
    async def rollback(self): pass

    async def execute(self, stmt):
        text = str(stmt).lower()
        self.executed.append(text)

        if "insert into webhook_event" in text:
            if self.existing_webhook is not None:
                return _Result(None)
            return _Result(_Row(["evt_id"]))
        if "from webhook_event" in text:
            return _Result(WebhookEvent(id="x", processor=Processor.razorpay,
                                        event_type="x", payload={}))
        if "from user_" in text:
            return _Result(self.user)
        if "from subscription" in text:
            return _Result(None)
        if "from contact" in text and "join audit" in text:
            return _Result(self.contact_audit)
        if "insert into outreach" in text:
            return _Result(None)
        return _Result(None)


@pytest.fixture
def client_with_db():
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
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": "deadbeef"})
    assert resp.status_code == 400


def test_missing_signature_returns_400(client_with_db):
    client = client_with_db(FakeDB())
    body = json.dumps(_event()).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body)
    assert resp.status_code == 400


def test_activated_creates_subscription_and_inr_plan(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="alice@x.com", plan=UserPlan.free)
    db = FakeDB(user=user)
    client = client_with_db(db)
    body = json.dumps(_event(user_id=str(user.id))).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body)})

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1
    sub = subs[0]
    assert sub.external_id == "sub_rzp_1"
    assert sub.status == SubscriptionStatus.active
    assert sub.processor == Processor.razorpay
    assert user.plan == UserPlan.pro_inr
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.converted for e in fe)


def test_replay_is_no_op(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="alice@x.com")
    db = FakeDB(
        user=user,
        existing_webhook=WebhookEvent(
            id="x", processor=Processor.razorpay,
            event_type="subscription.activated", payload={},
        ),
    )
    client = client_with_db(db)
    body = json.dumps(_event(user_id=str(user.id))).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body)})
    assert resp.status_code == 200
    assert resp.json() == {"received": True, "duplicate": True}
    assert not any(isinstance(x, Subscription) for x in db.added)


def test_halted_marks_past_due_and_enqueues_dunning(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="bob@x.com",
                plan=UserPlan.pro_inr)
    contact = Contact(prospect_id=1, email="bob@x.com"); contact.id = 1
    audit = Audit(prospect_id=1, aeo_score=42, band="Significant Gaps")
    audit.id = 2; audit.token_jti = uuid.uuid4()
    db = FakeDB(user=user, contact_audit=(contact, audit))
    client = client_with_db(db)
    body = json.dumps(
        _event(event_type="subscription.halted", user_id=str(user.id))
    ).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body)})

    assert resp.status_code == 200
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1 and subs[0].status == SubscriptionStatus.past_due
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.at_risk for e in fe)
    assert any("insert into outreach" in q for q in db.executed)


def test_cancelled_marks_canceled_and_churned(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="c@x.com",
                plan=UserPlan.pro_inr)
    db = FakeDB(user=user)
    client = client_with_db(db)
    body = json.dumps(
        _event(event_type="subscription.cancelled", user_id=str(user.id))
    ).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body)})

    assert resp.status_code == 200
    subs = [x for x in db.added if isinstance(x, Subscription)]
    assert len(subs) == 1 and subs[0].status == SubscriptionStatus.canceled
    assert user.plan == UserPlan.free
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.churned for e in fe)


def test_subscription_charged_recorded_as_retained(client_with_db):
    user = User(id=uuid.uuid4(), session_id="s", email="d@x.com",
                plan=UserPlan.pro_inr)
    db = FakeDB(user=user)
    client = client_with_db(db)
    body = json.dumps(
        _event(event_type="subscription.charged", user_id=str(user.id))
    ).encode("utf-8")
    resp = client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body)})
    assert resp.status_code == 200
    fe = [x for x in db.added if isinstance(x, FunnelEvent)]
    assert any(e.stage == FunnelStage.retained for e in fe)
