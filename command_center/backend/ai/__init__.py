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


def _model_for(provider_name: str, specific_env: str, default: str) -> str:
    """JARVIS_AI_MODEL names a model for whichever provider JARVIS_AI_PROVIDER
    actually selects. Applying it unconditionally to every provider factory
    means a model tag meant for one provider (e.g. a local Ollama tag) leaks
    into every other provider's own health check and gets looked up on a
    service that never heard of it — see the Gemini integration card 404'ing
    on a qwen2.5:7b "model" while JARVIS_AI_PROVIDER=local. Only the active
    provider gets the JARVIS_AI_MODEL override; everyone else falls back to
    their own env var or hardcoded default."""
    if os.environ.get("JARVIS_AI_PROVIDER", "").strip().lower() == provider_name:
        override = os.environ.get("JARVIS_AI_MODEL", "").strip()
        if override:
            return override
    return os.environ.get(specific_env, "").strip() or default


def _anthropic():
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"],
                             model=_model_for("anthropic", "ANTHROPIC_MODEL", "claude-opus-5"),
                             workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip())


def _openai():
    from .openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        provider_id="openai",
        base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com",
        api_key=os.environ["OPENAI_API_KEY"],
        model=_model_for("openai", "OPENAI_MODEL", "gpt-4.1"))


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
        model=_model_for("local", "LOCAL_LLM_MODEL", "llama3.2"))


def _gemini():
    from .gemini_provider import GeminiProvider
    return GeminiProvider(api_key=os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"],
                          model=_model_for("gemini", "GEMINI_MODEL", "gemini-flash-latest"))


def _freellm():
    """freellmapi router (unified key, /v1 proxy over free providers), with the
    local Ollama as last-resort fallback when LOCAL_LLM_URL is configured."""
    from .chain import ChainProvider
    from .openai_compat import OpenAICompatProvider
    url = os.environ.get("FREELLM_URL", "").strip()
    key = os.environ.get("FREELLM_API_KEY", "").strip()
    # Missing and empty both mean "not configured" -> build_provider returns None.
    if not url:
        raise KeyError("FREELLM_URL")
    if not key:
        raise KeyError("FREELLM_API_KEY")
    primary = OpenAICompatProvider(
        provider_id="freellm",
        base_url=url,
        api_key=key,
        model=_model_for("freellm", "FREELLM_MODEL", "auto:smart"))
    primary.include_usage = True
    return _with_fallbacks(primary, "freellmapi")


def _omniroute():
    """OmniRoute gateway (own key, /v1 proxy), same paid/local fallbacks as freellm.
    Model comes only from OMNIROUTE_MODEL: JARVIS_AI_MODEL is a local Ollama tag here."""
    from .openai_compat import OpenAICompatProvider
    url = os.environ.get("OMNIROUTE_URL", "").strip()
    key = os.environ.get("OMNIROUTE_API_KEY", "").strip()
    model = os.environ.get("OMNIROUTE_MODEL", "").strip()
    # Missing and empty all mean "not configured" -> build_provider returns None.
    if not url:
        raise KeyError("OMNIROUTE_URL")
    if not key:
        raise KeyError("OMNIROUTE_API_KEY")
    if not model:
        raise KeyError("OMNIROUTE_MODEL")
    primary = OpenAICompatProvider(provider_id="omniroute", base_url=url, api_key=key, model=model)
    primary.include_usage = True
    # Ersatzmodelle über denselben Zugang (OMNIROUTE_FALLBACK_MODELS, kommagetrennt), z. B. wenn
    # DuckDuckGo-Haiku gesperrt ist; sie kommen vor allen anderen Rückfällen.
    extra = []
    for m in os.environ.get("OMNIROUTE_FALLBACK_MODELS", "").replace(" ", "").split(","):
        if m and m != model:
            prov = OpenAICompatProvider(provider_id="omniroute", base_url=url, api_key=key, model=m)
            prov.include_usage = True
            extra.append((prov, "OmniRoute " + m.rsplit("/", 1)[-1]))
    # Bezahlter, zuverlässiger Ersatz direkt hinter dem Hauptmodell (MIA_TEXT_OPENAI_FALLBACK_MODEL): springt nur
    # ein, wenn das Gratis-Hauptmodell gesperrt ist; kostet dann OpenAI-Guthaben.
    oa_model = os.environ.get("MIA_TEXT_OPENAI_FALLBACK_MODEL", "").strip()
    oa_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if oa_model and oa_key:
        oa = OpenAICompatProvider(provider_id="openai", base_url="https://api.openai.com", api_key=oa_key, model=oa_model)
        oa.include_usage = True
        extra.insert(0, (oa, "OpenAI " + oa_model))
    # Groq (offizielle Gratis-Stufe, sehr schnell): MIA_GROQ_MODELS kommagetrennt; MIA_GROQ_FIRST=1 stellt
    # Groq vor OmniRoute. Zu große Anfragen (Tokens/Minute) beantwortet Groq mit 413/429, die Kette geht weiter.
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    groq = []
    for m in os.environ.get("MIA_GROQ_MODELS", "").replace(" ", "").split(","):
        if m and groq_key:
            prov = OpenAICompatProvider(provider_id="groq", base_url="https://api.groq.com/openai/v1",
                                        api_key=groq_key, model=m)
            prov.include_usage = True
            groq.append((prov, "Groq " + m.rsplit("/", 1)[-1]))
    if groq and os.environ.get("MIA_GROQ_FIRST", "0").strip() == "1":
        first, first_label = groq[0]
        return _with_fallbacks(first, first_label, groq[1:] + [(primary, "OmniRoute")] + extra)
    if os.environ.get("MIA_TEXT_OPENAI_FIRST", "0").strip() == "1" and extra and extra[0][1].startswith("OpenAI"):
        # OpenAI zuerst (zuverlässiger, ehrlicher bei Werkzeugen), Haiku über OmniRoute als erster Ersatz.
        first, first_label = extra[0]
        return _with_fallbacks(first, first_label, [(primary, "OmniRoute")] + extra[1:] + groq)
    return _with_fallbacks(primary, "OmniRoute", extra + groq)


def _claude_chain(model: str):
    """Claude direkt (Anthropic-Key), dahinter OmniRoute und das lokale Modell als Reserve."""
    from .anthropic_provider import AnthropicProvider
    from .chain import ChainProvider
    from .openai_compat import OpenAICompatProvider
    provs = [AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"], model=model,
                               workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip())]
    labels = ["Claude"]
    url = os.environ.get("OMNIROUTE_URL", "").strip()
    key = os.environ.get("OMNIROUTE_API_KEY", "").strip()
    omodel = os.environ.get("OMNIROUTE_MODEL", "").strip()
    if url and key and omodel:
        provs.append(OpenAICompatProvider(provider_id="omniroute", base_url=url, api_key=key, model=omodel))
        labels.append("OmniRoute")
    if os.environ.get("LOCAL_LLM_URL") and _chat_local_fallback():
        provs.append(OpenAICompatProvider(
            provider_id="local", base_url=os.environ["LOCAL_LLM_URL"],
            api_key=os.environ.get("LOCAL_LLM_API_KEY", ""),
            model=os.environ.get("LOCAL_LLM_MODEL", "").strip() or "llama3.2"))
        labels.append("Ollama")
    return ChainProvider(provs, labels)


def _claude():
    """Gründliches Modell (Standard Sonnet); das schnelle Haiku baut die Laufzeit mit _claude_chain."""
    return _claude_chain(os.environ.get("CLAUDE_DEEP_MODEL", "").strip() or "claude-sonnet-5-5")


def _chat_local_fallback() -> bool:
    """MIA_CHAT_LOCAL_FALLBACK=0: Ollama nicht als Chat-Ersatz. Gedächtnis (chat_retention, maillearn)
    nutzt LOCAL_LLM_URL weiter; ein zu langsames lokales Modell ließe MIA minutenlang hängen."""
    return os.environ.get("MIA_CHAT_LOCAL_FALLBACK", "1").strip() != "0"


def _with_fallbacks(primary, label, extra=()):
    from .chain import ChainProvider
    from .openai_compat import OpenAICompatProvider
    provs, labels = [primary], [label]
    for prov, lab in extra:
        provs.append(prov)
        labels.append(lab)
    gem_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    for gem_model in os.environ.get("MIA_GEMINI_FALLBACK_MODEL", "").replace(" ", "").split(","):
        if gem_model and gem_key:
            # Anderer Anbieter als das Gateway: fällt DuckDuckGo/OmniRoute ganz aus, antwortet Google.
            # Mehrere Modelle (kommagetrennt), weil Google einzelne Modelle zeitweise mit 503 abweist.
            from .gemini_provider import GeminiProvider
            provs.append(GeminiProvider(api_key=gem_key, model=gem_model))
            labels.append("Gemini " + gem_model)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if os.environ.get("MIA_ANTHROPIC_FALLBACK", "1").strip() == "0":
        anthropic_key = ""  # kein bezahlter Fallback: nur Gateway und lokales Modell
    if anthropic_key:
        # Reliable paid fallback between the free router and the weak local model. Own env var, not
        # _model_for("anthropic", ...): the default there is the big Opus model, which would be costly here.
        from .anthropic_provider import AnthropicProvider
        provs.append(AnthropicProvider(
            api_key=anthropic_key,
            model=os.environ.get("ANTHROPIC_FALLBACK_MODEL", "").strip() or "claude-haiku-4-5-20251001",
            workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()))
        labels.append("Haiku")
    if os.environ.get("LOCAL_LLM_URL") and _chat_local_fallback():
        # Deliberately not _model_for("local", ...): with JARVIS_AI_PROVIDER=freellm
        # the JARVIS_AI_MODEL override belongs to the primary only.
        provs.append(OpenAICompatProvider(
            provider_id="local",
            base_url=os.environ["LOCAL_LLM_URL"],
            api_key=os.environ.get("LOCAL_LLM_API_KEY", ""),
            model=os.environ.get("LOCAL_LLM_MODEL", "").strip() or "llama3.2"))
        labels.append("Ollama")
    return ChainProvider(provs, labels)


def _voice():
    """Sprachchat (MIA_VOICE_PROVIDER=voice): OpenAI direkt (schnell, MIA_VOICE_OPENAI_MODEL), dann freellmapi,
    dann die Gemini-Rückfälle aus _with_fallbacks. Gedächtnis und Werkzeuge sind dieselben wie im Text-Chat."""
    from .openai_compat import OpenAICompatProvider
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise KeyError("OPENAI_API_KEY")
    primary = OpenAICompatProvider(provider_id="openai", base_url="https://api.openai.com", api_key=key,
                                   model=os.environ.get("MIA_VOICE_OPENAI_MODEL", "").strip() or "gpt-4.1-mini")
    primary.include_usage = True
    extra = []
    url, fkey = os.environ.get("FREELLM_URL", "").strip(), os.environ.get("FREELLM_API_KEY", "").strip()
    if url and fkey:
        prov = OpenAICompatProvider(provider_id="freellm", base_url=url, api_key=fkey,
                                    model=os.environ.get("FREELLM_MODEL", "").strip() or "auto:smart")
        prov.include_usage = True
        extra.append((prov, "freellmapi"))
    return _with_fallbacks(primary, "OpenAI", extra)


_FACTORIES = {"anthropic": _anthropic, "openai": _openai, "local": _local, "gemini": _gemini,
              "freellm": _freellm, "omniroute": _omniroute, "claude": _claude, "voice": _voice}


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
