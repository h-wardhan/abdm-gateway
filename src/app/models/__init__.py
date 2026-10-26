"""Import every model here so Flask-Migrate (Alembic autogenerate) sees them."""
from src.app.models.abdm_transaction import AbdmTransaction
from src.app.models.api_client import ApiClient
from src.app.models.audit import AbdmErrorLog, ApiAuditLog
from src.app.models.callback_event import CallbackEvent
from src.app.models.hip_registration import HipRegistration
from src.app.models.outbox_job import OutboxJob

__all__ = [
    "ApiClient", "AbdmTransaction", "ApiAuditLog", "AbdmErrorLog",
    "HipRegistration", "CallbackEvent", "OutboxJob",
]
