"""
AI provider abstraction.

The orchestrator speaks one normalised message/tool format (see base.py) and
never imports a vendor SDK directly. `build_provider()` picks the concrete
provider from the environment; adding a provider is one module implementing
`LLMProvider` plus one line in `_FACTORIES`.
"""
from __future__ import annotations

import os

from .base import LLMProvider, ProviderInfo, ToolDef  # noqa: F401


def _anthropic():
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"],
                             model=os.environ.get("JARVIS_AI_MODEL") or "claude-opus-5")


def _openai():
    from .openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        provider_id="openai",
        base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com",
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("JARVIS_AI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1")


def _local():
    from .openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        provider_id="local",
        base_url=os.environ["LOCAL_LLM_URL"],
        api_key=os.environ.get("LOCAL_LLM_API_KEY", ""),
        model=os.environ.get("JARVIS_AI_MODEL") or os.environ.get("LOCAL_LLM_MODEL") or "llama3.2")


def _gemini():
    from .gemini_provider import GeminiProvider
    return GeminiProvider(api_key=os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"],
                          model=os.environ.get("JARVIS_AI_MODEL") or "gemini-flash-latest")


_FACTORIES = {"anthropic": _anthropic, "openai": _openai, "local": _local, "gemini": _gemini}


def build_provider(name: str) -> LLMProvider | None:
    factory = _FACTORIES.get(name)
    if not factory:
        return None
    try:
        return factory()
    except KeyError:
        return None


def provider_names() -> list[str]:
    return list(_FACTORIES)
