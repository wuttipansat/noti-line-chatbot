"""Choose the language model from settings (LLM_PROVIDER in .env)."""

from app.ai.cloud import CloudProvider
from app.ai.fallback import FallbackProvider
from app.ai.mock import MockProvider
from app.ai.provider import LLMProvider
from app.config import Settings


def build_provider(settings: Settings) -> LLMProvider:
    """cloud -> (second cloud model, if set) -> mock. Each step only if the one before fails."""
    if settings.llm_provider != "cloud" or settings.llm_api_key is None:
        return MockProvider()

    def cloud(model: str) -> CloudProvider:
        return CloudProvider(
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            model,
            timeout=settings.llm_timeout_seconds,
            json_mode=settings.llm_json_mode,
        )

    backup: LLMProvider = MockProvider()
    if settings.llm_fallback_model:
        backup = FallbackProvider(cloud(settings.llm_fallback_model), backup)
    return FallbackProvider(cloud(settings.llm_model), backup)
