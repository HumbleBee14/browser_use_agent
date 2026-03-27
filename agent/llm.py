"""LLM provider factory — clean abstraction over multiple providers.

Supports any LLM that browser-use supports: Anthropic, OpenAI, Google, etc.
Provider selection is config-driven (via .env), never hardcoded.

Usage:
    llm = create_llm()  # Uses LLM_PROVIDER and LLM_MODEL from .env
    llm = create_llm(provider="openai", model="gpt-4o")  # Explicit override
"""

from browser_use.llm.base import BaseChatModel


# Provider registry — maps provider names to their factory functions
def _create_anthropic(model: str, api_key: str, **kwargs) -> BaseChatModel:
    """Create Anthropic LLM via ChatOpenAI with Anthropic base URL.

    browser-use doesn't have a native Anthropic class, but ChatOpenAI
    works with Anthropic's OpenAI-compatible endpoint. We need to:
    - Set dont_force_structured_output=True (Anthropic doesn't support all JSON schema features)
    - Set remove_min_items_from_schema=True (Anthropic rejects 'minimum' in JSON schema)
    - Set remove_defaults_from_schema=True (cleaner schema for Anthropic)
    """
    from browser_use.llm.models import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url="https://api.anthropic.com/v1/",
        max_retries=kwargs.get("max_retries", 3),
        temperature=kwargs.get("temperature", 0.2),
        # Anthropic's OpenAI-compat endpoint doesn't support full JSON schema
        # in response_format. Put schema in system prompt instead.
        add_schema_to_system_prompt=True,
        dont_force_structured_output=True,
        remove_min_items_from_schema=True,
        remove_defaults_from_schema=True,
    )


def _create_openai(model: str, api_key: str, **kwargs) -> BaseChatModel:
    """Create OpenAI LLM."""
    from browser_use.llm.models import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        max_retries=kwargs.get("max_retries", 3),
        temperature=kwargs.get("temperature", 0.2),
    )


def _create_google(model: str, api_key: str, **kwargs) -> BaseChatModel:
    """Create Google Gemini LLM."""
    from browser_use.llm.models import ChatGoogle

    return ChatGoogle(
        model=model,
        api_key=api_key,
    )


def _create_browser_use(model: str, api_key: str, **kwargs) -> BaseChatModel:
    """Create browser-use cloud LLM (their managed proxy)."""
    from browser_use.llm.browser_use.chat import ChatBrowserUse

    return ChatBrowserUse(
        model=model or "bu-latest",
        api_key=api_key,
    )


# Registry: provider name → factory function
PROVIDER_REGISTRY: dict[str, callable] = {
    "anthropic": _create_anthropic,
    "openai": _create_openai,
    "google": _create_google,
    "gemini": _create_google,  # alias
    "browser_use": _create_browser_use,
}


def create_llm(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    **kwargs,
) -> BaseChatModel:
    """Create an LLM instance from config or explicit params.

    Resolution order for each param:
    1. Explicit argument
    2. Environment variable (via config.py)
    3. Default

    Supported providers: anthropic, openai, google/gemini, browser_use
    """
    import config

    # Resolve provider
    provider = provider or getattr(config, "LLM_PROVIDER", "anthropic")
    provider = provider.lower().strip()

    # Resolve model
    model = model or getattr(config, "LLM_MODEL", None)
    if model is None:
        # Provider-specific defaults
        model_defaults = {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
            "google": "gemini-2.0-flash",
            "gemini": "gemini-2.0-flash",
            "browser_use": "bu-latest",
        }
        model = model_defaults.get(provider, "claude-sonnet-4-20250514")

    # Resolve API key
    if api_key is None:
        key_env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
            "gemini": "GOOGLE_API_KEY",
            "browser_use": "BROWSER_USE_API_KEY",
        }
        import os
        env_var = key_env_map.get(provider, "ANTHROPIC_API_KEY")
        api_key = os.getenv(env_var, "")

    if not api_key:
        raise ValueError(
            f"No API key found for provider '{provider}'. "
            f"Set the appropriate key in your .env file."
        )

    # Create via registry
    if provider not in PROVIDER_REGISTRY:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"Unknown LLM provider '{provider}'. Available: {available}"
        )

    factory = PROVIDER_REGISTRY[provider]
    return factory(model=model, api_key=api_key, **kwargs)
