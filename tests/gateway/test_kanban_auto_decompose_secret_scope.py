"""Auto-decompose tick under multiplex.

Regression for #107955 / #57837: the tick runs off-turn in a fresh Context, so
``get_secret`` fails closed unless the tick installs the launch profile's scope.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from agent import secret_scope as ss
from gateway import kanban_watchers_dispatcher as kwd
from gateway.kanban_watchers_common import _to_thread_process_service


def _dispatcher():
    settings = kwd._DispatcherSettings(60.0, None, None, 2, 0, True, None, None)
    return kwd._KanbanDispatcher(SimpleNamespace(DEFAULT_BOARD="default"), settings)


def test_auto_decompose_tick_reads_launch_profile_secrets_under_multiplex(monkeypatch, tmp_path):
    import hermes_cli

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=launch-profile-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])

    seen = {}

    def fake_decompose(task_id, author=None):
        seen["value"] = ss.get_secret("ANTHROPIC_API_KEY")
        return SimpleNamespace(ok=True, fanout=False, child_ids=None, reason=None)

    fake = SimpleNamespace(list_triage_ids=lambda: ["t1"], decompose_task=fake_decompose)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)
    monkeypatch.setattr(hermes_cli, "kanban_decompose", fake, raising=False)

    ss.set_multiplex_active(True)
    try:
        # Same hop the gateway uses: fresh Context, no inherited per-turn scope.
        decomposed = asyncio.run(_to_thread_process_service(_dispatcher().auto_decompose_tick, 5))
    finally:
        ss.set_multiplex_active(False)

    assert decomposed == 1
    assert seen["value"] == "launch-profile-key"
    assert ss.current_secret_scope() is None


def test_auto_decompose_skips_already_decomposed_without_using_a_slot():
    """F12: the tick skips a re-triaged decomposed root before counting it against the per-tick cap."""
    from types import SimpleNamespace
    from gateway.kanban_watchers_dispatcher import _KanbanDispatcher as KanbanDispatcher
    calls = []
    fake = SimpleNamespace(already_decomposed=lambda tid: tid == "t_loop")
    assert KanbanDispatcher._skip_already_decomposed(fake, "default", "t_loop") is True
    assert KanbanDispatcher._skip_already_decomposed(fake, "default", "t_new") is False
    # Older decomposer module without the helper: never skip.
    assert KanbanDispatcher._skip_already_decomposed(SimpleNamespace(), "default", "t_loop") is False
    # A failing check never blocks decomposition.
    def boom(tid):
        raise RuntimeError("db busy")
    assert KanbanDispatcher._skip_already_decomposed(SimpleNamespace(already_decomposed=boom), "default", "t_loop") is False
