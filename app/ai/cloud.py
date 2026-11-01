"""
A cloud language model behind an OpenAI-compatible Chat Completions API.

One class works with many providers, only .env changes:
    Google Gemini, Groq, OpenRouter, OpenAI, ... (base URl + key + model_name).

The answer is forced into OUTPUT_SCHEMA when the provider supports it
(LLM_JSON_MODE=json_schema), and is ALWAYS checkd by app/ai/interpreter.py.
"""

import asyncio
import logging
import time
from typing import Literal

import httpx2

from app.ai.prompt import OUTPUT_SCHEMA, SCHEMA_TEXT, SYSTEM_PROMPT, user_message
from app.ai.provider import ExtractContext

logger = logging.getLogger(__name__)

# "Busy, try again" answers. One quick retry often succeeds; 429 (quota) does not,
# so it goes straight to the fallback.
RETRY_STATUSES = {500, 502, 503, 504}


class CloudProvider:
    name = "cloud"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        json_mode: Literal["json_schema", "json_object"] = "json_schema",
        transport: httpx2.AsyncBaseTransport | None = None,
        retry_delay: float = 1.0,
    ):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = timeout
        self._json_mode = json_mode
        self.name = f"cloud({model})"  # which model, in logs
        self._retry_delay = retry_delay
        self._transport = transport

    def _response_format(self) -> dict:
        if self._json_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "command", "schema": OUTPUT_SCHEMA, "strict": True},
        }

    async def _post(self, http: httpx2.AsyncClient, body: dict) -> httpx2.Response:
        """POST once; if the provider is busy (5xx), wait a moment and try once more."""
        response = await http.post(self._url, headers=self._headers, json=body)
        if response.status_code in RETRY_STATUSES:
            logger.warning(
                "%s busy (%d); retrying once", self.name, response.status_code
            )
            await asyncio.sleep(self._retry_delay)
            response = await http.post(self._url, headers=self._headers, json=body)
        return response

    async def extract(self, text: str, context: ExtractContext) -> str:
        started = time.perf_counter()
        body = {
            "model": self._model,
            "temperature": 0,
            "response_format": self._response_format(),
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nJSON schema:\n{SCHEMA_TEXT}",
                },
                {"role": "user", "content": user_message(text, context)},
            ],
        }
        # A fresh client per call: each Celery task runs its own event loop
        # (asyncio.run), and a client must not be shared between loops.
        async with httpx2.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as http:
            response = await self._post(http, body)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        logger.info(
            "llm call model=%s ms=%d in=%s out=%s",
            self._model,
            (time.perf_counter() - started) * 1000,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

        return data["choices"][0]["message"]["content"]
