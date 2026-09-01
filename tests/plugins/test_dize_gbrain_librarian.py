from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PLUGIN = (
    Path(__file__).parents[2]
    / "plugins"
    / "dize-gbrain-librarian"
    / "__init__.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("dize_gbrain_librarian_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._PENDING.clear()
    return module


class _State:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _Ctx:
    def __init__(self, result=None, settings=None):
        self.result = result
        self.settings = settings or {}
        self.state = _State()
        self.calls = []
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def call_mcp(self, server, tool, arguments, timeout=30):
        self.calls.append((server, tool, arguments, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def test_recall_and_capture_review_are_injected_without_persisting_content(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    payload = {
        "results": [
            {
                "title": "Canonical decision",
                "evidence": "Use the owner source before derived surfaces.",
                "provenance": {"path": "decisions.md", "line": 10},
            }
        ]
    }
    ctx = _Ctx({"ok": True, "result": json.dumps(payload)})

    result = module._on_pre_llm_call(
        ctx,
        user_message="Qual é a decisão atual sobre a fonte owner?",
        conversation_history=[{"role": "user", "content": "question"}],
        session_id="session-secret",
        turn_id="turn-secret",
    )

    assert "Canonical decision" in result["context"]
    assert "mcp__gbrain__remember" in result["context"]
    assert ctx.calls[0][0:2] == ("gbrain", "recall")
    assert ctx.calls[0][2]["budget_tokens"] == 900
    assert ctx.calls[0][3] == 5.0
    serialized_state = json.dumps(ctx.state.values)
    assert "Qual é" not in serialized_state
    assert "session-secret" not in serialized_state
    assert ctx.state.values["metrics"]["recall_hits"] == 1


def test_recall_failure_is_fail_open_and_keeps_capture_review(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    ctx = _Ctx(RuntimeError("offline"))

    result = module._on_pre_llm_call(
        ctx,
        user_message="A partir de agora registre decisões duráveis.",
        conversation_history=[],
        session_id="s",
        turn_id="t",
    )

    assert "mcp__gbrain__remember" in result["context"]
    assert ctx.state.values["metrics"]["recall_failures"] == 1


def test_short_ack_and_kill_switch_skip_all_work(monkeypatch, tmp_path):
    module = _load_plugin()
    switch = tmp_path / "disabled"
    monkeypatch.setattr(module, "_kill_switch_path", lambda: switch)
    ctx = _Ctx({"ok": True, "result": "{}"})

    assert module._on_pre_llm_call(ctx, user_message="ok") is None
    switch.write_text("disabled", encoding="utf-8")
    assert module._on_pre_llm_call(ctx, user_message="Uma pergunta substantiva") is None
    assert ctx.calls == []


def test_post_hook_counts_observed_and_missed_capture_without_content(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    ctx = _Ctx({"ok": True, "result": "{\"results\": []}"})

    module._on_pre_llm_call(
        ctx,
        user_message="Decidimos que esta é a nova regra.",
        conversation_history=[{"role": "user", "content": "decision"}],
        session_id="s",
        turn_id="remembered",
    )
    module._on_post_llm_call(
        ctx,
        conversation_history=[
            {"role": "user", "content": "decision"},
            {"role": "assistant", "tool_calls": [{"name": "mcp__gbrain__remember"}]},
        ],
        session_id="s",
        turn_id="remembered",
    )
    module._on_pre_llm_call(
        ctx,
        user_message="A lição aprendida deve ser registrada.",
        conversation_history=[{"role": "user", "content": "lesson"}],
        session_id="s",
        turn_id="missed",
    )
    module._on_post_llm_call(
        ctx,
        conversation_history=[
            {"role": "user", "content": "lesson"},
            {"role": "assistant", "content": "done"},
        ],
        session_id="s",
        turn_id="missed",
    )

    metrics = ctx.state.values["metrics"]
    assert metrics["capture_signals"] == 2
    assert metrics["remember_observed"] == 1
    assert metrics["missed_capture_signals"] == 1
    assert "decision" not in json.dumps(ctx.state.values)
    assert "lesson" not in json.dumps(ctx.state.values)


def test_register_exposes_only_turn_hooks():
    module = _load_plugin()
    ctx = _Ctx()
    module.register(ctx)
    assert set(ctx.hooks) == {"pre_llm_call", "post_llm_call"}
