"""Canonical DB client import path.

Implementation stays in `db.supabase` so existing `from db.supabase import …`
call sites keep working. New code should import from here:

    from core.db import get_supabase_admin, get_supabase_client

SQL migrations live in `db/migrations/` — see `core.paths.MIGRATIONS_DIR`.
"""
from db.supabase import (
    get_supabase_admin,
    get_supabase_client,
    get_supabase_with_jwt,
    is_transient_supabase_error,
    reset_supabase_admin,
    with_supabase_retry,
)

__all__ = [
    "get_supabase_admin",
    "get_supabase_client",
    "get_supabase_with_jwt",
    "is_transient_supabase_error",
    "reset_supabase_admin",
    "with_supabase_retry",
]
