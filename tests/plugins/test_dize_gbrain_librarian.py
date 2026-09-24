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
                "chunk": "Use the owner source before derived surfaces.",
                "evidence": "keyword_exact",
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


def test_real_recall_categories_never_replace_the_passage(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    claim = "Reopen the current owner document before changing operating policy."
    for category in ("keyword_exact", "weak_semantic", "new_match_category"):
        payload = {"protocol_version": 1, "facts": [{"fact": "unrelated fact"}],
                   "results": [{"title": "Owner policy", "chunk": claim,
                                "evidence": category, "provenance": "canonical/owner/policy"}]}
        for envelope in ({"ok": True, "structuredContent": payload},
                         {"ok": True, "result": json.dumps(payload)}):
            ctx = _Ctx(envelope)
            result = module._on_pre_llm_call(ctx, user_message="Which policy governs the change?")
            assert claim in result["context"]
            assert "canonical/owner/policy" in result["context"]
            assert category not in result["context"]
            assert "unrelated fact" not in result["context"]
            assert "not authorization" in result["context"]
            assert "mcp__gbrain__remember" in result["context"]
            assert claim not in json.dumps(ctx.state.values)


def test_metadata_only_or_malformed_rows_cannot_consume_hits_or_result_slots(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    payload = {"results": [
        {"title": "Metadata only", "evidence": "keyword_exact"},
        {"chunk": {"unexpected": "object"}, "evidence": "weak_semantic"},
        {"chunk": "   ", "text": "Valid fallback passage", "provenance": "canonical/owner/rule"},
    ]}
    ctx = _Ctx({"ok": True, "structuredContent": payload}, {"max_results": 1})
    result = module._on_pre_llm_call(ctx, user_message="Read the relevant current rule.")
    assert "Valid fallback passage" in result["context"]
    assert "Metadata only" not in result["context"]
    assert ctx.state.values["metrics"]["recall_hits"] == 1
    empty = _Ctx({"ok": True, "structuredContent": {"results": payload["results"][:2]}})
    empty_result = module._on_pre_llm_call(empty, user_message="Read the relevant current rule.")
    assert "Memory 1" not in empty_result["context"]
    assert empty.state.values["metrics"]["recall_hits"] == 0
    assert "mcp__gbrain__remember" in empty_result["context"]


def test_passage_remains_bounded_without_losing_provenance():
    module = _load_plugin()
    rendered = module._format_context(
        [{"chunk": "x" * 20000, "evidence": "keyword_exact", "provenance": "canonical/owner/rule"}],
        max_results=3, max_chars=6000, capture=True,
    )
    assert "x" * 1200 in rendered
    assert "x" * 1201 not in rendered
    assert "canonical/owner/rule" in rendered
    assert "mcp__gbrain__remember" in rendered
    assert len(rendered) <= 6000


def test_stale_index_blocks_before_mcp(monkeypatch, tmp_path):
    from types import SimpleNamespace
    module = _load_plugin()
    monkeypatch.setattr(module, '_kill_switch_path', lambda: tmp_path/'disabled')
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=20, stdout='{"status":"HOLD"}')), raising=False)
    ctx = _Ctx({'ok': True, 'structuredContent': {'results': [{'chunk': 'STALE_CONTENT'}]}},
               {'freshness_policy': '/owner/policy.json', 'freshness_receipts': '/owner/latest'})
    result = module._on_pre_llm_call(ctx, user_message='Qual e a decisao operacional atual?')
    assert ctx.calls == []
    assert 'STALE_CONTENT' not in result['context']
    assert 'canonical' in result['context']


def test_index_generation_change_drops_inflight_passages(monkeypatch, tmp_path):
    from types import SimpleNamespace
    module = _load_plugin()
    monkeypatch.setattr(module, '_kill_switch_path', lambda: tmp_path/'disabled')
    values = iter(['a'*64, 'b'*64])
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'status':'CURRENT', 'generation':next(values)}))), raising=False)
    ctx = _Ctx({'ok': True, 'structuredContent': {'results': [{'chunk': 'INFLIGHT_OLD'}]}},
               {'freshness_policy': '/owner/policy.json', 'freshness_receipts': '/owner/latest'})
    result = module._on_pre_llm_call(ctx, user_message='Qual e a decisao operacional atual?')
    assert len(ctx.calls) == 1
    assert 'INFLIGHT_OLD' not in result['context']


def test_current_generation_preserves_recall(monkeypatch, tmp_path):
    from types import SimpleNamespace
    module = _load_plugin(); calls=[]
    monkeypatch.setattr(module, '_kill_switch_path', lambda: tmp_path/'disabled')
    def current(*args, **kwargs):
        calls.append(args); return SimpleNamespace(returncode=0, stdout=json.dumps({'status':'CURRENT', 'generation':'a'*64}))
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=current), raising=False)
    ctx = _Ctx({'ok': True, 'structuredContent': {'results': [{'chunk': 'CURRENT_CONTENT'}]}},
               {'freshness_policy': '/owner/policy.json', 'freshness_receipts': '/owner/latest'})
    result = module._on_pre_llm_call(ctx, user_message='Qual e a decisao operacional atual?')
    assert 'CURRENT_CONTENT' in result['context'] and len(calls) == 2


def test_compact_recall_is_opt_in_and_keeps_the_existing_budget(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, "_kill_switch_path", lambda: tmp_path / "disabled")
    for enabled in (False, True):
        ctx = _Ctx({"ok": True, "structuredContent": {"results": []}},
                   {"compact_recall": enabled, "budget_tokens": 900})
        module._on_pre_llm_call(ctx, user_message="Onde registrar uma decisao duravel?")
        args = ctx.calls[0][2]
        assert args["budget_tokens"] == 900 and args["limit"] == 3
        if enabled:
            assert args["preserve_lexical"] is True and args["snippet_chars"] == 1200
        else:
            assert "preserve_lexical" not in args and "snippet_chars" not in args


def _reference_fixture(module, tmp_path, entries=None, page=None):
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'source_repo': '/owner', 'entries': entries or [
        {'source': 'context/MEMORY.md', 'target': 'canonical/context/MEMORY.md'}]}))
    payload = page or {'results': [{'slug': 'canonical/context/memory', 'chunk': 'Decisions: decisions.md; lessons: lessons.md.'}]}
    return _Ctx({'ok': True, 'structuredContent': payload}, {
        'canonical_references': True, 'freshness_policy': str(policy), 'capture_review': False})


def test_explicit_reference_case_and_owner_path(tmp_path):
    module = _load_plugin()
    for query in ['Onde ficam as decisoes? Consulte MEMORY.md.', 'Explique `memory.md`.', 'Consulte /owner/context/MEMORY.md.', 'Consulte context/MEMORY.md.']:
        ctx = _reference_fixture(module, tmp_path)
        result = module._reference_recall(ctx, query, 900, 5)
        assert 'decisions.md' in result['structuredContent']['results'][0]['chunk']
        assert ctx.calls[0][1] == 'recall'
        assert ctx.calls[0][2]['query'] == 'canonical/context/memory'


def test_reference_unknown_path_and_normal_query_keep_search(tmp_path):
    module = _load_plugin()
    for query in ['Consulte /other/context/MEMORY.md.', 'Onde salvar decisoes?', 'Leia ../MEMORY.md.', 'Leia UNKNOWN.md.']:
        ctx = _reference_fixture(module, tmp_path)
        assert module._reference_recall(ctx, query, 900, 5) is None
        assert ctx.calls == []


def test_ambiguous_basename_never_chooses_an_owner(tmp_path):
    module = _load_plugin()
    ctx = _reference_fixture(module, tmp_path, entries=[
        {'source': f'{owner}/MEMORY.md', 'target': f'canonical/{owner}/MEMORY.md'} for owner in ['a', 'b']])
    assert module._reference_recall(ctx, 'Consulte MEMORY.md.', 900, 5) == {'ok': False}
    assert ctx.calls == []


def test_reference_does_not_accept_wrong_page_or_fact_arm(tmp_path):
    module = _load_plugin()
    for payload in [{'results': [], 'facts': [{'text': 'must not inject'}]},
                    {'results': [{'slug': 'another/source', 'chunk': 'must not inject'}]}]:
        ctx = _reference_fixture(module, tmp_path, page=payload)
        result = module._reference_recall(ctx, 'Consulte MEMORY.md.', 900, 5)
        assert result['ok'] is False and not result['structuredContent']['results']


def test_reference_is_opt_in_and_bounded(tmp_path):
    module = _load_plugin()
    ctx = _reference_fixture(module, tmp_path, page={'results': [{'slug': 'canonical/context/memory', 'chunk': 'bounded'}]})
    module._reference_recall(ctx, 'Consulte MEMORY.md.', 128, 5)
    assert ctx.calls[0][2]['budget_tokens'] == 128
    assert ctx.calls[0][2]['limit'] == 3
    ctx.settings['canonical_references'] = False
    ctx.calls.clear()
    assert module._reference_recall(ctx, 'Consulte MEMORY.md.', 900, 5) is None
    assert ctx.calls == []


def test_reference_hook_requires_freshness_and_discards_changed_generation(monkeypatch, tmp_path):
    module = _load_plugin()
    monkeypatch.setattr(module, '_kill_switch_path', lambda: tmp_path / 'disabled')
    ctx = _reference_fixture(module, tmp_path)
    monkeypatch.setattr(module, '_freshness_generation', lambda ctx: '')
    assert 'No recalled passages' in module._on_pre_llm_call(ctx, user_message='Consulte MEMORY.md.')['context']
    assert not ctx.calls
    generations = iter(['a' * 64, 'b' * 64])
    monkeypatch.setattr(module, '_freshness_generation', lambda ctx: next(generations))
    result = module._on_pre_llm_call(ctx, user_message='Consulte MEMORY.md.')['context']
    assert ctx.calls[0][1] == 'recall'
    assert 'No recalled passages' in result and 'decisions.md' not in result
