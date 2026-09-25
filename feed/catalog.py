"""Server-side catalog filters for the home feed, browse, and search.

Every constraint is applied on the PostgREST query before range/limit.
Personalized ranking stays on the unfiltered home feed.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import Query

logger = logging.getLogger(__name__)

PHASE3_BADGES: tuple[str, ...] = (
    "identity_verified",
    "business_verified",
    "professional_verified",
)
LISTING_TYPES: frozenset[str] = frozenset(
    {"product", "service", "opportunity", "job", "property"}
)
SORTS: frozenset[str] = frozenset(
    {
        "relevance",
        "newest",
        "price_asc",
        "price_desc",
        "most_viewed",
        "best_rated",
        "trust_score",
    }
)
# Inventory types use stock_quantity. Services, jobs, and opportunities stay
# available while status=active even when stock is 0.
_NON_STOCK_TYPES = "service,opportunity,job,property"
AVAILABLE_OR = (
    f"stock_quantity.gt.0,stock_quantity.is.null,item_type.in.({_NON_STOCK_TYPES})"
)
MEMORY_SORTS = frozenset({"best_rated"})
_VERIFIED_IN_CAP = 120
_RATING_ID_CAP = 800
_MEMORY_CAP = 2000
_BATCH = 500
_META_TOKEN = re.compile(r"^[a-z0-9_]{1,40}$")

Refine = Callable[[Any], Any]


@dataclass(frozen=True)
class ListingFilters:
    category: str | None = None
    listing_type: str | None = None
    verified_only: bool = False
    available: bool = False
    min_price: float | None = None
    max_price: float | None = None
    min_rating: float | None = None
    location: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_km: float | None = None
    opportunity_kind: str | None = None
    compensation: str | None = None
    pricing_model: str | None = None
    sort: str = "relevance"

    def is_active(self) -> bool:
        """True when this request must not use the unfiltered ranked feed."""
        if self.sort != "relevance":
            return True
        if self.lat is not None and self.lng is not None:
            return True
        return any(
            (
                self.category,
                self.listing_type,
                self.verified_only,
                self.available,
                self.min_price is not None,
                self.max_price is not None,
                self.min_rating is not None,
                self.location,
                self.opportunity_kind,
                self.compensation,
                self.pricing_model,
            )
        )

    def cache_token(self) -> str:
        return "|".join(
            [
                self.category or "",
                self.listing_type or "",
                "1" if self.verified_only else "0",
                "1" if self.available else "0",
                "" if self.min_price is None else str(self.min_price),
                "" if self.max_price is None else str(self.max_price),
                "" if self.min_rating is None else str(self.min_rating),
                self.location or "",
                "" if self.lat is None else str(self.lat),
                "" if self.lng is None else str(self.lng),
                "" if self.radius_km is None else str(self.radius_km),
                self.opportunity_kind or "",
                self.compensation or "",
                self.pricing_model or "",
                self.sort,
            ]
        )


@dataclass
class ResolvedConstraints:
    impossible: bool = False
    use_badge_overlap: bool = False
    shop_ids: list[str] | None = None
    product_ids: list[str] | None = None
    location_shop_ids: list[str] = field(default_factory=list)


def listing_filters(
    category: str | None = Query(
        None,
        description="Category label or slug. Parent labels expand to subcategories.",
    ),
    listing_type: str | None = Query(
        None,
        description="product, service, opportunity, job, or property.",
    ),
    verified_only: bool = Query(
        False,
        description="Only listings whose shop has an approved Phase 3 badge.",
    ),
    available: bool = Query(
        False,
        description="Active listings that are not sold. Products must be in stock.",
    ),
    min_price: float | None = Query(None, ge=0, description="Minimum price_ugx."),
    max_price: float | None = Query(None, ge=0, description="Maximum price_ugx."),
    min_rating: float | None = Query(None, ge=0, le=5, description="Minimum average rating."),
    location: str | None = Query(
        None,
        max_length=160,
        description="Exact location_name or shop location display.",
    ),
    lat: float | None = Query(None, ge=-90, le=90, description="Near-me latitude."),
    lng: float | None = Query(None, ge=-180, le=180, description="Near-me longitude."),
    radius_km: float | None = Query(
        None,
        gt=0,
        le=200,
        description="Near-me radius in km. Defaults to 25 when lat and lng are set.",
    ),
    opportunity_kind: str | None = Query(
        None,
        description="listing_meta.opportunity_kind (job, gig, collaboration, internship, maids, other).",
    ),
    compensation: str | None = Query(
        None,
        description="listing_meta.compensation (paid, unpaid, commission, negotiable).",
    ),
    pricing_model: str | None = Query(
        None,
        description="listing_meta.pricing_model (fixed, hourly, starting_at, quote).",
    ),
    sort: str = Query(
        "relevance",
        description="relevance, newest, price_asc, price_desc, most_viewed, best_rated, trust_score.",
    ),
) -> ListingFilters:
    return build_listing_filters(
        category=category,
        listing_type=listing_type,
        verified_only=verified_only,
        available=available,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        location=location,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        opportunity_kind=opportunity_kind,
        compensation=compensation,
        pricing_model=pricing_model,
        sort=sort,
    )


def build_listing_filters(**raw: Any) -> ListingFilters:
    sort = str(raw.get("sort") or "relevance").strip().lower()
    if sort not in SORTS:
        raise ValueError(
            "sort must be one of: " + ", ".join(sorted(SORTS))
        )
    listing_type = _blank(raw.get("listing_type"))
    if listing_type is not None:
        listing_type = listing_type.lower()
        if listing_type not in LISTING_TYPES:
            raise ValueError(
                "listing_type must be one of: " + ", ".join(sorted(LISTING_TYPES))
            )
    min_price = raw.get("min_price")
    max_price = raw.get("max_price")
    if min_price is not None and max_price is not None and float(min_price) > float(max_price):
        raise ValueError("min_price cannot exceed max_price")
    lat = raw.get("lat")
    lng = raw.get("lng")
    radius = raw.get("radius_km")
    if lat is not None and lng is not None and radius is None:
        radius = 25.0
    return ListingFilters(
        category=_blank(raw.get("category")),
        listing_type=listing_type,
        verified_only=bool(raw.get("verified_only")),
        available=bool(raw.get("available")),
        min_price=None if min_price is None else float(min_price),
        max_price=None if max_price is None else float(max_price),
        min_rating=None if raw.get("min_rating") is None else float(raw["min_rating"]),
        location=_blank(raw.get("location")),
        lat=None if lat is None else float(lat),
        lng=None if lng is None else float(lng),
        radius_km=None if radius is None else float(radius),
        opportunity_kind=_meta_token(raw.get("opportunity_kind"), "opportunity_kind"),
        compensation=_meta_token(raw.get("compensation"), "compensation"),
        pricing_model=_meta_token(raw.get("pricing_model"), "pricing_model"),
        sort=sort,
    )


def fetch_catalog_page(
    client: Any,
    filters: ListingFilters,
    *,
    select: str,
    page: int,
    limit: int,
    refine: Refine | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return one page and the filtered total. Filters run before range."""
    resolved = resolve_constraints(client, filters)
    if resolved.impossible:
        return [], 0
    page = max(1, int(page))
    limit = max(1, int(limit))
    select_cols = _select_for(select, resolved, filters.sort)
    if filters.sort in MEMORY_SORTS:
        return _memory_sorted_page(
            client, filters, resolved, select, page, limit, refine
        )
    query = _filtered_query(client, select_cols, filters, resolved, refine)
    query = _apply_sql_order(query, filters.sort)
    offset = (page - 1) * limit
    resp = query.range(offset, offset + limit - 1).execute()
    total = int(resp.count) if getattr(resp, "count", None) is not None else len(resp.data or [])
    return _clean_rows(resp.data or []), total


def resolve_constraints(client: Any, filters: ListingFilters) -> ResolvedConstraints:
    resolved = ResolvedConstraints()
    verified_ids: list[str] | None = None
    if filters.verified_only:
        verified_ids = approved_phase3_shop_ids(client)
        if not verified_ids:
            resolved.impossible = True
            return resolved
    near_ids: list[str] | None = None
    if filters.lat is not None and filters.lng is not None:
        near_ids = shops_within_radius(
            client,
            filters.lat,
            filters.lng,
            filters.radius_km or 25.0,
        )
        if not near_ids:
            resolved.impossible = True
            return resolved
    if verified_ids is not None and len(verified_ids) > _VERIFIED_IN_CAP:
        resolved.use_badge_overlap = True
        resolved.shop_ids = near_ids
    else:
        resolved.shop_ids = _intersect(verified_ids, near_ids)
        if resolved.shop_ids is not None and not resolved.shop_ids:
            resolved.impossible = True
            return resolved
    if filters.location and near_ids is None:
        resolved.location_shop_ids = shop_ids_for_location(client, filters.location)
    if filters.min_rating is not None:
        ranked = products_with_min_rating(client, filters.min_rating)
        if not ranked:
            resolved.impossible = True
            return resolved
        resolved.product_ids = ranked[:_RATING_ID_CAP]
    return resolved


def apply_listing_filters(query: Any, filters: ListingFilters, resolved: ResolvedConstraints) -> Any:
    """Constrain an active+published products query. Does not paginate."""
    labels = _category_labels(filters.category)
    if labels:
        if len(labels) == 1:
            query = query.eq("category", labels[0])
        else:
            query = query.in_("category", labels)
    if filters.listing_type:
        query = _apply_listing_type(query, filters.listing_type)
    if filters.available:
        # status=active already excludes sold/hidden/expired. This adds in-stock.
        query = query.or_(AVAILABLE_OR)
    if filters.min_price is not None:
        query = query.gte("price_ugx", filters.min_price)
    if filters.max_price is not None:
        query = query.lte("price_ugx", filters.max_price)
    if filters.opportunity_kind:
        query = query.eq("listing_meta->>opportunity_kind", filters.opportunity_kind)
    if filters.compensation:
        query = query.eq("listing_meta->>compensation", filters.compensation)
    if filters.pricing_model:
        query = query.eq("listing_meta->>pricing_model", filters.pricing_model)
    if resolved.shop_ids is not None:
        query = query.in_("shop_id", resolved.shop_ids)
    if resolved.use_badge_overlap:
        query = query.overlaps("shops.trust_badges", list(PHASE3_BADGES))
    if resolved.product_ids is not None:
        query = query.in_("id", resolved.product_ids)
    if filters.location and filters.lat is None:
        query = _apply_location(query, filters.location, resolved.location_shop_ids)
    return query


def approved_phase3_shop_ids(client: Any) -> list[str]:
    """Shop ids with an approved identity, business, or professional badge.

    Stage status lives on shop_verifications.metadata (migration 041). The
    approve endpoint copies the same badges onto shops.trust_badges. Either
    source qualifies. The product query then uses `in` or `overlaps`.
    """
    ids: set[str] = set()
    try:
        rows = _scan(
            client,
            "shop_verifications",
            "shop_id",
            lambda q: q.or_(
                "metadata->>stage2_status.eq.verified,"
                "metadata->>stage3_status.eq.verified,"
                "metadata->>stage4_status.eq.verified"
            ),
        )
        for row in rows:
            sid = row.get("shop_id")
            if sid:
                ids.add(str(sid))
    except Exception as exc:
        logger.warning("phase3 verification shop lookup failed: %s", exc)
    try:
        rows = _scan(
            client,
            "shops",
            "id",
            lambda q: q.overlaps("trust_badges", list(PHASE3_BADGES)),
        )
        for row in rows:
            sid = row.get("id")
            if sid:
                ids.add(str(sid))
    except Exception as exc:
        logger.warning("trust_badges shop lookup failed: %s", exc)
    return list(ids)


def shop_ids_for_location(client: Any, location: str) -> list[str]:
    try:
        resp = (
            client.table("shops")
            .select("id")
            .eq("location->>display", location)
            .limit(500)
            .execute()
        )
    except Exception as exc:
        logger.warning("shop location lookup failed: %s", exc)
        return []
    return [str(row["id"]) for row in (resp.data or []) if row.get("id")]


def shops_within_radius(client: Any, lat: float, lng: float, radius_km: float) -> list[str]:
    try:
        rows = _scan(client, "shops", "id,location", lambda q: q)
    except Exception as exc:
        logger.warning("near-me shop lookup failed: %s", exc)
        return []
    out: list[str] = []
    for row in rows:
        loc = row.get("location")
        if not isinstance(loc, dict):
            continue
        try:
            slat = float(loc["lat"])
            slng = float(loc["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if _haversine_km(lat, lng, slat, slng) <= radius_km:
            sid = row.get("id")
            if sid:
                out.append(str(sid))
    return out


def products_with_min_rating(client: Any, min_rating: float) -> list[str]:
    """Product ids whose review average is at least min_rating, highest first."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    try:
        rows = _scan(client, "product_reviews", "product_id,rating", lambda q: q, cap=20000)
    except Exception as exc:
        logger.warning("min_rating review lookup failed: %s", exc)
        return []
    for row in rows:
        pid = row.get("product_id")
        rating = row.get("rating")
        if not pid or rating is None:
            continue
        key = str(pid)
        totals[key] = totals.get(key, 0.0) + float(rating)
        counts[key] = counts.get(key, 0) + 1
    ranked = [
        (pid, totals[pid] / counts[pid], counts[pid])
        for pid in counts
        if counts[pid] and (totals[pid] / counts[pid]) >= min_rating
    ]
    ranked.sort(key=lambda item: (-item[1], -item[2], item[0]))
    return [pid for pid, _, _ in ranked]


def rating_map(client: Any, product_ids: list[str]) -> dict[str, tuple[float, int]]:
    if not product_ids:
        return {}
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    try:
        for chunk in _chunks(product_ids, 100):
            resp = (
                client.table("product_reviews")
                .select("product_id,rating")
                .in_("product_id", chunk)
                .execute()
            )
            for row in resp.data or []:
                pid = row.get("product_id")
                rating = row.get("rating")
                if not pid or rating is None:
                    continue
                key = str(pid)
                totals[key] = totals.get(key, 0.0) + float(rating)
                counts[key] = counts.get(key, 0) + 1
    except Exception as exc:
        logger.warning("rating map failed: %s", exc)
        return {}
    return {
        pid: (round(totals[pid] / counts[pid], 2), counts[pid])
        for pid in counts
        if counts[pid]
    }


def trust_map(client: Any, shop_ids: list[str]) -> dict[str, float]:
    if not shop_ids:
        return {}
    out: dict[str, float] = {}
    try:
        for chunk in _chunks(shop_ids, 100):
            resp = (
                client.table("shops")
                .select("id,trust_score")
                .in_("id", chunk)
                .execute()
            )
            for row in resp.data or []:
                sid = row.get("id")
                if sid:
                    out[str(sid)] = float(row.get("trust_score") or 0)
    except Exception as exc:
        logger.warning("trust map failed: %s", exc)
    return out


def sort_rows(
    rows: list[dict[str, Any]],
    sort: str,
    *,
    ratings: dict[str, tuple[float, int]] | None = None,
    trusts: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    ratings = ratings or {}
    trusts = trusts or {}

    def created(row: dict[str, Any]) -> str:
        return str(row.get("created_at") or "")

    if sort == "price_asc":
        rows.sort(key=lambda row: (float(row.get("price_ugx") or 0), created(row)))
    elif sort == "price_desc":
        rows.sort(key=lambda row: (-float(row.get("price_ugx") or 0), created(row)))
    elif sort == "newest":
        rows.sort(key=created, reverse=True)
    elif sort == "most_viewed":
        rows.sort(key=lambda row: (-int(row.get("view_count") or 0), created(row)))
    elif sort == "best_rated":
        rows.sort(
            key=lambda row: (
                -ratings.get(str(row.get("id")), (0.0, 0))[0],
                -ratings.get(str(row.get("id")), (0.0, 0))[1],
                created(row),
            )
        )
    elif sort == "trust_score":
        rows.sort(
            key=lambda row: (
                -trusts.get(str(row.get("shop_id")), 0.0),
                created(row),
            )
        )
    else:
        rows.sort(
            key=lambda row: (-int(row.get("listing_score") or 0), created(row)),
            reverse=False,
        )
    return rows


def _filtered_query(
    client: Any,
    select: str,
    filters: ListingFilters,
    resolved: ResolvedConstraints,
    refine: Refine | None,
) -> Any:
    query = (
        client.table("products")
        .select(select, count="exact")
        .eq("status", "active")
        .eq("is_published", True)
    )
    query = apply_listing_filters(query, filters, resolved)
    if refine is not None:
        query = refine(query)
    return query


def _memory_sorted_page(
    client: Any,
    filters: ListingFilters,
    resolved: ResolvedConstraints,
    hydrate_select: str,
    page: int,
    limit: int,
    refine: Refine | None,
) -> tuple[list[dict[str, Any]], int]:
    lean = "id,shop_id,created_at,price_ugx,view_count,listing_score"
    if resolved.use_badge_overlap:
        lean += ",shops!inner(trust_badges)"
    collected: list[dict[str, Any]] = []
    total: int | None = None
    offset = 0
    while len(collected) < _MEMORY_CAP:
        query = _filtered_query(client, lean, filters, resolved, refine)
        end = offset + _BATCH - 1
        resp = query.order("created_at", desc=True).range(offset, end).execute()
        if total is None:
            total = int(resp.count) if getattr(resp, "count", None) is not None else None
        batch = list(resp.data or [])
        collected.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
        if total is not None and offset >= total:
            break
    if filters.sort == "best_rated":
        ratings = rating_map(client, [str(row.get("id")) for row in collected if row.get("id")])
        trusts: dict[str, float] = {}
    else:
        ratings = {}
        trusts = trust_map(
            client, list({str(row.get("shop_id")) for row in collected if row.get("shop_id")})
        )
    ordered = sort_rows(collected, filters.sort, ratings=ratings, trusts=trusts)
    if total is not None and len(collected) < total:
        logger.info(
            "catalog sort %s capped collected=%s matched=%s",
            filters.sort, len(collected), total,
        )
        filtered_total = len(ordered)
    else:
        filtered_total = total if total is not None else len(ordered)
    start = (page - 1) * limit
    page_rows = ordered[start : start + limit]
    page_ids = [str(row.get("id")) for row in page_rows if row.get("id")]
    if not page_ids:
        return [], filtered_total
    hydrated = _hydrate(client, hydrate_select, page_ids)
    by_id = {str(row.get("id")): row for row in hydrated}
    return [by_id[pid] for pid in page_ids if pid in by_id], filtered_total


def _hydrate(client: Any, select: str, page_ids: list[str]) -> list[dict[str, Any]]:
    resp = (
        client.table("products")
        .select(select)
        .in_("id", page_ids)
        .execute()
    )
    return _clean_rows(resp.data or [])


def _apply_sql_order(query: Any, sort: str) -> Any:
    if sort == "newest":
        return query.order("created_at", desc=True)
    if sort == "price_asc":
        return query.order("price_ugx", desc=False).order("created_at", desc=True)
    if sort == "price_desc":
        return query.order("price_ugx", desc=True).order("created_at", desc=True)
    if sort == "most_viewed":
        return query.order("view_count", desc=True).order("created_at", desc=True)
    if sort == "trust_score":
        return (
            query.order("trust_score", desc=True, foreign_table="shops")
            .order("created_at", desc=True)
        )
    return query.order("listing_score", desc=True).order("created_at", desc=True)


def _apply_listing_type(query: Any, listing_type: str) -> Any:
    if listing_type == "product":
        # Matches the browse "Products" chip: everything that is not a service or opportunity.
        return query.not_.in_("item_type", ["service", "opportunity", "job"])
    if listing_type == "service":
        return query.eq("item_type", "service")
    if listing_type == "property":
        return query.eq("item_type", "property")
    if listing_type == "opportunity":
        return query.in_("item_type", ["opportunity", "job"])
    if listing_type == "job":
        return query.or_(
            "item_type.eq.job,and(item_type.eq.opportunity,listing_meta->>opportunity_kind.eq.job)"
        )
    return query


def _apply_location(query: Any, location: str, shop_ids: list[str]) -> Any:
    if not shop_ids:
        return query.eq("location_name", location)
    quoted = '"' + location.replace('"', '""') + '"'
    id_list = ",".join(shop_ids)
    return query.or_(f"location_name.eq.{quoted},shop_id.in.({id_list})")


def _select_for(select: str, resolved: ResolvedConstraints, sort: str = "") -> str:
    if "shops!" in select:
        return select
    parts: list[str] = []
    if sort == "trust_score":
        parts.append("trust_score")
    if resolved.use_badge_overlap:
        parts.append("trust_badges")
    if not parts:
        return select
    return select + ",shops!inner(" + ",".join(parts) + ")"


def _category_labels(category: str | None) -> list[str] | None:
    if not category:
        return None
    from feed.service import resolve_category_filter_labels

    return resolve_category_filter_labels(category)


def _clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("shops", None)
        item.pop("updated_at", None)
        cleaned.append(item)
    return cleaned


def _scan(
    client: Any,
    table: str,
    select: str,
    build: Refine,
    cap: int = 5000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < cap:
        query = build(client.table(table).select(select))
        resp = query.range(offset, offset + _BATCH - 1).execute()
        batch = list(resp.data or [])
        rows.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return rows[:cap]


def _intersect(left: list[str] | None, right: list[str] | None) -> list[str] | None:
    if left is None:
        return right
    if right is None:
        return left
    right_set = set(right)
    return [item for item in left if item in right_set]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _meta_token(value: Any, field_name: str) -> str | None:
    text = _blank(value)
    if text is None:
        return None
    token = text.lower()
    if not _META_TOKEN.fullmatch(token):
        raise ValueError(f"Invalid {field_name}")
    return token


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))
