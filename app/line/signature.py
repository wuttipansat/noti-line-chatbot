"""LINE webhook signature check.

LINE signs the raw request body with the channel secret:
    x-line-signature = Base64(HMAC-SHA256(channel_secret, raw_body))
We must verify the exact bytes we received before parsing the JSON.
"""

import base64
import hashlib
import hmac

def compute_signature(channel_secret: str, body: bytes) -> str:
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()

def is_valid_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False

    expected = compute_signature(channel_secret, body)
    return hmac.compare_digest(expected, signature)
