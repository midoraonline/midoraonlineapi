"""Filesystem locations for core infrastructure (config, SQL migrations)."""
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
API_ROOT = CORE_DIR.parent
MIGRATIONS_DIR = API_ROOT / "db" / "migrations"
