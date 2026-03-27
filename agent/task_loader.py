"""Task loader — reads YAML task definitions and input CSV/JSON files.

Converts YAML configs into validated TaskConfig objects and
input files into lists of SampleInput objects.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from models.task import SampleInput, TaskConfig


def load_task_config(task_path: str | Path) -> TaskConfig:
    """Load and validate a task definition from YAML."""
    path = Path(task_path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return TaskConfig(**raw)


def load_samples(
    input_file: str | Path,
    input_columns: list[str],
) -> list[SampleInput]:
    """Load sample inputs from CSV or JSON file.

    Expects at minimum a 'sample_id' column. 'url' is optional.
    Any extra columns become extra_fields.
    """
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    if path.suffix == ".json":
        df = pd.read_json(path)
    else:
        df = pd.read_csv(path)

    # Validate required columns exist
    missing = [c for c in input_columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"Input file missing required columns: {missing}. "
            f"Found: {list(df.columns)}"
        )

    samples = []
    known_fields = {"sample_id", "url", "task_type"}
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        extra = {
            k: v for k, v in row_dict.items()
            if k not in known_fields and pd.notna(v)
        }
        samples.append(
            SampleInput(
                sample_id=str(row_dict.get("sample_id", "")),
                url=row_dict.get("url"),
                task_type=row_dict.get("task_type"),
                extra_fields=extra,
            )
        )

    return samples
