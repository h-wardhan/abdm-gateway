import logging

from flask import current_app
from flask_restx import Namespace, Resource
from sqlalchemy import text

from src.app.common.response import error_body, success
from src.app.extensions import cache, db

logger = logging.getLogger(__name__)

ns = Namespace("Health", path="/", description="Liveness and readiness checks")


@ns.route("health")
class Health(Resource):
    @ns.doc(security=[], description="Liveness: the process is up.")
    def get(self):
        return success({
            "status": "ok",
            "service": current_app.config["APP_NAME"],
            "version": current_app.config["VERSION"],
            "abdmEnv": current_app.config["ABDM_ENV"],
        })


@ns.route("ready")
class Ready(Resource):
    @ns.doc(security=[], description="Readiness: database and cache are reachable.")
    def get(self):
        checks = {}
        try:
            db.session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness: database check failed", extra={"error": str(exc)})
            checks["database"] = "down"
        try:
            checks["cache"] = "ok" if cache.ping() else "down"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness: cache check failed", extra={"error": str(exc)})
            checks["cache"] = "down"
        checks["cacheBackend"] = cache.backend
        checks["abdmCredentials"] = (
            "configured"
            if current_app.config.get("NHA_CLIENT_ID") and current_app.config.get("NHA_CLIENT_SECRET")
            else "missing"
        )

        if "down" in (checks["database"], checks["cache"]):
            body = error_body("NOT_READY", "One or more dependencies are down", details=checks)
            return body, 503
        return success(checks)
