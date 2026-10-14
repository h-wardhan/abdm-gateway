import os

from flask import Flask, redirect, request

from src.app import models  # noqa: F401  (register models for migrations)
from src.app.api import register_namespaces
from src.app.cli import register_cli
from src.app.errors import register_error_handlers
from src.app.extensions import api, cache, db, limiter, migrate
from src.app.middleware.audit import init_audit
from src.app.middleware.request_context import init_request_context
from src.config.index import BASE_DIR, get_config
from src.config.logger import setup_logging


def _configure_database(app):
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///") and ":memory:" not in uri:
        os.makedirs(os.path.dirname(uri.replace("sqlite:///", "", 1)), exist_ok=True)
    if uri.startswith("postgresql"):
        app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "connect_args": {"connect_timeout": 10, "application_name": "abdm_gateway"},
        })


def create_app(config_name=None):
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    app.config.setdefault("RATELIMIT_DEFAULT", app.config["DEFAULT_RATE_LIMIT"])

    setup_logging(app)
    _configure_database(app)

    db.init_app(app)
    migrate.init_app(
        app, db,
        directory=os.path.join(BASE_DIR, "migrations"),
        # SQLite can't ALTER columns in place; batch mode makes the same migrations work there
        render_as_batch=app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"),
    )
    cache.init_app(app)
    limiter.init_app(app)

    # after_request hooks run in reverse order: request_context (sets latency) runs before audit
    init_audit(app)
    init_request_context(app)

    if app.config["SWAGGER_ENABLED"]:
        @app.before_request
        def _docs_redirect():
            # "/" and "/docs/" are common typos for the Swagger UI at "/docs"
            if request.path in ("/", "/docs/"):
                return redirect("/docs", code=302)
    else:
        api._doc = False
    register_namespaces(api)
    api.init_app(app)
    register_error_handlers(app, api)
    register_cli(app)

    app.logger.info(
        "abdm-gateway started",
        extra={"abdm_env": app.config["ABDM_ENV"], "cache_backend": cache.backend},
    )
    return app
