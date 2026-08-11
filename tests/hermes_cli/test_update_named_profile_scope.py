from unittest.mock import patch

from hermes_cli.update_cmd import _sync_named_profiles_on_update_enabled


def test_named_profile_update_sync_defaults_enabled():
    with patch("hermes_cli.config.load_config", return_value={}):
        assert _sync_named_profiles_on_update_enabled() is True


def test_named_profile_update_sync_can_be_disabled_for_owner_hosts():
    config = {"updates": {"sync_named_profiles": False}}
    with patch("hermes_cli.config.load_config", return_value=config):
        assert _sync_named_profiles_on_update_enabled() is False


def test_named_profile_update_sync_accepts_false_string():
    config = {"updates": {"sync_named_profiles": "off"}}
    with patch("hermes_cli.config.load_config", return_value=config):
        assert _sync_named_profiles_on_update_enabled() is False


def test_named_profile_update_sync_fails_open_on_invalid_config():
    with patch("hermes_cli.config.load_config", side_effect=RuntimeError("boom")):
        assert _sync_named_profiles_on_update_enabled() is True
