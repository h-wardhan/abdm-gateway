"""
txnId ownership: the client that starts an ABDM flow owns its txnIds, other clients get 403.

- `assert_owner(txn_id)` before using a txnId the caller sent.
- `record(txn_id, flow, step)` for every txnId ABDM hands back.
Unknown txnIds are allowed (they may come from a flow started before this gateway existed),
and are recorded for the caller.
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app, g

from src.app.errors import ForbiddenError
from src.app.extensions import cache, db
from src.app.models import AbdmTransaction

logger = logging.getLogger(__name__)


def _cache_key(txn_id):
    return f"txn:{txn_id}"


def _owner_id(txn_id):
    cached = cache.get(_cache_key(txn_id))
    if cached is not None:
        return cached
    row = AbdmTransaction.query.filter_by(txn_id=txn_id).first()
    return row.api_client_id if row else None


def assert_owner(txn_id):
    owner = _owner_id(txn_id)
    if owner is not None and owner != g.client.id:
        logger.warning("txnId used by another client", extra={"txn_owner": owner})
        raise ForbiddenError("This transaction belongs to another client", code="TXN_FORBIDDEN")


def record(txn_id, flow, step, status="active"):
    if not txn_id:
        return
    ttl = current_app.config["ABDM_TXN_TTL_SECONDS"]
    try:
        row = AbdmTransaction.query.filter_by(txn_id=txn_id).first()
        if row is None:
            row = AbdmTransaction(txn_id=txn_id, api_client_id=g.client.id, flow=flow)
            db.session.add(row)
        elif row.api_client_id != g.client.id:
            return
        row.step = step
        row.status = status
        row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        db.session.commit()
        cache.set(_cache_key(txn_id), g.client.id, ttl=ttl)
    except Exception:  # noqa: BLE001  (bookkeeping must not fail the ABDM flow)
        db.session.rollback()
        logger.exception("Could not record txnId")


def record_from(response, flow, step, status="active"):
    """Record the txnId found in an ABDM response (dict, or list of dicts like ABHA search)."""
    items = response if isinstance(response, list) else [response]
    for item in items:
        if isinstance(item, dict) and item.get("txnId"):
            record(str(item["txnId"]), flow, step, status)
