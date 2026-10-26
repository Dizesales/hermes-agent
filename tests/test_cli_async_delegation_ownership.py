"""Regression coverage for CLI async-delegation session isolation."""

from types import SimpleNamespace
from unittest.mock import Mock

from cli import HermesCLI


def _cli(session_id="current", agent_session_id=None, db=None):
    cli = HermesCLI.__new__(HermesCLI)
    cli.session_id = session_id
    cli.agent = SimpleNamespace(session_id=agent_session_id or session_id)
    cli._session_db = db
    return cli


def test_cli_owns_direct_parent_completion():
    cli = _cli("current")
    assert cli._owns_async_delegation_event({
        "type": "async_delegation",
        "session_key": "older-context-key",
        "parent_session_id": "current",
    }) is True


def test_cli_rejects_foreign_completion():
    db = Mock()
    db.is_compression_ancestor.return_value = False
    cli = _cli("current", db=db)
    assert cli._owns_async_delegation_event({
        "type": "async_delegation",
        "session_key": "foreign-origin",
        "parent_session_id": "foreign-parent",
    }) is False


def test_cli_accepts_precompression_completion_for_current_continuation():
    db = Mock()
    db.is_compression_ancestor.side_effect = (
        lambda ancestor_id, descendant_id: (
            ancestor_id == "old-parent" and descendant_id == "current-child"
        )
    )
    cli = _cli("current-child", db=db)
    assert cli._owns_async_delegation_event({
        "type": "async_delegation",
        "session_key": "old-parent",
        "parent_session_id": "old-parent",
    }) is True


def test_cli_missing_identity_and_db_errors_fail_closed():
    cli = _cli("current", db=None)
    assert cli._owns_async_delegation_event({"type": "async_delegation"}) is False

    db = Mock()
    db.is_compression_ancestor.side_effect = RuntimeError("db unavailable")
    cli = _cli("current", db=db)
    assert cli._owns_async_delegation_event({
        "type": "async_delegation",
        "session_key": "foreign",
    }) is False


def test_real_session_db_accepts_only_compression_lineage(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("compressed-parent", source="cli")
        db.end_session("compressed-parent", "compression")
        db.create_session(
            "current-child",
            source="cli",
            parent_session_id="compressed-parent",
        )

        db.create_session("branch-parent", source="cli")
        db.create_session(
            "branch-child",
            source="cli",
            parent_session_id="branch-parent",
        )

        assert db.is_compression_ancestor(
            "compressed-parent", "current-child"
        ) is True
        assert db.is_compression_ancestor("branch-parent", "branch-child") is False

        cli = _cli("current-child", db=db)
        assert cli._owns_async_delegation_event({
            "type": "async_delegation",
            "parent_session_id": "compressed-parent",
        }) is True
        assert cli._owns_async_delegation_event({
            "type": "async_delegation",
            "parent_session_id": "branch-parent",
        }) is False
    finally:
        db.close()