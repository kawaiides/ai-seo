"""Locale plumbing through fan-out + GEO probe (Phase F)."""

from __future__ import annotations

import json

import pytest

from app.services.fanout_engine import _locale_suffix, generate_fanout
from app.services.geo.probe import OpenAIChatProbe, _locale_line
from tests.conftest import FakeLLMClient


def test_locale_suffix_for_known_code_uses_english_name():
    s = _locale_suffix("hi")
    assert "Hindi" in s
    assert "hi" in s


def test_locale_suffix_empty_for_none():
    assert _locale_suffix(None) == ""
    assert _locale_suffix("") == ""


def test_locale_suffix_falls_back_to_uppercase_for_unknown_code():
    s = _locale_suffix("xx")
    assert "XX" in s


@pytest.mark.asyncio
async def test_generate_fanout_threads_locale_into_system_prompt(valid_payload_str):
    fake = FakeLLMClient([valid_payload_str])
    await generate_fanout("best CRM software", client=fake, target_locale="es")
    system, _user = fake.calls[0]
    assert "Spanish" in system
    assert "ISO 639-1 `es`" in system


@pytest.mark.asyncio
async def test_generate_fanout_omits_locale_clause_when_none(valid_payload_str):
    fake = FakeLLMClient([valid_payload_str])
    await generate_fanout("best CRM software", client=fake, target_locale=None)
    system, _user = fake.calls[0]
    assert "ISO 639-1" not in system


def test_geo_probe_locale_line_when_provided():
    s = _locale_line("hi")
    assert "Hindi" in s
    assert "ANSWER_LOCALE" in s


def test_geo_probe_locale_line_empty_when_none():
    assert _locale_line(None) == ""
    assert _locale_line("") == ""


@pytest.mark.asyncio
async def test_openai_probe_includes_locale_in_user_prompt():
    fake = FakeLLMClient([
        json.dumps({"urls": ["https://en.wikipedia.org/wiki/Foo"]})
    ])
    probe = OpenAIChatProbe(client=fake)
    await probe.probe("foo", locale="ta")
    _system, user = fake.calls[0]
    assert "Tamil" in user
    assert "ANSWER_LOCALE" in user


@pytest.mark.asyncio
async def test_openai_probe_no_locale_line_when_locale_absent():
    fake = FakeLLMClient([
        json.dumps({"urls": ["https://en.wikipedia.org/wiki/Foo"]})
    ])
    probe = OpenAIChatProbe(client=fake)
    await probe.probe("foo")
    _system, user = fake.calls[0]
    assert "ANSWER_LOCALE" not in user
