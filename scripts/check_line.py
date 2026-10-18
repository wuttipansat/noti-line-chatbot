"""Check the LINE credentials in .env by asking LINE who our bot is.

Run: python -m scripts.check_line
"""

import sys

import httpx2

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.line_channel_access_token is None:
        sys.exit("LINE_CHANNEL_ACCESS_TOKEN is not set in .env")
    if settings.line_channel_secret is None:
        sys.exit("LINE_CHANNEL_SECRET is not set in .env")

    token = settings.line_channel_access_token.get_secret_value()
    response = httpx2.get(
        f"{settings.line_api_base_url.rstrip('/')}/v2/bot/info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )

    if response.status_code != 200:
        sys.exit(f"LINE said HTTP {response.status_code}: {response.text}")

    info = response.json()
    print("Token works!")
    print(f"  bot name: {info.get('displayName')}")
    print(f"  basic id: {info.get('basicId')}")


if __name__ == "__main__":
    main()
