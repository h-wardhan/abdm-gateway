import pytest

from src.config import index as config


def test_env_list(monkeypatch):
    monkeypatch.setenv("X_LIST", " A.example.com , ,b.example.com ")
    assert config._env_list("X_LIST") == ["a.example.com", "b.example.com"]
    monkeypatch.delenv("X_LIST")
    assert config._env_list("X_LIST") == []
    assert config._env_list("X_LIST", "app.i-medicus.in") == ["app.i-medicus.in"]


def test_env_int_list(monkeypatch):
    monkeypatch.setenv("X_INTS", "5, 10,30")
    assert config._env_int_list("X_INTS", "1,2") == [5, 10, 30]
    monkeypatch.setenv("X_INTS", "five,10")
    assert config._env_int_list("X_INTS", "1,2") == [1, 2]
    monkeypatch.setenv("X_INTS", " , ")
    assert config._env_int_list("X_INTS", "1,2") == [1, 2]
    monkeypatch.delenv("X_INTS")
    assert config._env_int_list("X_INTS", "1,2") == [1, 2]


def test_callback_auth_mode_default_is_jwt(monkeypatch):
    monkeypatch.delenv("CALLBACK_AUTH_MODE", raising=False)
    assert config._callback_auth_mode("sbx") == "jwt"
    assert config._callback_auth_mode("prod") == "jwt"


@pytest.mark.parametrize("value", ["jwt", "observe", "off", "OBSERVE", " Off "])
def test_callback_auth_mode_values_in_sandbox(monkeypatch, value):
    monkeypatch.setenv("CALLBACK_AUTH_MODE", value)
    assert config._callback_auth_mode("sbx") == value.strip().lower()


def test_callback_auth_mode_rejects_unknown_values(monkeypatch):
    monkeypatch.setenv("CALLBACK_AUTH_MODE", "maybe")
    with pytest.raises(ValueError, match="CALLBACK_AUTH_MODE"):
        config._callback_auth_mode("sbx")


@pytest.mark.parametrize("value", ["observe", "off"])
def test_production_must_verify_callbacks(monkeypatch, value):
    monkeypatch.setenv("CALLBACK_AUTH_MODE", value)
    with pytest.raises(ValueError, match="must be 'jwt'"):
        config._callback_auth_mode("prod")


def test_testing_config_is_pinned_whatever_the_developers_env_says():
    testing = config.TestingConfig
    assert testing.CALLBACK_AUTH_MODE == "jwt"
    assert testing.CALLBACK_ALLOW_HTTP is False and testing.CALLBACK_ALLOW_PRIVATE_URLS is False
    assert testing.CALLBACK_BLOCKED_HOSTS == ["app.i-medicus.in"]


def test_defaults(app):
    c = app.config
    assert c["CALLBACK_MAX_BYTES"] == 10 * 1024 * 1024
    assert c["OUTBOX_BACKOFF_SECONDS"] == [2, 10, 60, 300, 900, 3600]
    assert c["OUTBOX_MAX_ATTEMPTS"] == 6
    assert c["ABDM_JWKS_URL"].endswith("/api/hiecm/gateway/v3/certs")
