"""CSV writer — buffered batch write for master results.

Writes all sample results to a single CSV in one pass after the
batch completes. No per-row append, no concurrency issues.

ADL-5: Deterministic output. Same results in = same CSV out.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from models.evidence import SampleResult
from models.task import FieldSpec


class CSVWriter:
    """Buffered CSV writer — writes all results in one pass."""

    def __init__(self, csv_path: Path, field_specs: list[FieldSpec]):
        self.csv_path = csv_path
        self.field_names = [f.name for f in field_specs]
        self.columns = ["sample_id", "status"] + self.field_names

    def write_batch(self, results: list[SampleResult]) -> Path:
        """Write all results to CSV in one operation.

        Returns the path to the written CSV file.
        """
        rows = []
        for r in results:
            row: dict[str, object] = {
                "sample_id": r.sample_id,
                "status": r.status.value,
            }
            # Map extracted fields by name
            for field in r.extracted_fields:
                if field.field_name in self.field_names:
                    row[field.field_name] = field.value
            rows.append(row)

        df = pd.DataFrame(rows, columns=self.columns)
        df.to_csv(self.csv_path, index=False)
        return self.csv_path
