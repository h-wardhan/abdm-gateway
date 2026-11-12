"""InvoiceRecord: a ChargeItem per line, one Invoice with the price components the IG defines (MRP, rate, discount,
CGST, SGST)."""
import logging

from src.app.fhir import constants as c
from src.app.fhir import util as u
from src.app.fhir.core import DocumentBuilder
from src.app.fhir.errors import InvalidRecord

logger = logging.getLogger(__name__)

_BILLING = {"00": "Consultation", "01": "Pharmacy", "02": "IPD", "03": "OPD", "04": "Pathology", "05": "Medicines",
            "06": "Nursing Charges", "07": "Handling Charges", "08": "Delivery Charges", "99": "Others"}
_INVOICE_TYPE = ("01", "Pharmacy")  # medicines are what i-medicus bills; the IG's invoice types are 00/01/02/03/99


def _money(value):
    return {"value": value, "currency": "INR"}


def _price(kind, code, display, value):
    return {"type": kind, "code": u.concept(u.coding(c.PRICE_COMPONENTS, code, display)), "amount": _money(value)}


def build(payload, hip_id, when=None):
    b = DocumentBuilder(payload, hip_id, when)
    lines = u.items(payload, "lineItems")
    if not lines:
        raise InvalidRecord("The invoice has no lineItems", details={"field": "lineItems"})

    line_items, net, gross = [], 0.0, 0.0
    for row in lines:
        name = u.text(row.get("itemName"))
        rate, quantity = u.number(row.get("rate")), u.number(row.get("quantity")) or 1
        if not name or rate is None:
            logger.warning("fhir: invoice line without a name or rate skipped")
            continue
        billing = u.text(row.get("billingCode")) or "05"
        billing = billing if billing in _BILLING else "99"
        charge = b.add("ChargeItem", {
            "status": "billed",
            "code": u.concept(u.coding(c.BILLING_CODES, billing, _BILLING[billing])),
            "subject": b.patient, "performer": [{"actor": b.practitioner}],
            "quantity": {"value": quantity},
            "productCodeableConcept": u.snomed_concept(row.get("snomedCode"), name),
        }, display=name)
        cgst, sgst = u.number(row.get("cgst")) or 0, u.number(row.get("sgst")) or 0
        total = u.number(row.get("total"))
        total = total if total is not None else rate * quantity + cgst + sgst
        net += rate * quantity
        gross += total
        components = [_price("base", "01", "Rate", rate)]
        if u.number(row.get("mrp")) is not None:
            components.append(_price("informational", "00", "MRP", u.number(row.get("mrp"))))
        components += [_price("discount", "02", "Discount", u.number(row.get("discount")) or 0),
                       _price("tax", "03", "CGST", cgst), _price("tax", "04", "SGST", sgst)]
        line_items.append({"sequence": len(line_items) + 1, "chargeItemReference": charge,
                           "priceComponent": components})
    if not line_items:
        raise InvalidRecord("None of the invoice lines has a name and a rate", details={"field": "lineItems"})

    encounter_id = u.text(payload["encounter"].get("encounterId"))
    invoice = b.add("Invoice", {
        "identifier": [{"system": "https://ndhm.in/invoice", "value": f"INV-{encounter_id}"}],
        "status": "issued",
        "type": u.concept(u.coding(c.BILLING_CODES, *_INVOICE_TYPE)),
        "subject": b.patient, "date": b.timestamp,
        "participant": [{"actor": b.practitioner}], "issuer": b.organization,
        "lineItem": line_items,
        "totalNet": _money(round(net, 2)), "totalGross": _money(round(gross, 2)),
    }, display="Invoice")
    return b.finish("InvoiceRecord", {"text": "Invoice Record"}, "Invoice Record",
                    [("Invoice details", None, [b.typed(invoice, "Invoice")])])
