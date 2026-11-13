from __future__ import annotations

import hashlib
import hmac


def hash_phone_token(canonical_phone: str, key: str) -> str:
    """Computes an HMAC-SHA256 blind index token for a canonical phone (+91XXXXXXXXXX)."""
    return hmac.new(
        key.encode("utf-8"),
        canonical_phone.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """Returns masked phone format +91******XXXX from canonical phone (+91XXXXXXXXXX)."""
    if len(canonical_phone) >= 4:
        return "+91******" + canonical_phone[-4:]
    return "+91******0000"


def mask_phone_from_last4(last4: str) -> str:
    """Returns masked phone format +91******XXXX from last 4 digits."""
    return f"+91******{last4}"

