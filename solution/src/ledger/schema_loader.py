from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ledger.config import get_schema_dir


def load_dispositions(schema_dir: Path | None = None) -> list[dict[str, Any]]:
    """Loads dispositions.csv catalogue into a list of dicts with boolean flags."""
    dir_path = schema_dir or get_schema_dir()
    disp_path = dir_path / "dispositions.csv"
    if not disp_path.exists():
        raise FileNotFoundError(f"dispositions.csv not found at {disp_path}")

    records = []
    with open(disp_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "workflow_id": row["workflow_id"].strip(),
                "code": row["code"].strip(),
                "label": row["label"].strip(),
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            })
    return records


def load_workflow_schema(workflow_id: str, schema_dir: Path | None = None) -> dict[str, Any]:
    """Loads <workflow_id>.json schema file."""
    dir_path = schema_dir or get_schema_dir()
    schema_path = dir_path / f"{workflow_id}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found for workflow '{workflow_id}' at {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all_schemas(schema_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Loads all workflow json schemas in schema_dir."""
    dir_path = schema_dir or get_schema_dir()
    schemas = {}
    for json_file in dir_path.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            wf_id = data.get("workflow_id", json_file.stem)
            schemas[wf_id] = data
    return schemas

