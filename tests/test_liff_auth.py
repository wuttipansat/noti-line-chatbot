"""LIFF ID tokens are checked by LINE, then cached. No network: httpx2.MockTransport."""

import asyncio
import time

import httpx2
import pytest

from app.line.liff_auth import InvalidToken, LiffUser, TokenVerifier


def fake_line(status: int = 200, claims: dict | None = None):
    received: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        received.append(request)
        if status != 200:
            return httpx2.Response(status, json={"error": "invalid_request"})
        body = claims or {"sub": "U-alice", "name": "Alice", "exp": time.time() + 600}
        return httpx2.Response(200, json=body)

    return httpx2.MockTransport(handler), received


def test_valid_token_gives_the_line_user():
    transport, received = fake_line()
    verifier = TokenVerifier("1650000000", transport=transport)

    user = asyncio.run(verifier.verify("good-token"))

    assert user == LiffUser("U-alice", "Alice")
    [request] = received
    assert str(request.url) == "https://api.line.me/oauth2/v2.1/verify"
    assert request.content == b"id_token=good-token&client_id=1650000000"


def test_rejected_token_raises():
    transport, _ = fake_line(status=400)
    verifier = TokenVerifier("1650000000", transport=transport)

    with pytest.raises(InvalidToken):
        asyncio.run(verifier.verify("fake"))


def test_same_token_is_asked_once_until_it_expires():
    transport, received = fake_line()
    verifier = TokenVerifier("1650000000", transport=transport)

    async def twice():
        await verifier.verify("good-token")
        await verifier.verify("good-token")

    asyncio.run(twice())
    assert len(received) == 1


def test_expired_cache_entry_asks_line_again():
    claims = {"sub": "U-alice", "exp": time.time() - 1}  # already expired
    transport, received = fake_line(claims=claims)
    verifier = TokenVerifier("1650000000", transport=transport)

    async def twice():
        await verifier.verify("old-token")
        await verifier.verify("old-token")

    asyncio.run(twice())
    assert len(received) == 2
