from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import cache
from db.supabase import Client, get_supabase_admin, with_supabase_retry
from feed import boost_slots, expiry, impressions, location, placement, scoring, sessions, snapshots, trending
from feed import config as C
from feed import signals as sig
from feed.catalog import PAID_BADGES, ListingFilters, card_rating, fetch_catalog_page, matching_ids, rating_map
from feed.quality import listing_quality
from feed.stages import LANES, StagePlan, lane_of, plan_for
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

HOME_PAGE_SIZE = 40

_PRODUCT_CARD_SELECT = (
    "id,shop_id,title,description,category,item_type,price_ugx,discount_price,"
    "discount_expires_at,stock_quantity,image_urls,is_published,status,listing_score,"
    "location_name,created_at,first_posted_at,view_count,listing_meta,is_negotiable"
)


@dataclass
class FeedPage:
    items: list[ProductResponse]
    has_more: bool
    total: int | None = None
    version: str | None = None
    placements: dict[str, str] = field(default_factory=dict)
    trending: set[str] = field(default_factory=set)
    reasons: dict[str, str] = field(default_factory=dict)
    location_reasons: dict[str, str] = field(default_factory=dict)
    session: str | None = None


def _to_response(product: dict[str, Any]) -> ProductResponse:
    from shop.locations import listing_is_online
    from shop.serializers import clip_card_description

    stripped = {k: v for k, v in product.items() if k not in ("embedding", "embedding_source_hash")}
    stripped["description"] = clip_card_description(stripped.get("description"))
    stripped["is_online"] = listing_is_online(stripped.get("location_name"), stripped.get("listing_meta"))
    return ProductResponse(**stripped)


def _with_ratings(client: Client, cards: list[ProductResponse]) -> list[ProductResponse]:
    if not cards:
        return cards
    ratings = rating_map(client, [card.id for card in cards if card.id])
    out: list[ProductResponse] = []
    for card in cards:
        average, count = card_rating(ratings, card.id)
        out.append(card.model_copy(update={"average_rating": average, "review_count": count}))
    return out


def _fetch_products_by_ids(client: Client, product_ids: list[str]) -> list[dict[str, Any]]:
    if not product_ids:
        return []

    def _run():
        return (
            client.table("products")
            .select(_PRODUCT_CARD_SELECT)
            .eq("status", "active")
            .eq("is_published", True)
            .in_("id", product_ids)
            .execute()
        )

    try:
        rows = with_supabase_retry(_run, label="hydrate_cards").data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed card fetch failed: %s", exc)
        return []
    by_id = {str(row.get("id")): row for row in rows if row.get("id")}
    return [by_id[pid] for pid in product_ids if pid in by_id]


def _cards(client: Client, rows: list[dict[str, Any]]) -> list[ProductResponse]:
    return _with_ratings(client, [_to_response(row) for row in expiry.drop_expired(rows)])


def resolve_category_filter_labels(category: str | None) -> list[str] | None:
    if not category or not str(category).strip():
        return None
    raw = str(category).strip()
    try:
        from core.categories import ALL_CATEGORIES, normalize_category, subcategories_for_parent
    except Exception:  # noqa: BLE001
        return [raw]
    try:
        label = normalize_category(raw)
    except ValueError:
        lowered = raw.lower()
        label = next(
            (c.label for c in ALL_CATEGORIES if c.label.lower() == lowered or c.slug.lower() == lowered),
            None,
        )
        if label is None:
            return [raw]
    if not label:
        return None
    children = subcategories_for_parent(label)
    return [label, *children] if children else [label]


def _ranking_rows(db: Client) -> tuple[list[dict[str, Any]], int]:
    def _run():
        return (
            db.table("products")
            .select(_PRODUCT_CARD_SELECT, count="exact")
            .eq("status", "active")
            .eq("is_published", True)
            .order("created_at", desc=True)
            .limit(C.RANKING_FETCH_LIMIT)
            .execute()
        )

    resp = with_supabase_retry(_run, label="feed_rank_rows")
    rows = expiry.drop_expired([r for r in (resp.data or []) if r.get("id")])
    count = getattr(resp, "count", None)
    return rows, int(count) if count is not None else len(rows)


def build_snapshot(db: Client) -> snapshots.Snapshot:
    rows, total = _ranking_rows(db)
    by_shop = {str(r["id"]): str(r.get("shop_id") or "") for r in rows}
    meta = sig.collect_shop_meta(db, list({s for s in by_shop.values() if s}))
    fraud = sig.collect_fraud_severity(db, list(by_shop), by_shop, meta)
    seen = impressions.listing_impressions(list(by_shop), hours=C.EXPOSURE_WINDOW_HOURS)

    eligible: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        pid = str(row["id"])
        if meta.get(by_shop[pid], {}).get("is_active") is False:
            continue
        if fraud.get(pid, "low") in scoring.HIDDEN_FRAUD:
            continue
        quality = listing_quality(row)
        if quality >= C.QUALITY_MIN:
            eligible.append((row, quality))

    by_lane = {lane: 0 for lane in LANES}
    for row, _ in eligible:
        by_lane[lane_of(row.get("item_type"))] += 1
    plan = plan_for(by_lane, eligible=total if total > len(rows) else len(eligible))

    now = datetime.now(timezone.utc)
    entries = []
    for row, quality in eligible:
        pid = str(row["id"])
        shop = by_shop[pid]
        parts = scoring.components(
            row,
            quality=quality,
            shop_meta=meta.get(shop),
            impressions=seen.get(pid, 0),
            eligible=plan.eligible,
            stage=plan.stage,
            now=now,
        )
        penalty = C.FRAUD_MEDIUM_PENALTY if fraud.get(pid, "low") == "medium" else 0.0
        entries.append({
            "id": pid,
            "shop": shop,
            "lane": lane_of(row.get("item_type")),
            "cat": (row.get("category") or "").strip().lower(),
            "score": round(sum(parts.values()) - penalty, 3),
            "parts": {k: round(v, 2) for k, v in parts.items() if v},
            "quality": quality,
            "fresh": scoring.freshness(row, now),
            "impressions": seen.get(pid, 0),
            "posted": row.get("created_at"),
            "geo": location.geo_of(row),
        })
    items = [
        {k: e[k] for k in ("id", "shop", "lane", "cat", "score", "parts", "quality", "fresh", "impressions", "posted", "geo", "mode") if e.get(k) not in (None, {}, "")}
        | ({"reason": e["reason"]} if e.get("reason") else {})
        for e in placement.place(entries, plan)
    ]
    hot = trending.trending_ids(
        db,
        [
            {"id": str(row["id"]), "lane": lane_of(row.get("item_type")),
             "posted_at": row.get("first_posted_at") or row.get("created_at")}
            for row, _ in eligible
        ],
        now,
    ) if plan.stage >= C.TRENDING_MIN_STAGE else set()
    for item in items:
        if item["id"] in hot:
            item["trending"] = True
    return snapshots.Snapshot(version=uuid.uuid4().hex[:12], plan=plan, items=items, config=C.version())


_REBUILD_LOCK = cache.key("feed", "snap", "rebuilding")
_rebuild_guard = threading.Lock()


def current_snapshot(db: Client) -> snapshots.Snapshot:
    snap = snapshots.current(db)
    if snap is not None:
        return snap
    # One rebuild at a time across threads and workers; everyone else keeps serving the last order.
    if _rebuild_guard.acquire(blocking=False):
        try:
            if cache.acquire_lock(_REBUILD_LOCK, ttl_seconds=60):
                try:
                    return snapshots.current(db) or _rebuild(db)
                finally:
                    cache.release_lock(_REBUILD_LOCK)
        finally:
            _rebuild_guard.release()
    stale = snapshots.latest(db)
    if stale is not None:
        return stale
    with _rebuild_guard:
        return snapshots.current(db) or _rebuild(db)


def _rebuild(db: Client) -> snapshots.Snapshot:
    started = time.perf_counter()
    C.refresh_from_db()
    expiry.close_expired(db)
    snap = build_snapshot(db)
    snapshots.save(db, snap)
    logger.info(
        "feed:rebuild stage=%s eligible=%s ranked=%s ms=%.1f",
        snap.plan.stage, snap.plan.eligible, len(snap.items), (time.perf_counter() - started) * 1000,
    )
    return snap


def current_plan(db: Client) -> StagePlan:
    return current_snapshot(db).plan


def rebuild_ranking() -> dict[str, int]:
    db = get_supabase_admin()
    C.refresh_from_db(force=True)
    snapshots.clear_memory()
    expiry.close_expired(db)
    snap = build_snapshot(db)
    snapshots.save(db, snap)
    return {"ranked": len(snap.items), "stage": snap.plan.stage, "eligible": snap.plan.eligible}


def _home_page(
    db: Client,
    *,
    user_id: str | None,
    session_id: str | None,
    page: int,
    limit: int,
    version: str | None,
    exclude_ids: list[str] | None,
) -> FeedPage:
    snap = (snapshots.by_version(db, version) if version else None) or current_snapshot(db)
    items = snap.items
    if C.FOLLOWED_SHOP_LIFT:
        items = placement.lift_followed(items, sig.followed_shop_ids(db, user_id))
    modes = {i["id"]: i["mode"] for i in items}
    hidden = set(exclude_ids or [])
    lanes = boost_slots.paid_lanes(db, snap.plan)
    ids = [i["id"] for i in items if i["id"] not in hidden]
    lanes = [boost_slots.PaidLane(lane.placement, [b for b in lane.seq if b not in hidden], lane.phase) for lane in lanes]
    page_ids, has_more, paid = boost_slots.compose_paid(ids, lanes, page=1 if exclude_ids else page, limit=limit)
    rows = _fetch_products_by_ids(db, page_ids)
    return FeedPage(
        items=_cards(db, rows),
        has_more=has_more,
        version=snap.version,
        placements={str(r["id"]): paid.get(str(r["id"])) or modes.get(str(r["id"]), "organic") for r in rows},
        trending=snap.trending(),
    )


def _pool_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "shop": item.get("shop") or "",
        "lane": item.get("lane") or "product",
        "cat": item.get("cat") or "",
        "score": float(item.get("score") or 0),
        "quality": float(item.get("quality") if item.get("quality") is not None else 100.0),
        "fresh": float(item.get("fresh") or 0),
        "impressions": int(item.get("impressions") or 0),
    }


def build_session_page(
    sess: sessions.FeedSession,
    snap: snapshots.Snapshot,
    *,
    page: int,
    limit: int,
    order: Any = None,
) -> tuple[list[str], bool, dict[str, str]]:
    """Fill session pages up to `page`; earlier pages are never rebuilt."""
    reasons: dict[str, str] = {}
    by_id = {i["id"]: i for i in snap.items}
    for n in range(1, page + 1):
        if str(n) in sess.pages:
            continue
        shown = set(sess.shown())
        pool = [_pool_entry(i) for i in snap.items if i["id"] not in shown]
        recent = set(sess.recent)
        groups = order(pool) if order else [pool]
        # One placement over the whole page: location ring and "recently shown" only set a
        # preference (tier), so the type mix holds for every block of 20 across rings.
        for gi, group in enumerate(groups):
            for e in group:
                e["tier"] = gi * 2 + (1 if e["id"] in recent else 0)
        picked = placement.place([e for g in groups for e in g], snap.plan, limit=limit, offset=(n - 1) * limit)
        sess.pages[str(n)] = [e["id"] for e in picked]
        reasons.update({e["id"]: e.get("reason") or "ranked" for e in picked})
    ids = sess.pages.get(str(page), [])
    for pid in ids:
        reasons.setdefault(pid, (by_id.get(pid) or {}).get("reason") or "ranked")
    remaining = len([i for i in snap.items if i["id"] not in set(sess.shown())])
    return ids, remaining > 0 and bool(ids), reasons


def _page_ids(
    db: Client,
    sess: sessions.FeedSession,
    snap: snapshots.Snapshot,
    *,
    page: int,
    limit: int,
    order: Any = None,
) -> tuple[list[str], bool, dict[str, str], dict[str, str]]:
    ids, has_more, reasons = build_session_page(sess, snap, page=page, limit=limit, order=order)
    paid: dict[str, str] = {}
    if snap.plan.paid:
        lanes = boost_slots.paid_lanes(db, snap.plan)
        shown = set(sess.shown())
        lanes = [
            boost_slots.PaidLane(lane.placement, [b for b in lane.seq if b not in shown or b in ids], lane.phase)
            for lane in lanes
        ]
        ids, _, paid = boost_slots.compose_paid(
            ids, lanes, page=1, limit=len(ids) + len(ids) // max(boost_slots.slot_every(), 1) + 1
        )
    return ids, has_more, reasons, paid


def neutral_home_order(
    db: Client,
    *,
    stop_at: str | None = None,
    limit: int = HOME_PAGE_SIZE,
    max_pages: int = 25,
) -> list[str]:
    """Home order for a first-time visitor with no chosen area, built exactly like a real session."""
    snap = current_snapshot(db)
    sess = sessions.start("neutral", datetime.now(timezone.utc))
    out: list[str] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        ids, has_more, _, _ = _page_ids(db, sess, snap, page=page, limit=limit)
        for pid in ids:
            if pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
            if pid == stop_at:
                return out
        if not has_more:
            break
    return out


def _session_home_page(
    db: Client,
    sess: sessions.FeedSession,
    *,
    page: int,
    limit: int,
) -> FeedPage:
    snap = current_snapshot(db)
    if not sess.known:
        sess.known = snap.ids()
    area = sess.area if sess.area and sess.area != location.ALL_UGANDA else None
    order = location.ring_order(area, snap.plan)
    ids, has_more, reasons, paid = _page_ids(db, sess, snap, page=page, limit=limit, order=order)
    sessions.save(db, sess)
    rows = _fetch_products_by_ids(db, ids)
    by_id = {i["id"]: i for i in snap.items}
    return FeedPage(
        items=_cards(db, rows),
        has_more=has_more,
        version=sess.id,
        placements={
            str(r["id"]): paid.get(str(r["id"])) or (by_id.get(str(r["id"])) or {}).get("mode", "organic") for r in rows
        },
        trending=snap.trending(),
        reasons={str(r["id"]): reasons.get(str(r["id"]), "paid") for r in rows},
        location_reasons={
            pid: reason
            for pid in (str(r["id"]) for r in rows)
            if (reason := location.reason_for(by_id.get(pid) or {}, area))
        },
        session=sess.id,
    )


def new_listing_count(db: Client, *, user_id: str | None, session_id: str | None) -> int:
    key = sessions.key_for(user_id, session_id)
    sess = sessions.load(db, key) if key else None
    if sess is None:
        return 0
    started = sig.parse_timestamp(sess.started_at)
    if started is None:
        return 0
    shown = set(sess.shown())
    known = set(sess.known)
    count = 0
    for item in current_snapshot(db).items:
        if item["id"] in shown:
            continue
        posted = sig.parse_timestamp(item.get("posted"))
        if (known and item["id"] not in known) or (posted and posted > started):
            count += 1
    return count


def _catalog_page(
    db: Client, filters: ListingFilters, *, page: int, limit: int
) -> tuple[list[dict[str, Any]], int, dict[str, str]]:
    if filters.sort != "relevance" or filters.badge in PAID_BADGES:
        rows, total = fetch_catalog_page(db, filters, select=_PRODUCT_CARD_SELECT, page=page, limit=limit)
        return rows, total, {}
    lanes = boost_slots.paid_lanes(db, current_plan(db))
    candidates = list(dict.fromkeys(pid for lane in lanes for pid in lane.seq))
    if candidates:
        try:
            ok = matching_ids(db, filters, candidates)
        except Exception as exc:  # noqa: BLE001
            logger.info("catalog boosts skipped: %s", exc)
            ok = set()
        lanes = [boost_slots.PaidLane(lane.placement, [b for b in lane.seq if b in ok], lane.phase) for lane in lanes]
    lanes = [lane for lane in lanes if lane.seq]
    if not lanes:
        rows, total = fetch_catalog_page(db, filters, select=_PRODUCT_CARD_SELECT, page=page, limit=limit)
        return rows, total, {}
    every = boost_slots.slot_every()
    org_start = boost_slots.organic_offset_for(page, limit, every, lanes)
    rows, total = fetch_catalog_page(
        db, filters, select=_PRODUCT_CARD_SELECT, page=page, limit=limit, offset=org_start
    )
    by_id = {str(r["id"]): r for r in rows if r.get("id")}
    padded = [f"__pad{i}" for i in range(org_start)] + list(by_id)
    page_ids, _, paid = boost_slots.compose_paid(padded, lanes, page=page, limit=limit, every=every)
    for row in _fetch_products_by_ids(db, [pid for pid in page_ids if pid not in by_id]):
        by_id[str(row["id"])] = row
    return [by_id[pid] for pid in page_ids if pid in by_id], total, paid


def get_algorithm_feed(
    *,
    user_id: str | None = None,
    page: int = 1,
    limit: int = 20,
    exclude_ids: list[str] | None = None,
    session_id: str | None = None,
    category: str | None = None,
    filters: ListingFilters | None = None,
    version: str | None = None,
) -> FeedPage:
    db = get_supabase_admin()
    active = filters if filters is not None and filters.is_active() else None
    if active is None and category:
        active = ListingFilters(category=category)
    if active is not None:
        rows, total, paid = _catalog_page(db, active, page=page, limit=limit)
        forced = boost_slots.PLACEMENT[PAID_BADGES[active.badge]] if active.badge in PAID_BADGES else None
        try:
            hot = current_snapshot(db).trending()
        except Exception as exc:  # noqa: BLE001
            logger.info("trending set unavailable: %s", exc)
            hot = set()
        return FeedPage(
            items=_cards(db, rows),
            has_more=page * limit < total,
            total=total,
            placements={str(r["id"]): paid.get(str(r["id"])) or forced or "organic" for r in rows},
            trending=hot,
        )
    return _home_page(
        db,
        user_id=user_id,
        session_id=session_id,
        page=page,
        limit=limit,
        version=version,
        exclude_ids=exclude_ids,
    )


def get_session_feed(
    *,
    user_id: str | None,
    session_id: str | None,
    page: int = 1,
    limit: int = 20,
    refresh: bool = False,
    area: str | None = None,
    neutral: bool = False,
) -> FeedPage | None:
    db = get_supabase_admin()
    now = datetime.now(timezone.utc)
    if page > 1:
        area = None
    if neutral and page == 1:
        key = sessions.key_for(user_id, session_id)
        if key is None:
            return None
        sess = sessions.start(key, now, area=location.ALL_UGANDA)
        return _session_home_page(db, sess, page=page, limit=limit)
    if area is None and user_id:
        key = sessions.key_for(user_id, session_id)
        current = sessions.load(db, key) if key else None
        if current is None or current.expired(now) or refresh:
            from auth.preferences import load_preferences

            area = load_preferences(user_id).get("default_area") or None
    sess = sessions.resolve(
        db, user_id=user_id, session_id=session_id, refresh=refresh and page == 1,
        now=now, area=area,
    )
    if sess is None:
        return None
    if page == 1 and not refresh and sess.pages and _own_listing_new(db, sess, current_snapshot(db), user_id):
        # The viewer just posted: rebuild their session so their listing shows. Other people's
        # new listings never enter a session; they are announced by /home/new instead.
        sess = sessions.start(sess.key, now, area=sess.area)
    return _session_home_page(db, sess, page=page, limit=limit)


def _new_listings(sess: sessions.FeedSession, snap: snapshots.Snapshot) -> list[dict[str, Any]]:
    """Listings that went live (posted, approved or reposted) after this session's page 1 was built."""
    if not sess.known:
        return []
    shown = set(sess.shown())
    known = set(sess.known) | shown
    started = sig.parse_timestamp(sess.started_at)
    out = []
    for item in snap.items:
        if item["id"] in shown:
            continue
        posted = sig.parse_timestamp(item.get("posted"))
        if item["id"] not in known or (started and posted and posted > started):
            out.append(item)
    return out


def _new_since(sess: sessions.FeedSession, snap: snapshots.Snapshot) -> bool:
    """A listing went live (posted, approved or reposted) after this session's page 1 was built."""
    return bool(_new_listings(sess, snap))


def _own_listing_new(db: Client, sess: sessions.FeedSession, snap: snapshots.Snapshot, user_id: str | None) -> bool:
    """True when one of the new listings belongs to a shop the viewer owns."""
    if not user_id:
        return False
    shop_ids = {str(i.get("shop")) for i in _new_listings(sess, snap) if i.get("shop")}
    if not shop_ids:
        return False
    try:
        rows = db.table("shops").select("id").eq("owner_id", user_id).in_("id", sorted(shop_ids)).limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("own new listing check skipped: %s", exc)
        return False
    return bool(rows.data)


def get_latest_feed(*, limit: int = 20, page: int = 1, filters: ListingFilters | None = None) -> list[ProductResponse]:
    return get_algorithm_feed(page=page, limit=limit, filters=filters).items
