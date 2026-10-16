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
