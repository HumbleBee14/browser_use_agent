"""Global configuration — loaded from environment variables.

Single source of truth for API keys, model selection,
and runtime defaults. Uses python-dotenv for .env file support.
Provider-agnostic: supports Anthropic, OpenAI, Google, etc.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


# --- LLM Settings (provider-agnostic) ---
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL: str | None = os.getenv("LLM_MODEL", None)  # None = use provider default

# --- Browser Settings ---
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"
MAX_CONCURRENT: int = int(os.getenv("MAX_CONCURRENT", "3"))

# --- Output Settings ---
EVIDENCE_DIR: Path = Path(os.getenv("EVIDENCE_DIR", "evidence"))
TASKS_DIR: Path = Path(os.getenv("TASKS_DIR", "tasks"))

# --- Agent Defaults ---
DEFAULT_MAX_STEPS: int = 25
DEFAULT_TIMEOUT_SECONDS: int = 120
DEFAULT_MAX_RETRIES: int = 2
