"""Small helpers shared by every builder: tolerant input reading, FHIR dates, codings, narrative."""
import base64
import binascii
import re
from datetime import date, datetime, timedelta, timezone
from html import escape

from src.app.fhir import constants as c
from src.app.fhir.errors import InvalidRecord

IST = timezone(timedelta(minutes=c.IST_OFFSET_MINUTES))

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %I:%M %p", "%d %b, %Y %I:%M %p", "%d-%m-%Y %H:%M:%S",
                     "%d-%m-%Y %I:%M %p")
_PLACEHOLDERS = {"n/a", "na", "null", "none", "-"}


def text(value):
    """A stripped string, or None for None / empty / 'N/A' style placeholders."""
    if value is None or isinstance(value, (dict, list)):
        return None
    out = str(value).strip()
    return None if not out or out.lower() in _PLACEHOLDERS else out


def number(value):
    """int / float from a number or numeric string, else None (True/False are not numbers)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    raw = str(value).strip()
    try:
        out = float(raw)
    except ValueError:
        return None
    return int(out) if out.is_integer() and "." not in raw else out


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def items(payload, key):
    """payload[key] as a list of dicts (anything else is dropped)."""
    return [i for i in as_list(payload.get(key)) if isinstance(i, dict)]


def parse(value):
    """datetime (aware, IST when the input had no zone) from the many shapes i-medicus sends; None if unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = str(value).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _DATETIME_FORMATS + _DATE_FORMATS:
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def fhir_datetime(value):
    dt = parse(value)
    return dt.isoformat(timespec="seconds") if dt else None


def fhir_date(value):
    dt = parse(value)
    return dt.date().isoformat() if dt else None


def now():
    return datetime.now(IST).replace(microsecond=0)


def coding(system, code, display=None):
    out = {"system": system, "code": str(code)}
    if display:
        out["display"] = display
    return out


def concept(*codings, text=None):
    out = {}
    cs = [x for x in codings if x]
    if cs:
        out["coding"] = cs
    if text:
        out["text"] = text
    return out


def snomed_concept(code, display):
    """CodeableConcept with a SNOMED coding when both code and display are known, else text only (or None)."""
    code, display = text(code), text(display)
    if code and display:
        return concept(coding(c.SNOMED, code, display), text=display)
    return concept(text=display) if display else None


def narrative(label):
    return {"status": "generated",
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{escape(label)}</div>'}


_PDF, _JPEG, _PNG = b"%PDF", b"\xff\xd8\xff", b"\x89PNG"


def attachment(doc, what, default_title):
    """(content_type, base64, title) for a document dict from i-medicus. The bytes are checked, not trusted:
    the real type is sniffed (i-medicus always says application/pdf, even for an uploaded image)."""
    if not isinstance(doc, dict):
        raise InvalidRecord(f"{what}: document is missing")
    data = text(doc.get("base64Data"))
    if not data:
        raise InvalidRecord(f"{what}: document has no content (base64Data is empty; was the file found?)")
    data = re.sub(r"^data:[^,]*,", "", data).replace("\n", "").replace("\r", "")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidRecord(f"{what}: document is not valid base64")
    if raw.startswith(_PDF):
        ctype = "application/pdf"
    elif raw.startswith(_JPEG):
        ctype = "image/jpeg"
    elif raw.startswith(_PNG):
        ctype = "image/png"
    else:
        raise InvalidRecord(f"{what}: document is not a PDF, JPEG or PNG file")
    return ctype, data, text(doc.get("title")) or default_title


# Canonical UCUM unit per LOINC code. i-medicus sends "/min" for every vital sign, so the unit of a known vital
# comes from here; the sent unit is only used for codes we do not know.
_CANONICAL = {
    "8867-4": "/min", "9279-1": "/min", "8310-5": "[degF]", "61008-9": "[degF]", "8329-5": "[degF]",
    "2708-6": "%", "59408-5": "%", "8480-6": "mm[Hg]", "8462-4": "mm[Hg]", "29463-7": "kg", "8302-2": "cm",
    "39156-5": "kg/m2",
}
_UNIT_ALIASES = {"kg": "kg", "kgs": "kg", "cm": "cm", "in": "[in_i]", "inch": "[in_i]", "inches": "[in_i]",
                 "f": "[degF]", "c": "Cel", "%": "%", "/min": "/min", "bpm": "/min",
                 "mmhg": "mm[Hg]", "mm[hg]": "mm[Hg]", "lb": "[lb_av]", "lbs": "[lb_av]", "mg/dl": "mg/dL",
                 "g/dl": "g/dL"}
_UNIT_LABEL = {"[degF]": "degF", "Cel": "degC"}


def quantity(value, unit, loinc=None):
    """Quantity for a numeric value; the unit is UCUM-coded when it is one we recognise."""
    ucum = _CANONICAL.get(loinc) if loinc else None
    given = text(unit)
    if not ucum and given:
        ucum = _UNIT_ALIASES.get(given.lower())
    out = {"value": value}
    if ucum:
        out.update(unit=_UNIT_LABEL.get(ucum, ucum), system=c.UCUM, code=ucum)
    elif given:
        out["unit"] = given
    return out
