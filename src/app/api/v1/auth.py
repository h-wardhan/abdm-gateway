from flask import current_app, g, request
from flask_restx import Namespace, Resource, fields

from src.app.common.response import success
from src.app.extensions import limiter
from src.app.middleware.tenant_auth import TenantResource
from src.app.services import session_service

ns = Namespace("Session", path="/api/v1/auth", description="Client app authentication")

session_request = ns.model("SessionRequest", {
    "clientId": fields.String(required=True, example="i-medicus-3f9a1c2b7d10"),
    "clientSecret": fields.String(required=True, example="<secret shown once by `flask clients create`>"),
})

refresh_request = ns.model("RefreshRequest", {
    "refreshToken": fields.String(required=True),
})

tokens = ns.model("SessionTokens", {
    "accessToken": fields.String,
    "expiresIn": fields.Integer(description="seconds"),
    "refreshToken": fields.String,
    "refreshExpiresIn": fields.Integer(description="seconds"),
    "tokenType": fields.String(example="Bearer"),
})

session_response = ns.model("SessionResponse", {
    "success": fields.Boolean(example=True),
    "data": fields.Nested(tokens),
    "requestId": fields.String,
})

client_info = ns.model("ClientInfo", {
    "clientId": fields.String,
    "name": fields.String,
    "hipId": fields.String,
    "rateLimit": fields.String,
})


def _session_rate_limit():
    return current_app.config["SESSION_RATE_LIMIT"]


@ns.route("/session")
class Session(Resource):
    decorators = [limiter.limit(_session_rate_limit)]

    @ns.doc(
        security=[],
        description=(
            "Exchange client credentials for an access token. Send the token as `Authorization` "
            "(with or without `Bearer `) on every other call. Credentials are also accepted in the "
            "`Client-Id` / `Client-Secret` headers."
        ),
    )
    @ns.expect(session_request, validate=False)
    @ns.response(200, "Session created", session_response)
    @ns.response(401, "Invalid client credentials")
    @ns.response(403, "Client disabled")
    def post(self):
        body = request.get_json(silent=True) or {}
        client_id = body.get("clientId") or request.headers.get("Client-Id")
        client_secret = body.get("clientSecret") or request.headers.get("Client-Secret")
        client, data = session_service.create_session(client_id, client_secret)
        g.client = client
        return success(data)


@ns.route("/refresh")
class Refresh(Resource):
    decorators = [limiter.limit(_session_rate_limit)]

    @ns.doc(security=[], description="Get a new access token using a refresh token.")
    @ns.expect(refresh_request, validate=False)
    @ns.response(200, "New tokens", session_response)
    def post(self):
        body = request.get_json(silent=True) or {}
        client, data = session_service.refresh_session(body.get("refreshToken"))
        g.client = client
        return success(data)


@ns.route("/me")
class Me(TenantResource):
    @ns.doc(description="Return the client app the access token belongs to. Handy for testing a token.")
    @ns.response(200, "Client info", client_info)
    def get(self):
        client = g.client
        return success({
            "clientId": client.client_id,
            "name": client.name,
            "hipId": client.hip_id,
            "rateLimit": client.rate_limit or current_app.config["DEFAULT_RATE_LIMIT"],
        })
