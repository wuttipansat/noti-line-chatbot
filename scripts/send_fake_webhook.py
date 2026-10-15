import json
import sys
import time
import urllib.request
from uuid import uuid4

from app.config import get_settings
from app.line.signature import compute_signature

URL = "http://127.0.0.1:8000/webhook/line"

def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello"
    secret = get_settings().line_channel_secret
    if secret is None:
        sys.exit("Set LINE_CHANNEL_SECRET in .env first")

    event = {
        "type": "message",
        "webhookEventId": f"fake-{uuid4()}",
        "timestamp": int(time.time() * 1000),
        "source": {"type": "user", "userId": "U-local-dev"},
        "replyToken": "fake-reply-token",
        "deliveryContext": {"isRedelivery": False},
        "mode": "active",
        "message": {"id": "1", "type": "text", "text": text},
    }
    body = json.dumps({"destination": "U0", "events": [event]}).encode()
    request = urllib.request.Request(
        URL,
        data=body,
        headers={
            "context-type": "application/json",
            "x-line-signature": compute_signature(secret.get_secret_value(), body),
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        print(response.status, response.read().decode())

if __name__ == "__main__":
    main()

