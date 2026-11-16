"""Project paths. Tests point these at a temporary directory."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "workflows"
DOCS = ROOT / "docs"
IMAGES = DOCS / "images"
WORKFLOW_DOCS = DOCS / "workflows"
REPORTS = ROOT / "reports"
