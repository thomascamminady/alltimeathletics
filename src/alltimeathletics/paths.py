"""Shared filesystem paths for the site build."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"
STATIC_SRC = REPO_ROOT / "static"
DATA_SRC = REPO_ROOT / "data"
PARQUET_NAME = "alltime_athletics.parquet"
