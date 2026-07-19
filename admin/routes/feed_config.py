"""Admin API — feed scoring / placement configuration.

Backed by the `feed_config` table (see migration 028). Overrides are merged
on top of the code-level defaults in `feed.config` and picked up by the
runtime engine every 60 seconds (or on-demand via the tester endpoint).

Endpoints:
  GET  /admin/feed/config               → { defaults, current, applied }
  PUT  /admin/feed/config               → replace override map, apply, cache-bust
  POST /admin/feed/config/reset         → clear all overrides
  POST /admin/feed/config/test          → score a sample of products with
                                           the given overrides (dry-run) and
                                           return a component breakdown.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from db.supabase import get_supabase_admin
from feed import config as feed_config
from feed import scoring as feed_scoring
from feed import signals as feed_signals

logger = logging.getLogger(__name__)

router = APIRouter()


def _sanitize(overrides: dict[str, Any]) -> dict[str, Any]:
    """Keep only whitelisted keys, coerce to defaults' types, drop None."""
    out: dict[str, Any] = {}
    defaults = feed_config.get_defaults()
    for key in feed_config.OVERRIDABLE_KEYS:
        if key not in overrides:
            continue
        value = overrides[key]
        if value is None:
            continue
        default = defaults.get(key)
        try:
            if isinstance(default, bool):
                out[key] = bool(value)
            elif isinstance(default, int) and not isinstance(default, bool):
                out[key] = int(value)
            elif isinstance(default, float):
                out[key] = float(value)
            else:
                out[key] = value
        except (TypeError, ValueError):
            continue
    return out


def _current_row() -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("feed_config")
            .select("overrides, updated_at, updated_by")
            .eq("key", "default")
            .maybe_single()
            .execute()
        )
        return getattr(r, "data", None) or {}
    except Exception as exc:
        logger.warning("feed_config read failed: %s", exc)
        return {}


@router.get("/feed/config")
def get_feed_config() -> dict[str, Any]:
    """Return the defaults, currently-stored overrides, and applied values."""
    feed_config.refresh_from_db(force=True)
    row = _current_row()
    return {
        "defaults": feed_config.get_defaults(),
        "overrides": (row.get("overrides") or {}),
        "applied": feed_config.get_current_overrides(),
        "updated_at": row.get("updated_at"),
        "overridable_keys": list(feed_config.OVERRIDABLE_KEYS),
    }


@router.put("/feed/config")
def put_feed_config(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Replace the override map. Non-whitelisted keys are silently dropped."""
    overrides = _sanitize(body.get("overrides") or body)
    admin = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()
    try:
        admin.table("feed_config").upsert(
            {"key": "default", "overrides": overrides, "updated_at": now},
            on_conflict="key",
        ).execute()
    except Exception as exc:
        logger.error("feed_config upsert failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save overrides")
    feed_config.invalidate_cache()
    feed_config.refresh_from_db(force=True)
    return {
        "ok": True,
        "overrides": overrides,
        "applied": feed_config.get_current_overrides(),
    }


@router.post("/feed/config/reset")
def reset_feed_config() -> dict[str, Any]:
    """Wipe all overrides and revert to code defaults."""
    admin = get_supabase_admin()
    try:
        admin.table("feed_config").upsert(
            {"key": "default", "overrides": {}},
            on_conflict="key",
        ).execute()
    except Exception as exc:
        logger.error("feed_config reset failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reset overrides")
    feed_config.invalidate_cache()
    feed_config.refresh_from_db(force=True)
    return {"ok": True, "applied": feed_config.get_current_overrides()}


@router.post("/feed/config/test")
def test_feed_config(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Dry-run: apply overrides to an in-memory snapshot of feed.config,
    score up to `sample_size` products (default 25), then restore the
    previously-applied overrides. Returns per-product component scores plus
    an aggregate delta vs. the previous configuration."""
    sample_size = int(body.get("sample_size") or 25)
    sample_size = max(1, min(sample_size, 100))
    overrides = _sanitize(body.get("overrides") or {})

    # Snapshot the currently-applied overrides so we can restore.
    previous_overrides = feed_config.get_current_overrides()

    admin = get_supabase_admin()
    # Pull a small sample of published products with all fields scoring needs.
    try:
        r = (
            admin.table("products")
            .select(
                "id, shop_id, title, description, category, view_count, "
                "like_count, price_ugx, embedding, created_at"
            )
            .eq("is_published", True)
            .order("view_count", desc=True)
            .limit(sample_size)
            .execute()
        )
        products = r.data or []
    except Exception as exc:
        logger.warning("feed_config test products fetch failed: %s", exc)
        products = []

    if not products:
        return {"sample_size": 0, "results": [], "summary": {}}

    shop_ids = list({str(p.get("shop_id", "")) for p in products if p.get("shop_id")})
    try:
        shop_meta = feed_signals.collect_shop_meta(admin, shop_ids)
    except Exception:
        shop_meta = {}

    # Empty per-user signals — this is a global "how would this rank" test.
    signals = feed_signals.empty_signals()

    def _score_all() -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        out = []
        for p in products:
            components = {
                "taste": 0.0,
                "category": feed_scoring.category_match_fallback(p, signals, False),
                "search": feed_scoring.search_match(p, signals),
                "followed": feed_scoring.followed_shop(p, signals),
                "freshness": feed_scoring.freshness_bonus(p, now),
                "velocity": feed_scoring.velocity_bonus(p, {}),
                "new_seller": feed_scoring.new_seller_bonus(p, shop_meta, now),
                "seller_quality": feed_scoring.seller_quality_bonus(p, shop_meta),
                "global_popularity": feed_scoring.global_popularity(p),
                "premium_store": feed_scoring.premium_store_bonus(p, shop_meta, now),
                "boost": feed_scoring.boost_bonus(p, {}),
                "seen": feed_scoring.seen_penalty(p, signals),
                "fraud": feed_scoring.fraud_penalty(p, {}) or 0.0,
            }
            total = sum(v for v in components.values())
            out.append({
                "id": str(p.get("id")),
                "title": p.get("title") or "",
                "shop_id": str(p.get("shop_id") or ""),
                "components": components,
                "total": total,
            })
        return out

    # 1. baseline (previous overrides)
    feed_config.apply_overrides(previous_overrides)
    baseline = _score_all()
    baseline_by_id = {r["id"]: r["total"] for r in baseline}

    # 2. proposed (new overrides)
    feed_config.apply_overrides(overrides)
    proposed = _score_all()

    # 3. restore previous state
    feed_config.apply_overrides(previous_overrides)

    # Merge for response
    for row in proposed:
        row["baseline_total"] = baseline_by_id.get(row["id"], 0.0)
        row["delta"] = row["total"] - row["baseline_total"]

    proposed.sort(key=lambda r: r["total"], reverse=True)

    summary = {
        "sample_size": len(proposed),
        "avg_score": (sum(r["total"] for r in proposed) / len(proposed)) if proposed else 0.0,
        "avg_baseline": (sum(r["baseline_total"] for r in proposed) / len(proposed)) if proposed else 0.0,
        "max_delta": max((r["delta"] for r in proposed), default=0.0),
        "min_delta": min((r["delta"] for r in proposed), default=0.0),
    }
    return {"sample_size": len(proposed), "results": proposed, "summary": summary}
