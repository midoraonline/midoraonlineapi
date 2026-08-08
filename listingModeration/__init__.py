"""Listing moderation module.

Vercel-native design (no BackgroundTasks, no local SQLite, no bundled ML weights):

    POST /api/v1/moderation/listings        -> enqueue (202, returns instantly)
    GET  /api/v1/moderation/listings/{id}   -> poll status
    GET  /api/v1/moderation/listings?status=needs_review
    GET  /api/v1/moderation/drain           -> Vercel Cron trigger (Bearer CRON_SECRET)
    POST /api/v1/moderation/drain           -> Supabase pg_cron trigger

Pipeline (cheapest first, short-circuits on the first hit):
    1. Banned-keyword deny-list (free)
    2. Perceptual-hash blocklist (Pillow only, no numpy)
    3. Gemini text moderation   (replaces Detoxify)
    4. Gemini image moderation  (replaces NudeNet)
"""
from .router import router as router  # re-export

__all__ = ["router"]
