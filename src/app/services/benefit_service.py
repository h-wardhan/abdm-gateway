"""ABHA benefit APIs (search / link / de-link)."""
from flask import request

from src.app.abdm import benefit as abdm
from src.app.abdm.crypto import encrypt
from src.app.common import validators as v
from src.app.errors import ValidationError


def _target(data):
    """(loginHint, plain value) from `abhaNumber` or `xmlUid`."""
    if v.pick(data, "abhaNumber", "ABHANumber"):
        return "abha-number", v.abha_number(v.pick(data, "abhaNumber", "ABHANumber"))
    if v.pick(data, "xmlUid"):
        return "xmlUid", str(v.pick(data, "xmlUid"))
    raise ValidationError("abhaNumber or xmlUid is required", details={"field": "abhaNumber"})


def _benefit_name(data):
    """The benefit programme (ABDM header BENEFIT_NAME): body `benefitName` or the BENEFIT_NAME request header."""
    name = v.pick(data, "benefitName", "benefit_name") or request.headers.get("BENEFIT_NAME")
    return str(v.require(name, "benefitName")).strip()


def search(data):
    name = _benefit_name(data)
    hint, value = _target(data)
    return abdm.search(name, hint, encrypt(value))


def link_or_delink(data):
    name = _benefit_name(data)
    action = str(v.pick(data, "action") or "").strip().lower()
    if action not in ("link", "de-link"):
        raise ValidationError("action must be 'link' or 'de-link'", code="INVALID_ACTION", details={"field": "action"})
    try:
        token = v.x_token(data)
    except ValidationError:
        token = None
    if token and action == "link":
        return abdm.link_and_delink(name, action, x_token=token)
    hint, value = _target(data)
    return abdm.link_and_delink(name, action, hint, encrypt(value))


def abha_by_number(abha_number):
    name = request.headers.get("BENEFIT_NAME") or request.args.get("benefitName")
    return abdm.abha_by_number(v.abha_number(abha_number), name)
