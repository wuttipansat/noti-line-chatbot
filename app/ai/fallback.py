"""Use the real model, but never depend on it.

if the primary provider fails (timeout, outage, refusal), ask the backup
(the rule-based MockProvider) instead. The bot keeps working when the AI is down.
"""

import logging

import httpx2

from app.ai.provider import ExtractContext, LLMProvider

logger = logging.getLogger(__name__)


class FallbackProvider:
    def __init__(self, primary: LLMProvider, backup: LLMProvider):
        self.primary = primary
        self.backup = backup
        self.name = f"{primary.name}+{backup.name}"

    async def extract(self, text: str, context: ExtractContext) -> str:
        try:
            return await self.primary.extract(text, context)
        except (httpx2.HTTPStatusError, httpx2.TransportError) as exc:
            # Expected now and then (provider busy, quota, network): one line is enough.
            logger.warning(
                "%s unavailable (%s); using %s",
                self.primary.name,
                _why(exc),
                self.backup.name,
            )
        except Exception:  # anything else is a bug worth a full traceback
            logger.exception("%s failed; using %s", self.primary.name, self.backup.name)
        return await self.backup.extract(text, context)


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx2.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__
