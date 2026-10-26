from flask import g
from flask_restx import Namespace, fields

from src.app.callbacks import registry
from src.app.common.response import success
from src.app.common.validators import body, pick, require
from src.app.middleware.tenant_auth import TenantResource
from src.app.models import HipRegistration

ns = Namespace("HIP Registry", path="/api/v1/hips",
               description="Your facilities (HIPs) and the webhook ABDM callbacks for them are delivered to")

hip_request = ns.model("HipRequest", {
    "callbackUrl": fields.String(required=True, example="https://dev.example.com/api/abdm-webhook"),
    "name": fields.String(example="City Clinic"),
})


def _view(reg, secret=None):
    data = {
        "hipId": reg.hip_id,
        "name": reg.name,
        "callbackUrl": reg.callback_url,
        "isActive": reg.is_active,
    }
    if secret:
        data["webhookSecret"] = secret
    return data


@ns.route("")
class HipList(TenantResource):
    @ns.doc(description="Your registered HIPs.")
    def get(self):
        rows = HipRegistration.query.filter_by(api_client_id=g.client.id).order_by(HipRegistration.id).all()
        return success([_view(reg) for reg in rows])


@ns.route("/<string:hip_id>")
class Hip(TenantResource):
    @ns.doc(description=(
        "Register a HIP or change its callback URL. ABDM callbacks for this facility are forwarded to "
        "`callbackUrl` (the ABDM path is appended, e.g. `<callbackUrl>/api/v3/hip/patient/share`). "
        "On first registration the response has `webhookSecret`, shown once: use it to verify the "
        "`X-Gateway-Signature` header. The URL must be https and must not be a private address."))
    @ns.expect(hip_request, validate=False)
    @ns.response(200, "Updated")
    @ns.response(201, "Registered")
    @ns.response(409, "hipId belongs to another client")
    def put(self, hip_id):
        data = body()
        url = require(pick(data, "callbackUrl", "callback_url"), "callbackUrl")
        existed = registry.get(hip_id) is not None
        reg, secret = registry.upsert(g.client, hip_id, url, pick(data, "name"))
        return success(_view(reg, secret), 200 if existed else 201)

    @ns.doc(description="One of your HIPs.")
    @ns.response(404, "Not found")
    def get(self, hip_id):
        return success(_view(registry.get_for_client(g.client, hip_id)))

    @ns.doc(description="Stop delivering callbacks for this HIP (it can be registered again later).")
    @ns.response(404, "Not found")
    def delete(self, hip_id):
        reg = registry.disable(registry.get_for_client(g.client, hip_id))
        return success(_view(reg))
