import os
import secrets
import warnings

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Defaults per ABDM environment: (gateway url, ABHA url, X-CM-ID)
ABDM_ENVIRONMENTS = {
    "sbx": ("https://dev.abdm.gov.in", "https://abhasbx.abdm.gov.in", "sbx"),
    "prod": ("https://live.abdm.gov.in", "https://abha.abdm.gov.in", "abdm"),
}


def _env(name, default=None):
    """Read an env var, treating empty strings as unset (so defaults actually apply)."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name, default):
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _env_list(name, default=""):
    """Comma separated env var -> list of lowercase, stripped, non-empty items."""
    raw = _env(name, default) or ""
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _env_int_list(name, default):
    raw = _env(name, default)
    try:
        values = [int(item) for item in raw.split(",") if item.strip()]
    except ValueError:
        values = []
    return values or [int(item) for item in default.split(",")]


CALLBACK_AUTH_MODES = ("jwt", "observe", "off")


def _callback_auth_mode(abdm_env):
    mode = (_env("CALLBACK_AUTH_MODE", "jwt")).lower()
    if mode not in CALLBACK_AUTH_MODES:
        raise ValueError(f"CALLBACK_AUTH_MODE must be one of {list(CALLBACK_AUTH_MODES)}, got {mode!r}")
    if abdm_env == "prod" and mode != "jwt":
        raise ValueError("CALLBACK_AUTH_MODE must be 'jwt' when ABDM_ENV=prod")
    return mode


def _secret(name):
    value = _env(name)
    if value:
        return value
    warnings.warn(f"{name} is not set; using a random value. Tokens will not survive a restart.")
    return secrets.token_urlsafe(48)


def _abdm_settings():
    abdm_env = (_env("ABDM_ENV", "sbx")).lower()
    if abdm_env not in ABDM_ENVIRONMENTS:
        raise ValueError(f"ABDM_ENV must be one of {sorted(ABDM_ENVIRONMENTS)}, got {abdm_env!r}")
    gateway, abha, cm_id = ABDM_ENVIRONMENTS[abdm_env]
    return {
        "ABDM_ENV": abdm_env,
        "ABDM_GATEWAY_URL": _env("ABDM_GATEWAY_URL", gateway).rstrip("/"),
        "ABDM_ABHA_URL": _env("ABDM_ABHA_URL", abha).rstrip("/"),
        "ABDM_X_CM_ID": _env("ABDM_X_CM_ID", cm_id),
    }


class Config:
    APP_NAME = "abdm-gateway"
    VERSION = "0.1.0"

    SECRET_KEY = _secret("SECRET_KEY")
    JWT_SECRET_KEY = _secret("JWT_SECRET_KEY")
    ENCRYPTION_KEY = _env("ENCRYPTION_KEY")

    SQLALCHEMY_DATABASE_URI = _env(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "abdm_gateway.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REDIS_URL = _env("REDIS_URL")

    CLIENT_TOKEN_EXPIRES = _env_int("CLIENT_TOKEN_EXPIRES", 1800)
    CLIENT_REFRESH_TOKEN_EXPIRES = _env_int("CLIENT_REFRESH_TOKEN_EXPIRES", 86400)
    SESSION_RATE_LIMIT = _env("SESSION_RATE_LIMIT", "600 per minute")
    AUDIT_LOG_RETENTION_DAYS = _env_int("AUDIT_LOG_RETENTION_DAYS", 90)

    NHA_CLIENT_ID = _env("NHA_CLIENT_ID")
    NHA_CLIENT_SECRET = _env("NHA_CLIENT_SECRET")
    ABDM_TIMEOUT_CONNECT = _env_int("ABDM_TIMEOUT_CONNECT", 5)
    ABDM_TIMEOUT_READ = _env_int("ABDM_TIMEOUT_READ", 30)
    # Optional: ABDM public key (base64 DER or PEM). Empty = fetched from ABDM and cached.
    ABDM_PUBLIC_KEY = _env("ABDM_PUBLIC_KEY")
    ABDM_PUBLIC_KEY_CACHE_SECONDS = _env_int("ABDM_PUBLIC_KEY_CACHE_SECONDS", 6 * 3600)
    # How long a txnId stays bound to the client that started it
    ABDM_TXN_TTL_SECONDS = _env_int("ABDM_TXN_TTL_SECONDS", 1800)
    # Duplicate-request guard: max time a POST is held "in progress" (covers ABDM timeouts + one retry),
    # and how long a response sent with an Idempotency-Key is kept for replay
    IDEMPOTENCY_LOCK_SECONDS = _env_int("IDEMPOTENCY_LOCK_SECONDS", 60)
    IDEMPOTENCY_TTL_SECONDS = _env_int("IDEMPOTENCY_TTL_SECONDS", 86400)
    _abdm = _abdm_settings()
    ABDM_ENV = _abdm["ABDM_ENV"]
    ABDM_GATEWAY_URL = _abdm["ABDM_GATEWAY_URL"]
    ABDM_ABHA_URL = _abdm["ABDM_ABHA_URL"]
    ABDM_X_CM_ID = _abdm["ABDM_X_CM_ID"]

    # --- ABDM callbacks (ABDM -> gateway -> client app) ---
    # jwt: reject callbacks without a valid ABDM JWT. observe: verify and log the result but never reject
    # (use it on sandbox to see real callbacks first). off: skip verification (local mock only).
    CALLBACK_AUTH_MODE = _callback_auth_mode(_abdm["ABDM_ENV"])
    ABDM_JWKS_URL = _env("ABDM_JWKS_URL", _abdm["ABDM_GATEWAY_URL"] + "/api/hiecm/gateway/v3/certs")
    ABDM_JWKS_CACHE_SECONDS = _env_int("ABDM_JWKS_CACHE_SECONDS", 6 * 3600)
    # Public https URL of this gateway, registered with ABDM as the bridge URL
    GATEWAY_PUBLIC_URL = _env("GATEWAY_PUBLIC_URL")
    # Health-information pushes can be large, so callbacks get a bigger body limit than the rest of the API
    CALLBACK_MAX_BYTES = _env_int("CALLBACK_MAX_BYTES", 10 * 1024 * 1024)
    CALLBACK_ALLOW_HTTP = _env_bool("CALLBACK_ALLOW_HTTP", False)
    CALLBACK_ALLOW_PRIVATE_URLS = _env_bool("CALLBACK_ALLOW_PRIVATE_URLS", False)
    # Hosts a client may never register as a callback target (production webhooks), comma separated
    CALLBACK_BLOCKED_HOSTS = _env_list("CALLBACK_BLOCKED_HOSTS", "app.i-medicus.in")
    CALLBACK_RETENTION_DAYS = _env_int("CALLBACK_RETENTION_DAYS", 30)

    # --- Outbox / retry worker ---
    OUTBOX_POLL_SECONDS = _env_int("OUTBOX_POLL_SECONDS", 1)
    OUTBOX_BATCH = _env_int("OUTBOX_BATCH", 20)
    OUTBOX_MAX_ATTEMPTS = _env_int("OUTBOX_MAX_ATTEMPTS", 6)
    OUTBOX_BACKOFF_SECONDS = _env_int_list("OUTBOX_BACKOFF_SECONDS", "2,10,60,300,900,3600")
    OUTBOX_LOCK_SECONDS = _env_int("OUTBOX_LOCK_SECONDS", 60)
    OUTBOX_HTTP_TIMEOUT = _env_int("OUTBOX_HTTP_TIMEOUT", 15)

    LOG_LEVEL = _env("LOG_LEVEL", "INFO")
    LOG_LEVEL_LIBS = _env("LOG_LEVEL_LIBS", "WARNING")
    LOG_DIR = _env("LOG_DIR", os.path.join(BASE_DIR, "logs"))

    SWAGGER_ENABLED = _env_bool("SWAGGER_ENABLED", True)
    RESTX_MASK_SWAGGER = False
    RESTX_ERROR_404_HELP = False

    DEFAULT_RATE_LIMIT = _env("DEFAULT_RATE_LIMIT", "120 per minute")
    RATELIMIT_STORAGE_URI = REDIS_URL or "memory://"
    RATELIMIT_HEADERS_ENABLED = True

    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # M1 payloads are tiny


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    # CI runs the suite on Postgres too by setting TEST_DATABASE_URL
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL") or "sqlite:///:memory:"
    # Pinned so a developer's .env can never change what the tests check
    CALLBACK_AUTH_MODE = "jwt"
    CALLBACK_ALLOW_HTTP = False
    CALLBACK_ALLOW_PRIVATE_URLS = False
    CALLBACK_BLOCKED_HOSTS = ["app.i-medicus.in"]
    OUTBOX_BACKOFF_SECONDS = [2, 10, 60, 300, 900, 3600]
    OUTBOX_MAX_ATTEMPTS = 6
    REDIS_URL = None
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_ENABLED = False
    LOG_DIR = None
    NHA_CLIENT_ID = "test-client"
    NHA_CLIENT_SECRET = "test-secret"


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name=None):
    return CONFIGS.get((name or _env("FLASK_ENV", "development")).lower(), DevelopmentConfig)
