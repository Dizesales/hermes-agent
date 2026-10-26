from gateway.run import _start_gateway_housekeeping


class _TenTicks:
    def __init__(self):
        self.waits = 0

    def is_set(self):
        return self.waits >= 10

    def wait(self, timeout=None):
        del timeout
        self.waits += 1
        return self.is_set()


def test_existing_housekeeping_loop_fires_bounded_plugin_tick(monkeypatch):
    from hermes_cli.plugins import VALID_HOOKS

    assert "gateway_housekeeping_tick" in VALID_HOOKS
    calls = []
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda name, **kwargs: calls.append((name, kwargs)),
    )

    _start_gateway_housekeeping(_TenTicks(), adapters=None, loop=None, interval=60)

    assert calls == [(
        "gateway_housekeeping_tick",
        {"tick_count": 10, "interval_seconds": 60},
    )]
