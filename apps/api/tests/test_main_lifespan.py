"""Skill Runtime mission Phase 12: the outbox dispatcher's deployment mode
(embedded-in-API-process vs. left to an external `python -m
app.outbox_worker`) must be an explicit, operator-controlled choice, not a
hard-coded always-on call. These tests drive app.main.lifespan directly
against both settings.outbox_mode values and assert start_background_thread
is called only for "embedded"."""
from __future__ import annotations

import asyncio

import app.main as main_module


def _run_lifespan_once(monkeypatch, *, outbox_mode: str):
    calls = []
    stop_events = []

    class _FakeStopEvent:
        def set(self):
            stop_events.append(True)

    def _fake_start_background_thread():
        calls.append(True)
        return None, _FakeStopEvent()

    monkeypatch.setattr(main_module, "start_background_thread", _fake_start_background_thread)
    monkeypatch.setattr(main_module.settings, "outbox_mode", outbox_mode)
    monkeypatch.setattr(main_module, "assert_production_secrets_are_safe", lambda: None)
    monkeypatch.setattr(main_module, "ensure_buckets", lambda: None)

    async def _drive():
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(_drive())
    return calls, stop_events


def test_embedded_mode_starts_and_stops_the_background_dispatcher(monkeypatch):
    calls, stop_events = _run_lifespan_once(monkeypatch, outbox_mode="embedded")
    assert calls == [True]
    assert stop_events == [True]


def test_external_mode_never_starts_the_background_dispatcher(monkeypatch):
    calls, stop_events = _run_lifespan_once(monkeypatch, outbox_mode="external")
    assert calls == []
    assert stop_events == []
