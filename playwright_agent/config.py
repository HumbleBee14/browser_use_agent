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

# --- Browser ---
MAX_CONCURRENT: int = int(os.getenv("MAX_CONCURRENT", "5"))
HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"

# --- Auth ---
AUTH_STORAGE_STATE: str | None = os.getenv("AUTH_STORAGE_STATE", None)

# --- Paths ---
EVIDENCE_DIR: Path = Path(__file__).parent / "evidence"
TASKS_DIR: Path = Path(__file__).parent / "tasks"
