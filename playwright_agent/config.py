"""Configuration — single source of truth for all settings.

Loaded from environment variables (.env file via python-dotenv).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from playwright_agent root
load_dotenv(Path(__file__).parent / ".env")

# --- LLM ---
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_FAST_MODEL: str = os.getenv("LLM_FAST_MODEL", "claude-haiku-4-5")

# Known context windows per model family (tokens).
# Used to derive a safe prompt budget as a fraction of total capacity.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-opus-4": 200_000,
}
LLM_CONTEXT_WINDOW: int = int(os.getenv(
    "LLM_CONTEXT_WINDOW",
    str(MODEL_CONTEXT_WINDOWS.get(LLM_MODEL, 200_000)),
))

# --- Browser ---
MAX_CONCURRENT: int = int(os.getenv("MAX_CONCURRENT", "5"))
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"

# --- Auth ---
AUTH_STORAGE_STATE: str | None = os.getenv("AUTH_STORAGE_STATE", None)

# --- Paths ---
EVIDENCE_DIR: Path = Path(__file__).parent / "evidence"
TASKS_DIR: Path = Path(__file__).parent / "tasks"
LOGS_DIR: Path = Path(__file__).parent / "logs"
# Memory is run-scoped — stored in evidence/run_XXXX/memory/ (no global folder)
MAX_PATTERNS_PER_DOMAIN: int = 5

# --- Agent behavior ---
# Default "light" saves tool-schema + prompt tokens; use "full" for long-horizon / hard tasks.
REFLECTION_MODE: str = os.getenv("REFLECTION_MODE", "light")  # "full" or "light"
FINALIZE_ON_FAILURE: bool = os.getenv("FINALIZE_ON_FAILURE", "true").lower() == "true"
# After successful runs, optional Haiku distillation into evidence/run_XXXX/memory/ (latency + tiny cost).
ENABLE_MEMORY_DISTILLATION: bool = os.getenv("ENABLE_MEMORY_DISTILLATION", "true").lower() == "true"
ENABLE_FALLBACK_LLM: bool = os.getenv("ENABLE_FALLBACK_LLM", "false").lower() == "true"
FALLBACK_LLM_MODEL: str = os.getenv("FALLBACK_LLM_MODEL", "claude-haiku-4-5")
ENABLE_MULTI_ACTIONS: bool = os.getenv("ENABLE_MULTI_ACTIONS", "false").lower() == "true"
MAX_ACTIONS_PER_STEP: int = int(os.getenv("MAX_ACTIONS_PER_STEP", "3"))
