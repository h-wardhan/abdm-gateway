"""Day 21: production cannot be switched into the local-development escapes, and weak settings are reported."""
from src.app import _unsafe_settings
from src.config.index import ProductionConfig


def test_production_pins_the_private_url_escapes_off(monkeypatch):
    monkeypatch.setenv("CALLBACK_ALLOW_PRIVATE_URLS", "true")
    monkeypatch.setenv("PUSH_ALLOW_PRIVATE_URLS", "true")
    assert ProductionConfig.CALLBACK_ALLOW_PRIVATE_URLS is False and ProductionConfig.PUSH_ALLOW_PRIVATE_URLS is False


def _config(**changes):
    base = {"DEBUG": False, "TESTING": False, "ABDM_ENV": "prod", "CALLBACK_AUTH_MODE": "jwt", "OUTBOX_HTTP_TIMEOUT": 60}
    base.update(changes)
    return base


def test_a_clean_production_config_has_nothing_to_report():
    assert _unsafe_settings(_config()) == []


def test_weak_production_settings_are_listed():
    found = _unsafe_settings(_config(PUSH_ALLOW_HTTP=True, CALLBACK_ALLOW_HTTP=True, CALLBACK_AUTH_MODE="observe",
                                     OUTBOX_HTTP_TIMEOUT=15, SWAGGER_ENABLED=True))
    assert found == ["CALLBACK_ALLOW_HTTP", "PUSH_ALLOW_HTTP", "SWAGGER_ENABLED", "CALLBACK_AUTH_MODE",
                     "OUTBOX_HTTP_TIMEOUT<60"]


def test_development_and_sandbox_are_not_reported():
    assert _unsafe_settings(_config(DEBUG=True, PUSH_ALLOW_HTTP=True)) == []
    assert _unsafe_settings(_config(ABDM_ENV="sbx", PUSH_ALLOW_HTTP=True, OUTBOX_HTTP_TIMEOUT=15)) == []
