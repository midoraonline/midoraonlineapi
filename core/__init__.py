"""Core infrastructure: config, schemas, DB client, security, runtime.

    core.config      — env-backed Settings
    core.schemas     — shared pagination envelopes
    core.db          — Supabase client (admin / request-scoped)
    core.paths       — MIGRATIONS_DIR → db/migrations/
    core.runtime     — serverless detection

Feature modules (shop, listingModeration, …) live beside this package
and register themselves through `common.module.AppModule`.
"""
