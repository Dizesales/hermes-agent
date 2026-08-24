"""Behavioral tests for Honcho's explicit read-only safety boundary."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.memory_manager import MemoryManager
from plugins.memory.honcho import (
    HonchoMemoryProvider,
    _redact_honcho_auth_identifiers,
)
from plugins.memory.honcho.client import HonchoClientConfig
from plugins.memory.honcho.session import HonchoSessionManager


def _config(
    *,
    ai_peer: str = "hermes",
    peer_name: str | None = None,
) -> HonchoClientConfig:
    return HonchoClientConfig(
        enabled=True,
        api_key="test-key",
        read_only=True,
        # Deliberately unsafe values prove readOnly overrides them.
        recall_mode="hybrid",
        save_messages=True,
        query_rewrite=True,
        init_on_session_start=True,
        ai_peer=ai_peer,
        peer_name=peer_name,
    )


def _initialized_provider():
    cfg = _config()
    provider = HonchoMemoryProvider(query_rewriter=MagicMock())
    manager = MagicMock()

    with (
        patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=cfg,
        ),
        patch(
            "plugins.memory.honcho.client.get_honcho_client",
            return_value=MagicMock(),
        ),
        patch(
            "plugins.memory.honcho.session.HonchoSessionManager",
            return_value=manager,
        ) as manager_cls,
    ):
        provider.initialize("readonly-test", platform="cli")

    return provider, manager, manager_cls


class TestReadOnlyToolBoundary:
    def test_auth_identifier_redactor_handles_nested_case_and_near_miss(self):
        client_id = (
            "123456789012-abcdefghijklmnopqrstuvwxyz.apps.googleusercontent.com"
        )
        nested = {
            "values": [
                f"({client_id}) and {client_id.upper()}",
                "service.apps.googleusercontent.com",
                "123-short.apps.googleusercontent.com",
            ]
        }

        result = _redact_honcho_auth_identifiers(nested)

        assert client_id not in result["values"][0].lower()
        assert result["values"][0].count(
            "[REDACTED_GOOGLE_OAUTH_CLIENT_ID]"
        ) == 2
        assert result["values"][1:] == nested["values"][1:]

    def test_is_available_hydrates_router_before_initialize(self):
        provider = HonchoMemoryProvider()
        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=_config(),
        ):
            assert provider.is_available() is True

        memory = MemoryManager()
        memory.add_provider(provider)

        assert memory.get_all_tool_names() == {
            "honcho_profile",
            "honcho_search",
            "honcho_context",
        }
        assert not memory.has_tool("honcho_reasoning")
        assert not memory.has_tool("honcho_conclude")

    def test_surface_is_exact_and_profile_has_no_write_parameter(self):
        provider, _manager, _manager_cls = _initialized_provider()

        schemas = provider.get_tool_schemas()

        assert [schema["name"] for schema in schemas] == [
            "honcho_profile",
            "honcho_search",
            "honcho_context",
        ]
        assert "card" not in schemas[0]["parameters"]["properties"]
        assert "reasoning" not in " ".join(
            schema["description"] for schema in schemas
        ).lower()

    def test_stale_write_and_reasoning_calls_fail_before_session_init(self):
        provider = HonchoMemoryProvider()
        provider._apply_config_mode(_config())
        provider._lazy_init_kwargs = {"platform": "cli"}
        provider._lazy_init_session_id = "must-not-init"

        conclude = provider.handle_tool_call(
            "honcho_conclude", {"conclusion": "write me"}
        )
        reasoning = provider.handle_tool_call(
            "honcho_reasoning", {"query": "synthesize"}
        )
        card = provider.handle_tool_call("honcho_profile", {"card": []})

        assert "disabled in read-only mode" in conclude
        assert "disabled in read-only mode" in reasoning
        assert "updates are disabled in read-only mode" in card
        assert provider._manager is None

    def test_three_allowed_tools_still_dispatch_reads(self):
        provider = HonchoMemoryProvider()
        provider._apply_config_mode(_config())
        provider._manager = MagicMock()
        provider._session_initialized = True
        provider._session_key = "readonly-test"
        provider._manager.get_peer_card.return_value = ["stored fact"]
        provider._manager.search_context.return_value = "stored excerpt"
        provider._manager.get_session_context.return_value = {
            "summary": "stored summary"
        }

        profile = json.loads(provider.handle_tool_call("honcho_profile", {}))
        search = json.loads(
            provider.handle_tool_call("honcho_search", {"query": "fact"})
        )
        context = json.loads(provider.handle_tool_call("honcho_context", {}))

        assert profile["result"] == ["stored fact"]
        assert search["result"] == "stored excerpt"
        assert "stored summary" in context["result"]

    def test_lookup_tools_redact_google_oauth_client_ids(self):
        provider = HonchoMemoryProvider()
        provider._apply_config_mode(_config())
        provider._manager = MagicMock()
        provider._session_initialized = True
        provider._session_key = "readonly-test"
        client_id = (
            "123456789012-abcdefghijklmnopqrstuvwxyz.apps.googleusercontent.com"
        )
        provider._manager.get_peer_card.return_value = [f"client {client_id}"]
        provider._manager.search_context.return_value = f"found {client_id}"
        provider._manager.get_session_context.return_value = {
            "summary": f"summary {client_id}",
            "representation": f"representation {client_id}",
            "recent_messages": [{"role": "user", "content": client_id}],
        }

        profile = provider.handle_tool_call("honcho_profile", {})
        search = provider.handle_tool_call(
            "honcho_search", {"query": "oauth client"}
        )
        context = provider.handle_tool_call("honcho_context", {})

        for result in (profile, search, context):
            assert client_id not in result
            assert "[REDACTED_GOOGLE_OAUTH_CLIENT_ID]" in result

    def test_lookup_tool_errors_redact_google_oauth_client_ids(self):
        provider = HonchoMemoryProvider()
        provider._apply_config_mode(_config())
        provider._manager = MagicMock()
        provider._session_initialized = True
        provider._session_key = "readonly-test"
        client_id = (
            "123456789012-abcdefghijklmnopqrstuvwxyz.apps.googleusercontent.com"
        )
        provider._manager.search_context.side_effect = RuntimeError(client_id)

        result = provider.handle_tool_call(
            "honcho_search", {"query": "oauth client"}
        )

        assert client_id not in result
        assert "[REDACTED_GOOGLE_OAUTH_CLIENT_ID]" in result

    def test_gateway_cache_signature_changes_with_guard(self, tmp_path, monkeypatch):
        from gateway.run import GatewayRunner

        config_path = tmp_path / "honcho.json"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        GatewayRunner._HONCHO_CACHE_BUSTING_MEMO = {}

        config_path.write_text(json.dumps({"apiKey": "key", "readOnly": True}))
        guarded = GatewayRunner._extract_cache_busting_config(
            {"memory": {"provider": "honcho"}}
        )
        config_path.write_text(json.dumps({"apiKey": "key", "readOnly": False}))
        writable = GatewayRunner._extract_cache_busting_config(
            {"memory": {"provider": "honcho"}}
        )

        assert guarded["honcho.read_only"] is True
        assert writable["honcho.read_only"] is False


class TestReadOnlyLifecycle:
    def test_initialize_uses_local_handles_and_skips_migration(self):
        provider, manager, manager_cls = _initialized_provider()

        assert provider._read_only is True
        assert provider._recall_mode == "tools"
        assert provider._query_rewrite_enabled is False
        assert provider._session_initialized is True
        assert manager_cls.call_args.kwargs["read_only"] is True
        manager.open_read_only.assert_called_once_with(provider._session_key)
        manager.get_or_create.assert_not_called()
        manager.migrate_memory_files.assert_not_called()
        manager.dialectic_query.assert_not_called()

    def test_provider_instance_cannot_downgrade_after_guarded_discovery(self):
        provider = HonchoMemoryProvider()
        guarded = _config()
        writable = HonchoClientConfig(
            enabled=True,
            api_key="test-key",
            read_only=False,
            recall_mode="hybrid",
            init_on_session_start=True,
        )
        manager = MagicMock()

        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=guarded,
        ):
            assert provider.is_available() is True

        with (
            patch(
                "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
                return_value=writable,
            ),
            patch(
                "plugins.memory.honcho.client.get_honcho_client",
                return_value=MagicMock(),
            ),
            patch(
                "plugins.memory.honcho.session.HonchoSessionManager",
                return_value=manager,
            ) as manager_cls,
        ):
            provider.initialize("readonly-test", platform="cli")

        assert provider._read_only is True
        assert provider._recall_mode == "tools"
        assert manager_cls.call_args.kwargs["read_only"] is True
        manager.open_read_only.assert_called_once_with(provider._session_key)
        manager.get_or_create.assert_not_called()
        assert [schema["name"] for schema in provider.get_tool_schemas()] == [
            "honcho_profile",
            "honcho_search",
            "honcho_context",
        ]

    def test_prompt_and_prefetch_cannot_trigger_automatic_recall(self):
        provider, manager, _manager_cls = _initialized_provider()

        prompt = provider.system_prompt_block()
        assert "read-only mode" in prompt
        assert "cannot create resources" in prompt
        assert "honcho_reasoning" not in prompt
        assert "honcho_conclude" not in prompt

        assert provider.prefetch("what do you know?") == ""
        provider.queue_prefetch("what do you know?")
        assert provider._run_dialectic_depth("what do you know?") == ""
        manager.prefetch_context.assert_not_called()
        manager.dialectic_query.assert_not_called()

    def test_all_write_lifecycle_hooks_are_noops(self):
        provider, manager, _manager_cls = _initialized_provider()

        provider.sync_turn("hello", "hi")
        provider.on_memory_write("add", "user", "durable fact")
        provider.on_session_end([])
        provider.shutdown()

        manager.get_or_create.assert_not_called()
        manager.save.assert_not_called()
        manager.create_conclusion.assert_not_called()
        manager.flush_all.assert_not_called()
        manager.shutdown.assert_not_called()

    def test_cli_peer_setup_respects_guard(self):
        from plugins.memory.honcho import cli as honcho_cli

        with (
            patch(
                "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
                return_value=_config(),
            ),
            patch(
                "plugins.memory.honcho.client.get_honcho_client"
            ) as get_client,
        ):
            assert honcho_cli._ensure_peer_exists("hermes") is False

        get_client.assert_not_called()


class TestReadOnlySessionHandles:
    def _manager(self):
        source = MagicMock(name="source_honcho")
        read_client = MagicMock(name="read_only_honcho")
        source.model_copy.return_value = read_client
        manager = HonchoSessionManager(
            honcho=source,
            config=_config(ai_peer="arconte", peer_name="lucas"),
            read_only=True,
        )
        return manager, source, read_client

    def test_open_read_only_constructs_only_local_sdk_handles(self):
        manager, source, read_client = self._manager()
        session_cls = MagicMock(name="Session")
        peer_cls = MagicMock(name="Peer")
        fake_honcho = SimpleNamespace(Session=session_cls, Peer=peer_cls)

        with (
            patch.dict(sys.modules, {"honcho": fake_honcho}),
            patch(
                "plugins.memory.honcho.session.get_honcho_client",
                return_value=source,
            ),
        ):
            session = manager.open_read_only("gateway:42")
            peer = manager._get_or_create_peer(session.user_peer_id)
            same = manager.get_or_create("gateway:42")

        assert same is session
        assert session.user_peer_id == "lucas"
        assert session.assistant_peer_id == "arconte"
        session_cls.assert_called_once_with("gateway-42", read_client)
        peer_cls.assert_called_once_with("lucas", read_client)
        source.model_copy.assert_called_once_with(deep=False)
        source.session.assert_not_called()
        source.peer.assert_not_called()
        read_client.session.assert_not_called()
        read_client.peer.assert_not_called()
        session_cls.return_value.add_peers.assert_not_called()
        session_cls.return_value.context.assert_not_called()
        assert read_client._workspace_ensured is True
        assert manager._async_queue is None

    def test_pinned_honcho_sdk_builds_handles_without_http(self):
        """Exercise the real honcho-ai 2.2.0 object shapes without network."""
        from honcho import Honcho

        source = Honcho(
            api_key="test-key",
            base_url="http://127.0.0.1:9",
            workspace_id="existing-workspace",
        )
        source._http = MagicMock(name="http_transport")
        manager = HonchoSessionManager(
            honcho=source,
            config=_config(ai_peer="arconte", peer_name="lucas"),
            read_only=True,
        )

        with patch(
            "plugins.memory.honcho.session.get_honcho_client",
            return_value=source,
        ):
            session = manager.open_read_only("existing-session")
            peer = manager._get_or_create_peer("lucas")

        assert session.honcho_session_id == "existing-session"
        assert peer.id == "lucas"
        assert source._workspace_ensured is False
        assert manager.honcho._workspace_ensured is True
        assert source._http.mock_calls == []

    def test_low_level_writers_and_reasoning_fail_closed(self):
        manager, source, _read_client = self._manager()
        session = SimpleNamespace(messages=[])

        assert manager._flush_session(session) is False
        assert manager.save(session) is None
        assert manager.flush_all() is None
        assert manager.migrate_local_history("missing", []) is False
        assert manager.migrate_memory_files("missing", "/missing") is False
        assert manager.create_conclusion("missing", "fact") is False
        assert manager.delete_conclusion("missing", "id") is False
        assert manager.set_peer_card("missing", ["fact"]) is None
        assert manager.seed_ai_identity("missing", "identity") is False
        assert manager.dialectic_query("missing", "synthesize") == ""
        with pytest.raises(RuntimeError, match="disabled in read-only mode"):
            manager._get_or_create_honcho_session(
                "missing",
                MagicMock(name="user_peer"),
                MagicMock(name="assistant_peer"),
            )
        source.assert_not_called()
