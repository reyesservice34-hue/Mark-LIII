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
                             model=os.environ.get("JARVIS_AI_MODEL") or "claude-opus-5",
                             workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip())


def _openai():
    from .openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        provider_id="openai",
        base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com",
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("JARVIS_AI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1")


_CHAIN_DEFS = {
    "groq": ("Groq", "https://api.groq.com/openai", "GROQ_API_KEY", "JARVIS_GROQ_MODEL", "openai/gpt-oss-120b"),
    "together": ("Together", "https://api.together.xyz", "TOGETHER_API_KEY", "JARVIS_TOGETHER_MODEL",
                 "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY", "JARVIS_DEEPSEEK_MODEL", "deepseek-flash"),
}


def _chain():
    from .chain import ChainProvider
    from .openai_compat import OpenAICompatProvider
    provs, labels = [], []
    for name in os.environ.get("JARVIS_CHAIN", "").replace(" ", "").split(","):
        d = _CHAIN_DEFS.get(name)
        if not d or not os.environ.get(d[2]):
            continue
        prov = OpenAICompatProvider(provider_id=name, base_url=d[1], api_key=os.environ[d[2]],
                                    model=os.environ.get(d[3]) or d[4])
        prov.include_usage = True
        provs.append(prov)
        labels.append(d[0])
    if not provs:
        raise KeyError("JARVIS_CHAIN")
    return ChainProvider(provs, labels)


def _local():
    if os.environ.get("JARVIS_CHAIN", "").strip():
        return _chain()
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
