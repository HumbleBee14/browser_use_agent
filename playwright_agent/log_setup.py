"""Structured file logging — runs alongside rich console output.

Logs are written to playwright_agent/logs/ with one pair of files per run:
  logs/
  ├── run_2026-03-27_140000.log      ← human-readable, greppable by sample_id
  ├── run_2026-03-27_140000.jsonl    ← machine-parseable JSON lines
  ├── run_2026-03-27_150000.log
  └── run_2026-03-27_150000.jsonl

Console output is NOT affected — rich.Console owns that separately.
If init_logging() is never called (e.g. in tests), all log calls are silently dropped.

Usage:
    from log_setup import init_logging, logger
    init_logging(evidence_dir)            # derives run name from dir
    log = logger.bind(sample_id="torvalds")
    log.info("Agent started")
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

import config

# Remove default stderr sink — rich.Console owns all console output
logger.remove()

# Default context so format strings never KeyError on missing sample_id
logger.configure(extra={"sample_id": "system"})

_initialized = False


def init_logging(evidence_dir: Path, level: str = "DEBUG") -> None:
    """Initialize file logging for one run. Idempotent.

    Derives the run name from the evidence directory:
      evidence/run_2026-03-27_140000  →  run_2026-03-27_140000.log
      evidence/                       →  run_{timestamp}.log  (fallback)
    """
    global _initialized
    if _initialized:
        return

    logs_dir = config.LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Derive a unique run name from the evidence directory
    run_name = evidence_dir.name
    if not run_name.startswith("run_"):
        run_name = f"run_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"

    # Human-readable text log
    logger.add(
        logs_dir / f"{run_name}.log",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{extra[sample_id]:>20} | {message}"
        ),
        level=level,
        encoding="utf-8",
    )

    # Machine-readable JSON lines (every field including all extras)
    logger.add(
        logs_dir / f"{run_name}.jsonl",
        serialize=True,
        level=level,
        encoding="utf-8",
    )

    _initialized = True
    logger.info(f"Logging initialized → {logs_dir / run_name}")
