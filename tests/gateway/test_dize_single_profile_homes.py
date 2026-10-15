"""Dize: a gateway that is not multiplexing serves and ticks only its launch profile.

Each Dize host runs one gateway per profile; sibling profile dirs on a host (Arconte keeps copies
of every fleet profile, Atlas keeps a parked kael-sdr) must not get their cron jobs fired, their
logs routed or their plugins injected by this process.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def profiles(monkeypatch, tmp_path):
    import hermes_cli.profiles as profiles_mod

    homes = {name: tmp_path / name for name in ("default", "atlas", "kael-sdr")}
    monkeypatch.setattr(profiles_mod, "profiles_to_serve", lambda multiplex=True, **_: list(homes.items()))
    monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda name: homes[name])
    return homes, profiles_mod


@pytest.mark.parametrize("flag", [False, None])
def test_non_multiplexing_gateway_serves_only_its_launch_profile(profiles, monkeypatch, flag):
    from gateway.run import _cron_tick_profile_homes, _multiplex_profile_homes

    homes, profiles_mod = profiles
    monkeypatch.setattr(profiles_mod, "get_active_profile_name", lambda: "atlas")
    config = SimpleNamespace(multiplex_profiles=flag)

    assert _multiplex_profile_homes(config) == [("atlas", Path(homes["atlas"]))]
    assert _cron_tick_profile_homes(config) == [("atlas", Path(homes["atlas"]))]


def test_default_launch_without_multiplex_ignores_sibling_profiles(profiles, monkeypatch):
    from gateway.run import _cron_tick_profile_homes

    homes, profiles_mod = profiles
    monkeypatch.setattr(profiles_mod, "get_active_profile_name", lambda: None)

    assert _cron_tick_profile_homes(SimpleNamespace(multiplex_profiles=False)) == [
        ("default", Path(homes["default"]))]


def test_multiplexing_gateway_keeps_upstream_profile_set(profiles, monkeypatch):
    from gateway.run import _multiplex_profile_homes

    homes, profiles_mod = profiles
    monkeypatch.setattr(profiles_mod, "get_active_profile_name", lambda: "default")

    assert _multiplex_profile_homes(SimpleNamespace(multiplex_profiles=True)) == list(homes.items())
    reserved = (("default", homes["default"]),)
    assert _multiplex_profile_homes(SimpleNamespace(multiplex_profiles=True, _runtime_profile_homes=reserved)) == list(reserved)
