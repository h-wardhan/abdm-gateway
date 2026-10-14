"""
Aadhaar Verhoeff validation — matches ABDM portal JavaScript implementation.
"""
import re

# Verhoeff tables (d, p, inv from ABDM portal)
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]

_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]

_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _inv_array(digits):
    """JS invArray — reverse digit list."""
    return list(reversed(digits))


def _generate_checksum(first_eleven):
    """JS generate — checksum digit for first 11 digits."""
    c = 0
    inverted = _inv_array(list(first_eleven))
    for i, digit in enumerate(inverted):
        c = _D[c][_P[(i + 1) % 8][digit]]
    return _INV[c]


def _verhoeff_validate(all_twelve):
    """JS validate — full 12-digit Verhoeff check (c === 0)."""
    c = 0
    inverted = _inv_array(list(all_twelve))
    for i, digit in enumerate(inverted):
        c = _D[c][_P[i % 8][digit]]
    return c == 0


def normalize_aadhaar_digits(aadhaar_number):
    """Strip non-digits from input."""
    return re.sub(r"\D", "", str(aadhaar_number).strip())


def validate_aadhaar(aadhaar_string):
    """
    JS validateAadhaar — True if 12-digit Aadhaar passes Verhoeff checksum.
    """
    if len(aadhaar_string) != 12:
        return False
    if re.search(r"[^0-9]", aadhaar_string):
        return False

    digits = [int(d) for d in aadhaar_string]
    check_digit = digits.pop()
    return _generate_checksum(digits) == check_digit


def validate_aadhaar_number(aadhaar_number):
    """Validate exactly 12 numeric digits with Verhoeff checksum."""
    raw = str(aadhaar_number).strip().replace(" ", "")
    if len(raw) != 12 or not raw.isdigit():
        return False
    return validate_aadhaar(raw)
