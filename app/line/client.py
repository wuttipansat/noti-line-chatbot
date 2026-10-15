"""LINE Messaging API calls"""

from typing import Any
from uuid import UUID

import httpx2

class LineClient:
    def __init__(self, access_token: str, base_url: str, http: httpx2.AsyncClient):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._base_url = base_url.rstrip("/")
        self._http = http

    async def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> int:
        response = await self._http.post(
            f"{self._base_url}/v2/bot/message/reply",
            json={"replyToken": reply_token, "messages": messages},
            headers=self._headers,
        )
        return response.status_code

    async def push(self, to: str, messages: list[dict[str, Any]], retry_key: UUID) -> int:
        response = await self._http.post(
            f"{self._base_url}/v2/bot/message/push",
            json={"to": to, "messages": messages},
            headers={**self._headers, "X-Line-Retry-Key": str(retry_key)},
        )
        return response.status_code

    

    
        