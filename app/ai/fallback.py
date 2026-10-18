"""Use the real model, but never depend on it.

if the primary provider fails (timeout, outage, refusal), ask the backup
(the rule-based MockProvider) instead. The bot keeps working when the AI is down.
"""

import logging

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
        except Exception:
            logger.exception("%s failed; using %s", self.primary.name, self.backup.name)
            return await self.backup.extract(text, context)
