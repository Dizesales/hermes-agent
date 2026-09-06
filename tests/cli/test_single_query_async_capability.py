"""Single-query entry points must not promise delivery after their only turn."""
from contextvars import Context
from types import SimpleNamespace
import pytest
import cli as cli_mod
from gateway.session_context import async_delivery_supported

@pytest.mark.parametrize("tty,quiet,oneshot,expected", [
    (False, False, False, False),
    (True, False, True, False),
    (True, True, False, False),
    (True, False, False, True),
])
def test_query_delivery_capability(monkeypatch, tty, quiet, oneshot, expected):
    observed = []
    class ReachedTurn(Exception):
        pass
    def observe():
        observed.append(async_delivery_supported())
        raise ReachedTurn()
    class FakeCLI:
        def __init__(self, **kwargs):
            self.console = SimpleNamespace(print=lambda *a, **k: None)
            self.session_id = "async-capability-test"
            self.agent = SimpleNamespace(session_id=self.session_id, platform="cli")
        def _claim_active_session(self, *a, **k): return True
        def _show_security_advisories(self): pass
        def chat(self, *a, **k): observe()
        def _ensure_runtime_credentials(self): observe()
        def run(self): observe()
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_INTERACTIVE", "0")
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "0")
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(cli_mod.sys.stdout, "isatty", lambda: tty)
    monkeypatch.setattr(cli_mod, "HermesCLI", FakeCLI)
    monkeypatch.setattr(cli_mod.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "_finalize_single_query", lambda *a: None)
    with pytest.raises(ReachedTurn):
        Context().run(cli_mod.main, query="hello", quiet=quiet,
                      oneshot=oneshot, toolsets="terminal")
    assert observed == [expected]
