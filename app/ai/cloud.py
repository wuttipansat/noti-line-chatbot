"""
A cloud language model behind an OpenAI-compatible Chat Completions API.

One class works with many providers, only .env changes:
    Google Gemini, Groq, OpenRouter, OpenAI, ... (base URl + key + model_name).

The answer is forced into OUTPUT_SCHEMA when the provider supports it
(LLM_JSON_MODE=json_schema), and is ALWAYS checkd by app/ai/interpreter.py.
"""

import logging
import time
from typing import Literal

import httpx2

from app.ai.prompt import OUTPUT_SCHEMA, SCHEMA_TEXT, SYSTEM_PROMPT, user_message
from app.ai.provider import ExtractContext

logger = logging.getLogger(__name__)


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
    ):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = timeout
        self._json_mode = json_mode
        self._transport = transport

    def _response_format(self) -> dict:
        if self._json_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "command", "schema": OUTPUT_SCHEMA, "strict": True},
        }

    async def extract(self, text: str, context: ExtractContext) -> str:
        started = time.perf_counter()

        async with httpx2.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as http:
            response = await http.post(
                self._url,
                headers=self._headers,
                json={
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
                },
            )

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
