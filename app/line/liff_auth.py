"""Who is calling our API from the LIFF page? Ask LINE, never trust the browser.

The page sends the LIFF ID token (liff.getIDToken()). We send it to LINE's
verify endpoint together with our LINE Login channel ID. LINE answers with the
user's ID ("sub") only if the token is genuine, unexpired, and made for our channel.
"""

import time
from dataclasses import dataclass

import httpx2

VERIFY_PATH = "/oauth2/v2.1/verify"
CACHE_LIMIT = 1000


class InvalidToken(Exception):
    """The token is missing, fake, expired, or for another channel."""


@dataclass(frozen=True)
class LiffUser:
    line_user_id: str
    name: str | None = None


class TokenVerifier:
    def __init__(
        self,
        channel_id: str,
        base_url: str = "https://api.line.me",
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self._channel_id = channel_id
        self._url = base_url.rstrip("/") + VERIFY_PATH
        self._transport = transport  # tests pass a fake; normally None = real network
        # token -> (user, expires at). Saves one call to LINE per request.
        self._cache: dict[str, tuple[LiffUser, float]] = {}

    async def verify(self, id_token: str) -> LiffUser:
        now = time.time()
        cached = self._cache.get(id_token)
        if cached and cached[1] > now:
            return cached[0]

        async with httpx2.AsyncClient(timeout=5, transport=self._transport) as http:
            response = await http.post(
                self._url, data={"id_token": id_token, "client_id": self._channel_id}
            )
        if response.status_code != 200:
            raise InvalidToken(response.text[:200])
        claims = response.json()
        user = LiffUser(claims["sub"], claims.get("name"))

        if len(self._cache) >= CACHE_LIMIT:
            self._cache.clear()  # simple and safe: the next calls just ask LINE again
        self._cache[id_token] = (user, float(claims.get("exp", now)))
        return user
