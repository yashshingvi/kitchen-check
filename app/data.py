"""In-memory data store for the POC.

Loads the FSSAI checklist (YAML) and sample establishments (JSON) at import
time. The plan calls for Postgres + PostGIS, but for the POC a small in-memory
index over JSON is enough to exercise every read path and keeps the demo
runnable with one command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .scoring import Checklist

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_checklist() -> Checklist:
    with (DATA_DIR / "checklist.yaml").open() as f:
        raw = yaml.safe_load(f)
    return Checklist(raw)


def _load_establishments() -> dict[str, Any]:
    with (DATA_DIR / "establishments.json").open() as f:
        return json.load(f)


CHECKLIST: Checklist = _load_checklist()
_DATA = _load_establishments()
CITY: str = _DATA["city"]
ESTABLISHMENTS: list[dict[str, Any]] = _DATA["establishments"]

# Pre-built indices for fast lookup.
BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in ESTABLISHMENTS}
BY_SLUG: dict[str, dict[str, Any]] = {e["slug"]: e for e in ESTABLISHMENTS}
