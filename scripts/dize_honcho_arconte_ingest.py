#!/usr/local/lib/hermes-agent/venv/bin/python
"""Bounded, redacted Hermes-to-Honcho ingestion for the Arconte.

The script never logs conversation content or raw platform identifiers. Dry-run
is the default; writes require both ``--apply`` and an enabled policy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_POLICY = Path("/etc/dizevolv/honcho-arconte/ingest-policy.json")
INTERNAL_TURN_RE = re.compile(
    r"^\s*(?:"
    r"\[ASYNC (?:DELEGATION )?(?:BATCH )?COMPLETE[^\]]*\]|"
    r"\[CONTEXT (?:COMPACTION|SUMMARY)[^\]]*\]|"
    r"\[PRIOR CONTEXT[^\]]*\]|"
    r"\[Your active task list was preserved across context compression\]|"
    r"\[IMPORTANT: Background process \d+ matched watch pattern[^\n]*|"
    r"A background (?:fan-out of \d+ subagent\(s\)|subagent) .* finished\."
    r")",
    re.IGNORECASE,
)

GOOGLE_OAUTH_CLIENT_ID_RE = re.compile(
    r"(?<![\w-])\d{6,}-[A-Za-z0-9_-]{8,}\.apps\.googleusercontent\.com(?![\w.-])",
    re.IGNORECASE,
)

_ERROR_STAGE = "startup"


def mark_stage(stage: str) -> None:
    global _ERROR_STAGE
    _ERROR_STAGE = stage


def redact_auth_identifiers(text: str) -> str:
    """Remove auth-client identifiers before relational-memory ingestion."""
    return GOOGLE_OAUTH_CLIENT_ID_RE.sub(
        "[REDACTED_GOOGLE_OAUTH_CLIENT_ID]", text
    )


def error_receipt(exc: BaseException) -> dict[str, Any]:
    """Return diagnostic metadata without exception text or message content."""
    receipt: dict[str, Any] = {
        "status": "error",
        "error_type": type(exc).__name__,
        "stage": _ERROR_STAGE,
    }
    sqlite_errorcode = getattr(exc, "sqlite_errorcode", None)
    sqlite_errorname = getattr(exc, "sqlite_errorname", None)
    if sqlite_errorcode is not None:
        receipt["sqlite_errorcode"] = sqlite_errorcode
    if sqlite_errorname is not None:
        receipt["sqlite_errorname"] = sqlite_errorname
    return receipt


@dataclass(frozen=True)
class Candidate:
    source_message_id: int
    source: str
    chat_type: str
    role: str
    session_id: str
    gateway_session_id: str
    peer_id: str
    source_ref: str
    content: str
    created_at: dt.datetime | None
    metadata: dict[str, Any]


def emit(**values: Any) -> None:
    """Emit sanitized machine-readable counters only."""
    print(json.dumps(values, sort_keys=True, separators=(",", ":")))


def load_policy(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("unsupported policy version")
    allowed = {str(v).lower() for v in data.get("allowed_sources", [])}
    denied = {str(v).lower() for v in data.get("denied_sources", [])}
    if not allowed or allowed & denied or "buzz" not in denied:
        raise ValueError("invalid source allow/deny policy")
    if data.get("history_order") != "newest-first":
        raise ValueError("unsupported history order")
    return data


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError("invalid client env line")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def key_bytes(value: str) -> bytes:
    if re.fullmatch(r"[0-9a-fA-F]{64,}", value):
        return bytes.fromhex(value)
    return value.encode("utf-8")


def digest(secret: bytes, value: str, length: int = 24) -> str:
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


def gateway_session_id(raw_key: str) -> str:
    """Mirror Hermes' gateway-session sanitization without disclosing the key."""
    from plugins.memory.honcho.client import HonchoClientConfig

    sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_key).strip("-")
    if not sanitized:
        raise ValueError("empty sanitized gateway session key")
    return HonchoClientConfig._enforce_session_id_limit(sanitized, raw_key)


def parse_origin(raw: str | None, source: str, chat_type: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        origin = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(origin, dict):
        return None
    if str(origin.get("platform", "")).lower() != source:
        return None
    if str(origin.get("chat_type", "")).lower() != chat_type:
        return None
    if not origin.get("chat_id"):
        return None
    if chat_type == "dm" and not origin.get("user_id"):
        return None
    return origin


def to_datetime(timestamp: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(timestamp), tz=dt.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def source_connection(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def ledger_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingested_messages (
          source_message_id INTEGER PRIMARY KEY,
          source TEXT NOT NULL,
          source_ref TEXT NOT NULL UNIQUE,
          honcho_message_id TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ingestion_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL,
          selected_count INTEGER NOT NULL DEFAULT 0,
          posted_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    os.chmod(path, 0o600)
    return con


def ingested_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        return {int(row[0]) for row in con.execute("SELECT source_message_id FROM ingested_messages")}
    finally:
        con.close()


def in_quiet_hours(policy: dict[str, Any], now: dt.datetime | None = None) -> bool:
    automation = policy.get("automation") or {}
    zone = ZoneInfo(str(automation.get("timezone", "UTC")))
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone)
    quiet = automation.get("quiet_hours") or {}
    start = dt.time.fromisoformat(str(quiet.get("start", "00:00")))
    end = dt.time.fromisoformat(str(quiet.get("end", "00:00")))
    current = local.time().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def today_run_count(ledger: sqlite3.Connection, policy: dict[str, Any]) -> int:
    zone = ZoneInfo(str((policy.get("automation") or {}).get("timezone", "UTC")))
    local_now = dt.datetime.now(dt.timezone.utc).astimezone(zone)
    local_start = dt.datetime.combine(local_now.date(), dt.time.min, zone)
    utc_start = local_start.astimezone(dt.timezone.utc).isoformat()
    row = ledger.execute(
        "SELECT COUNT(*) FROM ingestion_runs WHERE started_at >= ? AND status IN ('complete','partial')",
        (utc_start,),
    ).fetchone()
    return int(row[0]) if row else 0


def collect_candidates(
    policy: dict[str, Any], secret: bytes, limit: int
) -> tuple[list[Candidate], Counter[tuple[str, str, str]], Counter[str]]:
    allowed_sources = sorted({str(v).lower() for v in policy["allowed_sources"]})
    allowed_chats = sorted({str(v).lower() for v in policy["allowed_chat_types"]})
    allowed_roles = sorted({str(v).lower() for v in policy["allowed_roles"]})
    placeholders = lambda values: ",".join("?" for _ in values)
    query = f"""
      SELECT m.id AS message_id, m.role, m.content, m.timestamp, m.compacted,
             m.active, m.display_kind, s.id AS source_session_id, s.source,
             s.chat_type, s.session_key, s.chat_id, s.thread_id, s.origin_json
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
       WHERE lower(s.source) IN ({placeholders(allowed_sources)})
         AND lower(COALESCE(s.chat_type,'')) IN ({placeholders(allowed_chats)})
         AND lower(m.role) IN ({placeholders(allowed_roles)})
         AND trim(COALESCE(m.content,'')) <> ''
       ORDER BY COALESCE(m.timestamp, s.started_at, 0) DESC, m.id DESC
    """
    params = [*allowed_sources, *allowed_chats, *allowed_roles]
    mark_stage("read_ingest_ledger")
    already = ingested_ids(Path(policy["ledger_db"]))
    selected: list[Candidate] = []
    counts: Counter[tuple[str, str, str]] = Counter()
    rejected: Counter[str] = Counter()

    from agent.redact import redact_sensitive_text

    mark_stage("scan_source")
    with source_connection(Path(policy["source_db"])) as con:
        for row in con.execute(query, params):
            message_id = int(row["message_id"])
            if message_id in already:
                rejected["already_ingested"] += 1
                continue
            source = str(row["source"]).lower()
            chat_type = str(row["chat_type"]).lower()
            role = str(row["role"]).lower()
            if source in {str(v).lower() for v in policy.get("denied_sources", [])}:
                rejected["denied_source"] += 1
                continue
            if not policy.get("include_compacted") and bool(row["compacted"]):
                rejected["compacted"] += 1
                continue
            if row["active"] is not None and not bool(row["active"]):
                rejected["inactive"] += 1
                continue
            if not policy.get("include_hidden") and str(row["display_kind"] or "").lower() == "hidden":
                rejected["hidden"] += 1
                continue
            origin = parse_origin(row["origin_json"], source, chat_type)
            if policy.get("require_structured_origin") and origin is None:
                rejected["unstructured_origin"] += 1
                continue
            if origin is None:
                rejected["invalid_origin"] += 1
                continue
            original = str(row["content"])
            if INTERNAL_TURN_RE.match(original):
                rejected["internal_turn"] += 1
                continue
            content = redact_sensitive_text(
                original,
                force=bool(policy.get("redact_secrets", True)),
                redact_url_credentials=True,
            ).strip()
            content = redact_auth_identifiers(content)
            if not content:
                rejected["empty_after_redaction"] += 1
                continue
            max_chars = int(policy["max_chars_per_message"])
            truncated = len(content) > max_chars
            if truncated:
                content = content[:max_chars]

            source_session = str(row["source_session_id"])
            session_basis = str(row["session_key"] or source_session)
            honcho_session = f"{source}-{digest(secret, 'session:'+source+':'+session_basis)}"
            honcho_gateway_session = gateway_session_id(session_basis)
            if role == "assistant":
                peer_id = str(policy["assistant_peer"])
                peer_kind = "assistant"
            elif chat_type == "dm":
                peer_id = f"person-{digest(secret, 'person:'+source+':'+str(origin['user_id']))}"
                peer_kind = "person"
            else:
                group_basis = f"{origin.get('chat_id')}:{origin.get('thread_id') or ''}"
                peer_id = f"group-{digest(secret, 'group:'+source+':'+group_basis)}"
                peer_kind = "group"

            source_ref = digest(secret, f"message:{source}:{message_id}", length=32)
            metadata = {
                "ingest_version": 1,
                "source": source,
                "chat_type": chat_type,
                "source_role": role,
                "source_ref": source_ref,
                "peer_kind": peer_kind,
                "redacted": content != original.strip(),
                "truncated": truncated,
            }
            selected.append(
                Candidate(
                    source_message_id=message_id,
                    source=source,
                    chat_type=chat_type,
                    role=role,
                    session_id=honcho_session,
                    gateway_session_id=honcho_gateway_session,
                    peer_id=peer_id,
                    source_ref=source_ref,
                    content=content,
                    created_at=to_datetime(row["timestamp"]),
                    metadata=metadata,
                )
            )
            counts[(source, chat_type, role)] += 1
            if len(selected) >= limit:
                break
    return selected, counts, rejected


def remote_existing(session: Any, source_ref: str) -> Any | None:
    page = session.messages(filters={"metadata": {"source_ref": source_ref}}, page=1, size=2)
    # SyncPage iteration auto-fetches all pages; inspect this bounded page only.
    matches = page.items
    if len(matches) > 1:
        raise RuntimeError("ambiguous remote message acknowledgement")
    return matches[0] if matches else None


def verify_remote_message(message: Any, candidate: Candidate, workspace: str) -> None:
    """Confirm remote bytes and identity before persisting an acknowledgement."""
    metadata = getattr(message, "metadata", None)
    if (
        not isinstance(getattr(message, "id", None), str)
        or not message.id
        or getattr(message, "content", None) != candidate.content
        or getattr(message, "peer_id", None) != candidate.peer_id
        or getattr(message, "session_id", None) != candidate.session_id
        or getattr(message, "workspace_id", None) != workspace
        or not isinstance(metadata, dict)
        or metadata.get("source_ref") != candidate.source_ref
        or any(metadata.get(key) != value for key, value in candidate.metadata.items())
    ):
        raise RuntimeError("remote message acknowledgement mismatch")


def apply_candidates(policy: dict[str, Any], candidates: list[Candidate]) -> dict[str, int]:
    env = load_env(Path(policy["client_env"]))
    token = env.get("HONCHO_API_KEY", "")
    if not token:
        raise RuntimeError("HONCHO_API_KEY missing from client env")

    from honcho import Honcho
    from honcho.api_types import (
        DreamConfiguration,
        MessageCreateParams,
        PeerCardConfiguration,
        PeerConfig,
        ReasoningConfiguration,
        SessionConfiguration,
        SessionPeerConfig,
        SummaryConfiguration,
        WorkspaceConfiguration,
    )

    client = Honcho(
        api_key=token,
        base_url=str(policy["honcho_base_url"]),
        workspace_id=str(policy["workspace"]),
        timeout=20,
        max_retries=2,
    )
    workspace_config = WorkspaceConfiguration(
        reasoning=ReasoningConfiguration(enabled=True),
        peer_card=PeerCardConfiguration(use=True, create=True),
        summary=SummaryConfiguration(
            enabled=True,
            messages_per_short_summary=20,
            messages_per_long_summary=60,
        ),
        dream=DreamConfiguration(enabled=True),
    )
    client.set_configuration(workspace_config)
    status_before = client.queue_status()
    backlog_before = status_before.pending_work_units + status_before.in_progress_work_units
    if backlog_before >= int(policy["max_queue_backlog"]):
        return {
            "posted": 0,
            "deduplicated_remote": 0,
            "backlog_before": backlog_before,
            "backlog_after": backlog_before,
            "backpressure": 1,
        }

    assistant_id = str(policy["assistant_peer"])
    assistant = client.peer(
        assistant_id,
        metadata={"kind": "assistant", "managed_by": "dizevolv-ingestor-v1"},
        configuration=PeerConfig(observe_me=False),
    )
    by_session: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_session[candidate.session_id].append(candidate)

    posted = 0
    deduplicated = 0
    ledger = ledger_connection(Path(policy["ledger_db"]))
    try:
        for session_id, session_candidates in by_session.items():
            observed_ids = sorted({c.peer_id for c in session_candidates if c.peer_id != assistant_id})
            observed_peers = [
                client.peer(
                    peer_id,
                    metadata={
                        "kind": "group" if peer_id.startswith("group-") else "person",
                        "managed_by": "dizevolv-ingestor-v1",
                    },
                    configuration=PeerConfig(observe_me=True),
                )
                for peer_id in observed_ids
            ]
            peer_specs: list[Any] = [
                (assistant, SessionPeerConfig(observe_me=False, observe_others=True))
            ]
            peer_specs.extend(
                (peer, SessionPeerConfig(observe_me=True, observe_others=False))
                for peer in observed_peers
            )
            session = client.session(
                session_id,
                metadata={
                    "source": session_candidates[0].source,
                    "chat_type": session_candidates[0].chat_type,
                    "managed_by": "dizevolv-ingestor-v1",
                },
                configuration=SessionConfiguration.model_validate(workspace_config.model_dump()),
                peers=peer_specs,
            )
            # Read-only Hermes resolves the live gateway key, while historical
            # messages live under a pseudonymous session ID.  A message-free
            # gateway alias session joins the same peers so honcho_context can
            # retrieve their standing representation without exposing the raw
            # gateway key to the LLM processing queue.
            gateway_ids = {c.gateway_session_id for c in session_candidates}
            if len(gateway_ids) != 1:
                raise RuntimeError("gateway session alias cardinality mismatch")
            gateway_id = next(iter(gateway_ids))
            if gateway_id != session_id:
                client.session(
                    gateway_id,
                    metadata={
                        "alias_only": True,
                        "source": session_candidates[0].source,
                        "chat_type": session_candidates[0].chat_type,
                        "managed_by": "dizevolv-ingestor-v1",
                    },
                    configuration=SessionConfiguration.model_validate(workspace_config.model_dump()),
                    peers=peer_specs,
                )

            to_post: list[Candidate] = []
            for candidate in sorted(
                session_candidates,
                key=lambda c: (c.created_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc), c.source_message_id),
            ):
                existing = remote_existing(session, candidate.source_ref)
                if existing is None:
                    to_post.append(candidate)
                    continue
                verify_remote_message(existing, candidate, str(policy["workspace"]))
                ledger.execute(
                    "INSERT OR REPLACE INTO ingested_messages VALUES (?,?,?,?,?,?)",
                    (
                        candidate.source_message_id,
                        candidate.source,
                        candidate.source_ref,
                        existing.id,
                        hashlib.sha256(candidate.content.encode("utf-8")).hexdigest(),
                        dt.datetime.now(dt.timezone.utc).isoformat(),
                    ),
                )
                ledger.commit()
                deduplicated += 1

            if not to_post:
                continue
            created = session.add_messages(
                [
                    MessageCreateParams(
                        content=c.content,
                        peer_id=c.peer_id,
                        metadata=c.metadata,
                        created_at=c.created_at,
                    )
                    for c in to_post
                ]
            )
            if len(created) != len(to_post):
                raise RuntimeError("Honcho batch response cardinality mismatch")
            # Validate the entire returned batch before acknowledging any member.
            for candidate, message in zip(to_post, created, strict=True):
                verify_remote_message(message, candidate, str(policy["workspace"]))
            if len({message.id for message in created}) != len(created):
                raise RuntimeError("duplicate remote message acknowledgement")
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            ledger.executemany(
                "INSERT OR REPLACE INTO ingested_messages VALUES (?,?,?,?,?,?)",
                [
                    (
                        candidate.source_message_id,
                        candidate.source,
                        candidate.source_ref,
                        message.id,
                        hashlib.sha256(candidate.content.encode("utf-8")).hexdigest(),
                        now,
                    )
                    for candidate, message in zip(to_post, created, strict=True)
                ],
            )
            ledger.commit()
            posted += len(created)
    finally:
        ledger.close()

    status_after = client.queue_status()
    backlog_after = status_after.pending_work_units + status_after.in_progress_work_units
    return {
        "posted": posted,
        "deduplicated_remote": deduplicated,
        "backlog_before": backlog_before,
        "backlog_after": backlog_after,
        "backpressure": 0,
    }


def main() -> int:
    mark_stage("parse_arguments")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the bounded batch")
    mode.add_argument("--dry-run", action="store_true", help="inspect counts only (default)")
    parser.add_argument("--limit", type=int, help="lower the policy batch cap")
    parser.add_argument("--ignore-quiet-hours", action="store_true", help="manual canary only")
    args = parser.parse_args()

    mark_stage("load_policy")
    policy = load_policy(args.policy)
    configured_limit = int(policy["max_messages_per_run"])
    limit = configured_limit if args.limit is None else args.limit
    if limit < 1 or limit > configured_limit:
        raise ValueError("limit must be within the configured batch cap")

    apply_mode = bool(args.apply)
    if apply_mode and not policy.get("enabled"):
        raise RuntimeError("ingestion policy is disabled")
    if apply_mode and not args.ignore_quiet_hours and in_quiet_hours(policy):
        emit(status="quiet_hours", selected=0, posted=0)
        return 0

    mark_stage("load_client_env")
    env: dict[str, str] = {}
    client_env = Path(policy["client_env"])
    if client_env.exists():
        env = load_env(client_env)
    hmac_value = env.get("HONCHO_IDENTITY_HMAC_KEY", "")
    if apply_mode and not hmac_value:
        raise RuntimeError("HONCHO_IDENTITY_HMAC_KEY missing from client env")
    secret = key_bytes(hmac_value) if hmac_value else b"dry-run-nonpersistent-identity-key"
    mark_stage("collect_source")
    candidates, counts, rejected = collect_candidates(policy, secret, limit)
    count_view = {
        "/".join(key): value for key, value in sorted(counts.items())
    }

    if not apply_mode:
        emit(
            status="dry_run",
            selected=len(candidates),
            counts=count_view,
            rejected=dict(sorted(rejected.items())),
            configured_sources=sorted(policy["allowed_sources"]),
            denied_sources=sorted(policy["denied_sources"]),
        )
        return 0

    mark_stage("open_run_ledger")
    ledger = ledger_connection(Path(policy["ledger_db"]))
    automation = policy.get("automation") or {}
    max_runs = int(automation.get("max_runs_per_day", 72))
    if today_run_count(ledger, policy) >= max_runs:
        ledger.close()
        emit(status="daily_run_cap", selected=0, posted=0)
        return 0
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    cursor = ledger.execute(
        "INSERT INTO ingestion_runs(started_at,status,selected_count) VALUES (?,?,?)",
        (started, "running", len(candidates)),
    )
    run_id = int(cursor.lastrowid)
    ledger.commit()
    ledger.close()

    try:
        mark_stage("apply_remote_batch")
        result = apply_candidates(policy, candidates) if candidates else {
            "posted": 0,
            "deduplicated_remote": 0,
            "backlog_before": 0,
            "backlog_after": 0,
            "backpressure": 0,
        }
        final_status = "backpressure" if result["backpressure"] else "complete"
        mark_stage("finalize_run_ledger")
        ledger = ledger_connection(Path(policy["ledger_db"]))
        ledger.execute(
            "UPDATE ingestion_runs SET completed_at=?,status=?,posted_count=? WHERE id=?",
            (dt.datetime.now(dt.timezone.utc).isoformat(), final_status, result["posted"], run_id),
        )
        ledger.commit()
        ledger.close()
        emit(
            status=final_status,
            selected=len(candidates),
            counts=count_view,
            **result,
        )
        return 0
    except Exception:
        failed_stage = _ERROR_STAGE
        mark_stage("record_failed_run")
        ledger = ledger_connection(Path(policy["ledger_db"]))
        ledger.execute(
            "UPDATE ingestion_runs SET completed_at=?,status='failed' WHERE id=?",
            (dt.datetime.now(dt.timezone.utc).isoformat(), run_id),
        )
        ledger.commit()
        ledger.close()
        mark_stage(failed_stage)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit(**error_receipt(exc))
        raise SystemExit(1)
