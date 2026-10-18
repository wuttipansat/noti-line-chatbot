"""Choose the language model from settings (LLM_PROVIDER in .env)."""

from app.ai.cloud import CloudProvider
from app.ai.fallback import FallbackProvider
from app.ai.mock import MockProvider
from app.ai.provider import LLMProvider
from app.config import Settings


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "cloud" and settings.llm_api_key is not None:
        cloud = CloudProvider(
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            json_mode=settings.llm_json_mode,
        )
        return FallbackProvider(cloud, MockProvider())
    return MockProvider()
