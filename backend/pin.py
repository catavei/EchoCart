"""Optional payment PIN: 4 to 8 digits, typed (never spoken), stored only as a salted scrypt hash.

A spoken "yes" proves someone near the device agreed, not that it was the shopper.
The PIN is the second factor for payments, raising or removing the spending
limit, and unlinking PayPal. Standard library only.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

MAX_TRIES = 3
LOCK_MINUTES = 10
_PIN_RE = re.compile(r"[0-9]{4,8}")
_N, _R, _P = 2 ** 14, 8, 1


def valid_pin(pin: str) -> bool:
    return bool(_PIN_RE.fullmatch(pin or "")) and pin.isascii()


def _derive(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_derive(pin, salt).hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_derive(pin, bytes.fromhex(salt_hex)), bytes.fromhex(digest_hex))
    except ValueError:
        return False
