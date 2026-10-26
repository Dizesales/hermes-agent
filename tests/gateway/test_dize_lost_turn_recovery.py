"""Dize: an opt-in startup recovery resumes chats whose turn was lost to an abrupt stop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.session_authority import SessionAuthority
from hermes_state import SessionDB
from hermes_state_runtime import (
    admit_session_input,
    begin_runtime_epoch,
    claim_session_input,
    list_session_admissions,
    recover_session_inputs,
)

SID, ROUTE = "native-session", "telegram:default:chat"


class _Adapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, metadata))


def _runner(adapter):
    return SimpleNamespace(
        _draining=False, session_store=SimpleNamespace(),
        _adapter_for_source=lambda source: adapter,
        _thread_metadata_for_source=lambda source: {"thread_id": source.thread_id,
                                                    "reply_to": source.message_id})


def _source(platform=Platform.TELEGRAM):
    return SessionSource(platform=platform, chat_id="chat", user_id="u", thread_id="447",
                         message_id="21844", profile="default")


def _crash_mid_turn(db, principal="messaging:native"):
    """Epoch 1 claims a human turn and dies; a follower stays queued; epoch 2 fences it unknown."""
    db.create_session(SID, source="telegram")
    first = begin_runtime_epoch(db, instance_id="first")
    for request in ("lost", "follower"):
        admit_session_input(db, epoch=first, principal_id=principal, session_id=SID, request_id=request,
                            payload={"native_text_v1": {"source": {}, "route": ROUTE}})
    claim_session_input(db, epoch=first, session_id=SID)
    second = begin_runtime_epoch(db, instance_id="restart")
    assert recover_session_inputs(db, epoch=second) == 1
    return second


async def _recover(monkeypatch, db, epoch, source, adapter):
    async def check_native_route(runner, payload, target, available_source, adapter):
        return source, ROUTE

    monkeypatch.setattr("gateway.session_envelope.check_native_route", check_native_route)
    authority = SessionAuthority(_runner(adapter), profile_id="default", instance_id="restart", db=db, epoch=epoch)
    scheduled = []
    authority._schedule = lambda ref: scheduled.append(ref.session_id)
    authority._publish_pending = lambda ref: None
    outcome = await authority.recover_native_sessions([(SID, source, adapter)])
    assert outcome == {SID: "unknown_execution"}
    return authority, outcome, scheduled


@pytest.mark.asyncio
async def test_disabled_by_default_leaves_the_chat_paused(monkeypatch, tmp_path):
    from gateway.dize_lost_turn_recovery import discard_lost_chat_turns
    monkeypatch.delenv("HERMES_DIZE_AUTO_DISCARD_LOST_TURNS", raising=False)
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = _Adapter()
        authority, outcome, _ = await _recover(monkeypatch, db, _crash_mid_turn(db), _source(), adapter)
        assert await discard_lost_chat_turns(authority.runner, authority, outcome) == {}
        assert [r["status"] for r in list_session_admissions(db, session_id=SID, pending_only=False)] == [
            "unknown", "queued"]
        assert adapter.sent == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_enabled_discards_the_lost_turn_resumes_followers_and_tells_the_topic(monkeypatch, tmp_path):
    from gateway.dize_lost_turn_recovery import NOTICE, discard_lost_chat_turns
    monkeypatch.setenv("HERMES_DIZE_AUTO_DISCARD_LOST_TURNS", "true")
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = _Adapter()
        authority, outcome, scheduled = await _recover(monkeypatch, db, _crash_mid_turn(db), _source(), adapter)
        assert await discard_lost_chat_turns(authority.runner, authority, outcome) == {SID: 1}
        rows = list_session_admissions(db, session_id=SID, pending_only=False)
        assert [r["status"] for r in rows] == ["terminal", "queued"]  # never replayed; follower runs
        assert scheduled == [SID]
        assert adapter.sent == [("chat", NOTICE, {"thread_id": "447", "reply_to": None})]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_enabled_leaves_non_chat_lost_work_to_the_operator(monkeypatch, tmp_path):
    from gateway.dize_lost_turn_recovery import discard_lost_chat_turns
    monkeypatch.setenv("HERMES_DIZE_AUTO_DISCARD_LOST_TURNS", "1")
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = _Adapter()
        authority, outcome, _ = await _recover(
            monkeypatch, db, _crash_mid_turn(db, principal="api:viewer"), _source(), adapter)
        assert await discard_lost_chat_turns(authority.runner, authority, outcome) == {}
        assert list_session_admissions(db, session_id=SID, pending_only=False)[0]["status"] == "unknown"
        assert adapter.sent == []
    finally:
        db.close()
