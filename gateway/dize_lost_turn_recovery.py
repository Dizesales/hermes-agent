"""Dize: resume a chat route whose turn was lost across an abrupt owner stop (opt-in).

Since 0.22 an owner restart fences a ``started`` admission ``unknown`` and pauses its FIFO until an
operator discards it (``hermes sessions discard``). A stop that kills the gateway mid-drain (the
hypervisor powering the VPS off) leaves every chat that was busy at that moment paused, and each
later message only gets the "conversation is paused" notice. Under 0.21 the lost turn was simply
gone and the next message ran.

With ``HERMES_DIZE_AUTO_DISCARD_LOST_TURNS`` set, startup recovery does exactly what the operator
does: acknowledge the lost turns (never replayed; their text stays in the transcript), let the
queued followers run, and tell the chat once so a person can ask to continue. Only messaging and
automation rows on a messaging route qualify; LOCAL/API sessions or anything else stays the
operator's.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

NOTICE = ("⚠️ O Hermes reiniciou no meio da tarefa anterior deste tópico e ela foi interrompida sem "
          "terminar. Nada foi refeito automaticamente. Mande \"continua\" para retomar.")
_CHAT_PRINCIPALS = ("messaging:", "automation:")


def enabled() -> bool:
    return os.getenv("HERMES_DIZE_AUTO_DISCARD_LOST_TURNS", "").strip().lower() in {"1", "true", "yes", "on"}


async def discard_lost_chat_turns(runner, authority, outcome: dict) -> dict:
    """Resolve the lost chat turns behind every ``unknown_execution`` startup verdict in ``outcome``.

    Returns ``{session_id: discarded_count}`` for the sessions whose queue was resumed."""
    if not enabled():
        return {}
    from gateway.config import Platform
    from gateway.session_contract import Principal, SessionRef
    from gateway.session_identity import replace_source
    from hermes_state_runtime import RuntimeStoreError, list_session_admissions
    actor = Principal("messaging-control", authority.profile_id,
                      frozenset({"session:submit", "session:control"}), "lost-turn")
    resumed = {}
    for sid, verdict in outcome.items():
        if verdict != "unknown_execution":
            continue
        live = authority.sessions.get(sid)
        source = getattr(live, "source", None)
        if source is None or source.platform in (Platform.LOCAL, Platform.API_SERVER):
            continue
        rows = await asyncio.to_thread(list_session_admissions, authority.db, session_id=sid, pending_only=False)
        lost = [row for row in rows if row["status"] == "unknown"]
        if not lost or not all(row["principal_id"].startswith(_CHAT_PRINCIPALS) for row in lost):
            continue
        ref = SessionRef(authority.profile_id, sid)
        try:
            for row in lost:
                await authority.resolve_unknown(actor, ref, row["admission_id"], row["generation"])
        except RuntimeStoreError as exc:
            logger.warning("Dize lost-turn recovery: %s stays paused (%s)", sid, exc)
            continue
        resumed[sid] = len(lost)
        logger.warning("Dize lost-turn recovery: discarded %d lost turn(s) on %s; its queue resumes",
                       len(lost), sid)
        adapter = runner._adapter_for_source(source)
        if adapter is None:
            continue
        try:
            # No reply anchor: the notice is about the topic, not a reply to its last message.
            bare = replace_source(source, message_id=None) if source.message_id else source
            await adapter.send(source.chat_id, NOTICE, metadata=runner._thread_metadata_for_source(bare))
        except Exception:
            logger.warning("Dize lost-turn recovery: notice to %s failed", sid, exc_info=True)
    return resumed


# ── Restart-interrupted turns (planned stop whose drain timed out) ─────────────────────────────
# 0.21 auto-resumed these through ``resume_pending``; since 0.22 a chat routed through the
# session-authority FIFO settles the cut turn ``interrupted`` and the legacy resume skips every
# route that has admissions (``_schedule_resume_pending_sessions``), so the topic just stops.
# ``HERMES_DIZE_RESUME_INTERRUPTED_TURNS=ask`` restores 0.21's interactive wording (report the
# restore, ask what next); ``continue`` uses the non-interactive one (finish the interrupted task
# without re-running tool calls already recorded). Either way it is ONE automation turn on the
# route's FIFO, admitted like a Kanban wake, never a replay of the cut input.
_SHUTDOWN_INTERRUPTS = frozenset({"Gateway shutting down", "Gateway restarting"})
RESUME_PRODUCER = "restart-resume"


def resume_mode() -> str:
    mode = os.getenv("HERMES_DIZE_RESUME_INTERRUPTED_TURNS", "").strip().lower()
    return mode if mode in {"ask", "continue"} else ""


def _cut_by_previous_owner(authority, rows) -> bool:
    """The session's newest admission is a chat turn the previous owner's shutdown interrupted."""
    from gateway.session_results import admission_result
    if not rows:
        return False
    last = rows[-1]
    if (last["status"] != "terminal" or last.get("outcome") != "interrupted"
            or last["owner_epoch"] != authority.epoch - 1
            or not last["principal_id"].startswith(_CHAT_PRINCIPALS)):
        return False
    saved = admission_result(authority.db, last["admission_id"]) or {}
    return (saved.get("result") or {}).get("interrupt_message") in _SHUTDOWN_INTERRUPTS


async def resume_restart_interrupted_turns(runner, authority) -> list:
    """Admit one resume turn per chat the previous owner's shutdown cut mid-turn; returns their ids."""
    mode = resume_mode()
    if not mode:
        return []
    from gateway.config import Platform
    from gateway.run import _auto_continue_freshness_window, _is_fresh_gateway_interruption, build_resume_recovery_note
    from gateway.session_automation import admit_route_wake
    from hermes_state_runtime import list_session_admissions
    text = build_resume_recovery_note("restart_timeout", "", interactive=mode == "ask")
    window = _auto_continue_freshness_window()
    resumed, seen = [], set()
    for entry in runner.session_store.list_sessions():
        source = entry.origin
        if source is None or source.platform in (Platform.LOCAL, Platform.API_SERVER):
            continue
        owner = authority.logical_owner(entry.session_id)
        if owner in seen:
            continue
        seen.add(owner)
        if not _is_fresh_gateway_interruption(entry.updated_at, window_secs=window):
            continue
        rows = await asyncio.to_thread(list_session_admissions, authority.db, session_id=owner, pending_only=False)
        if not _cut_by_previous_owner(authority, rows):
            continue
        adapter = runner._adapter_for_source(source)
        if adapter is None:
            continue
        try:
            if await admit_route_wake(runner, adapter, source, text, RESUME_PRODUCER):
                resumed.append(owner)
                logger.warning("Dize restart recovery: resuming %s (cut by the previous shutdown, mode=%s)",
                               owner, mode)
        except Exception:
            logger.warning("Dize restart recovery: could not resume %s", owner, exc_info=True)
    return resumed
