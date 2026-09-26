import logging
from typing import Any

from postgrest.exceptions import APIError

from core.postgrest_compat import is_undefined_column_error
from shop import engagement_service
from core.categories import normalize_category
from shop.events import CONTENT_MODERATION_FIELDS
from shop.locations import apply_online_location, listing_is_online
from shop.schemas import (
    ProductCreate,
    ProductDetailResponse,
    ProductListItem,
    ProductResponse,
    ProductUpdate,
    ShopSummary,
)
from shop.serializers import clip_card_description

logger = logging.getLogger(__name__)


_PRODUCT_LIST_PUBLIC = (
    "id,shop_id,title,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,status,"
    "listing_score,location_name,is_published,is_negotiable,stock_quantity,listing_meta,"
    "created_at,view_count"
)
_PRODUCT_LIST_OWNER = (
    "id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,status,"
    "listing_score,location_name,is_published,is_negotiable,stock_quantity,listing_meta,"
    "created_at,view_count,review_notes,reviewed_at"
)


def list_products(
    client: Any,
    shop_id: str,
    page: int = 1,
    limit: int = 20,
    category: str | None = None,
    search: str | None = None,
    status: str | None = None,
    is_owner: bool = False,
) -> dict:
    """List products for a shop. If not owner, only is_published=True."""
    limit = min(limit, 100)
    offset = (page - 1) * limit

    def _run_list(select_cols: str):
        q = client.table("products").select(select_cols, count="exact").eq("shop_id", shop_id)
        if not is_owner:
            q = q.eq("is_published", True).eq("status", "active")
        if category:
            try:
                cat = normalize_category(category)
            except ValueError:
                cat = category.strip()
            if cat:
                q = q.eq("category", cat)
        if search:
            q = q.or_(f"title.ilike.%{search}%,description.ilike.%{search}%")
        if status and is_owner:
            q = q.eq("status", status)
        return q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()

    cols = _PRODUCT_LIST_OWNER if is_owner else _PRODUCT_LIST_PUBLIC
    try:
        r = _run_list(cols)
    except APIError as exc:
        if is_undefined_column_error(exc):
            r = _run_list(
                "id,shop_id,title,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,"
                "is_published,is_negotiable,stock_quantity,listing_meta,created_at"
            )
        else:
            raise
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = []
    for row in (r.data or []):
        image_urls = row.get("image_urls")
        if isinstance(image_urls, str):
            image_urls = [image_urls] if image_urls else []
        items.append(
            ProductListItem(
                id=str(row["id"]),
                shop_id=str(row["shop_id"]),
                title=row.get("title", ""),
                description=row.get("description"),
                price_ugx=float(row.get("price_ugx", 0)),
                discount_price=float(row["discount_price"]) if row.get("discount_price") is not None else None,
                discount_expires_at=str(row["discount_expires_at"]) if row.get("discount_expires_at") else None,
                image_urls=image_urls[:1] if image_urls else None,
                category=row.get("category"),
                is_published=row.get("is_published", True),
                is_negotiable=row.get("is_negotiable", True) is not False,
                stock_quantity=int(row.get("stock_quantity") or 0),
                listing_meta=row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {},
                item_type=row.get("item_type"),
                status=row.get("status"),
                listing_score=int(row.get("listing_score") or 0),
                location_name=row.get("location_name"),
                is_online=listing_is_online(row.get("location_name"), row.get("listing_meta")),
                review_notes=row.get("review_notes"),
                reviewed_at=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
                created_at=str(row["created_at"]) if row.get("created_at") else None,
                view_count=int(row.get("view_count") or 0),
            )
        )
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}


def _image_urls_for_db(value: list[str] | None) -> list[str] | None:
    """Postgres column is TEXT[]; PostgREST expects a JSON array, not a comma-separated string."""
    if value is None:
        return None
    return list(value)


def _attach_media_keys(payload: dict, urls: list[str] | None, data) -> None:
    from media.keys import resolve_stored_keys

    image_keys, video_keys = resolve_stored_keys(
        urls or [],
        getattr(data, "image_keys", None),
        getattr(data, "video_keys", None),
    )
    payload["image_keys"] = image_keys
    payload["video_keys"] = video_keys


def _write_products(client: Any, payload: dict, *, product_id: str | None = None):
    def run(body: dict):
        query = client.table("products")
        if product_id is None:
            return query.insert(body).execute()
        return query.update(body).eq("id", product_id).execute()

    try:
        return run(payload)
    except APIError as exc:
        if not (
            is_undefined_column_error(exc, "image_keys")
            or is_undefined_column_error(exc, "video_keys")
        ):
            raise
        logger.warning(
            "products.image_keys/video_keys missing; run db/migrations/045_product_image_keys.sql"
        )
        slim = {key: value for key, value in payload.items() if key not in {"image_keys", "video_keys"}}
        if not slim:
            raise
        return run(slim)


def create_product(client: Any, shop_id: str, data: ProductCreate) -> dict:
    location_name, listing_meta = apply_online_location(
        data.location_name,
        data.listing_meta,
        is_online=data.is_online,
    )
    payload = {
        "shop_id": shop_id,
        "title": data.title,
        "description": data.description,
        "price_ugx": data.price_ugx,
        "discount_price": data.discount_price,
        "discount_expires_at": data.discount_expires_at,
        "stock_quantity": data.stock_quantity,
        "category": data.category,
        "is_published": data.is_published,
        "is_negotiable": data.is_negotiable,
        "item_type": data.item_type or "product",
        "location_name": location_name,
        "status": "pending_review",
    }
    if listing_meta is not None:
        payload["listing_meta"] = listing_meta
    imgs = _image_urls_for_db(data.image_urls)
    if imgs is not None:
        payload["image_urls"] = imgs
        _attach_media_keys(payload, imgs, data)
    r = _write_products(client, payload)
    if not r.data or len(r.data) == 0:
        raise ValueError("Failed to create product")
    created_row = r.data[0]
    # Side effects (moderation, mail, ranking, embeddings) are subscribers
    # on `product.created` / `product.pending_review` emitted by the route.
    return _row_to_product_response(created_row)


def get_similar_products(client: Any, product_id: str, limit: int = 8) -> list[dict]:
    """Fetch products in the same category, excluding the current product."""
    try:
        cat_r = (
            client.table("products")
            .select("category")
            .eq("id", product_id)
            .limit(1)
            .execute()
        )
    except Exception:
        return []
    if not cat_r.data or not cat_r.data[0].get("category"):
        return []
    category = cat_r.data[0]["category"]
    try:
        r = (
            client.table("products")
            .select("id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,"
                    "listing_score,location_name,is_published,is_negotiable,stock_quantity,"
                    "listing_meta,created_at,view_count")
            .eq("category", category)
            .eq("is_published", True)
            .eq("status", "active")
            .neq("id", product_id)
            .order("listing_score", desc=True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        r = (
            client.table("products")
            .select("id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,image_urls,category,item_type,"
                    "is_negotiable,stock_quantity,listing_meta,is_published,created_at")
            .eq("category", category)
            .eq("is_published", True)
            .eq("status", "active")
            .neq("id", product_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    out = []
    shop_ids = list({str(row["shop_id"]) for row in (r.data or []) if row.get("shop_id")})
    shops_map = _load_card_shops(client, shop_ids)
    for row in (r.data or []):
        imgs = row.get("image_urls")
        if isinstance(imgs, str):
            imgs = [imgs] if imgs else []
        sid = str(row.get("shop_id", ""))
        s = shops_map.get(sid, {})
        pid = str(row["id"])
        out.append({
            "id": pid,
            "shop_id": sid,
            "title": row.get("title", ""),
            "description": clip_card_description(row.get("description")),
            "price_ugx": float(row.get("price_ugx", 0)),
            "discount_price": float(row["discount_price"]) if row.get("discount_price") is not None else None,
            "discount_expires_at": str(row["discount_expires_at"]) if row.get("discount_expires_at") else None,
            "image_urls": imgs[:1] if imgs else None,
            "category": row.get("category"),
            "item_type": row.get("item_type"),
            "listing_score": int(row.get("listing_score") or 0),
            "location_name": row.get("location_name"),
            "is_online": listing_is_online(row.get("location_name"), row.get("listing_meta")),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "view_count": int(row.get("view_count") or 0),
            "is_negotiable": row.get("is_negotiable", True) is not False,
            "stock_quantity": int(row["stock_quantity"]) if row.get("stock_quantity") is not None else None,
            "listing_meta": row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {},
            "average_rating": 0.0,
            "review_count": 0,
            "shop_name": s.get("seller_name") or s.get("name"),
            "shop_slug": s.get("slug"),
            "owner_id": str(s.get("owner_id")) if s.get("owner_id") else None,
            "shop_whatsapp": s.get("whatsapp_number") or None,
            "shop_is_active": bool(s.get("is_active")),
            "shop_is_personal": bool(s.get("is_personal")),
            "shop_trust_badges": s.get("trust_badges") or [],
            "shop_available_now": bool(s.get("available_now", False)),
            "seller_name": s.get("seller_name") or s.get("name"),
            "seller_joined_at": s.get("joined_at"),
            "seller_last_active_at": s.get("last_active_at"),
        })
    return out


def get_product(client: Any, product_id: str, viewer_id: str | None = None) -> dict | None:
    r = client.table("products").select("*").eq("id", product_id).execute()
    if not r.data or len(r.data) == 0:
        return None
    row = r.data[0]
    shop_id = row.get("shop_id", "")
    is_owner = bool(viewer_id and shop_id and bool(
        client.table("shops").select("owner_id").eq("id", shop_id).eq("owner_id", viewer_id).limit(1).execute().data
    ))
    if not is_owner and (row.get("status") != "active" or not row.get("is_published")):
        return None
    out = _row_to_product_response(row)
    out.update(engagement_service.get_product_engagement(client, product_id, viewer_id))
    return out


def get_product_detail(
    client: Any,
    product_id: str,
    viewer_id: str | None = None,
) -> ProductDetailResponse | None:
    """Product + shop + likes/boost/ratings in a short parallel pass."""
    from datetime import datetime, timezone
    from concurrent.futures import ThreadPoolExecutor

    prod_r = _select_product_detail(client, product_id)
    if not prod_r.data:
        return None
    row = prod_r.data[0]

    shop_id = str(row.get("shop_id", ""))
    shop_snapshot: ShopSummary | None = None
    is_owner = False
    nested = row.get("shops")
    if isinstance(nested, list):
        nested = nested[0] if nested else None
    if isinstance(nested, dict):
        from shop.seller_display import overlay_personal_sellers

        overlay_personal_sellers(client, {str(nested.get("id") or shop_id): nested})
        loc = nested.get("location")
        location_str = loc.get("display") if isinstance(loc, dict) else loc
        if listing_is_online(row.get("location_name"), row.get("listing_meta")) and not location_str:
            location_str = "Online"
        is_owner = bool(viewer_id and str(nested.get("owner_id", "")) == viewer_id)
        raw_badges = nested.get("trust_badges")
        if isinstance(raw_badges, list) and (raw_badges or nested.get("is_personal")):
            badges = raw_badges
        else:
            badges = ["shop_listed"]
        shop_snapshot = ShopSummary(
            id=str(nested["id"]),
            name=nested.get("name", ""),
            slug=nested.get("slug"),
            logo_url=nested.get("logo_url"),
            owner_id=str(nested["owner_id"]) if nested.get("owner_id") else None,
            whatsapp_number=nested.get("whatsapp_number"),
            is_active=bool(nested.get("is_active", True)),
            trust_score=int(nested.get("trust_score") or 0),
            trust_badges=badges,
            available_now=bool(nested.get("available_now", False)),
            location=location_str if isinstance(location_str, str) else (str(location_str) if location_str else None),
            created_at=str(nested["created_at"]) if nested.get("created_at") else None,
            last_seen_at=str(nested["last_seen_at"]) if nested.get("last_seen_at") else None,
            owner_phone_verified=False,
            is_personal=bool(nested.get("is_personal")),
            seller_name=nested.get("seller_name") or nested.get("name"),
            joined_at=nested.get("joined_at"),
            last_active_at=nested.get("last_active_at"),
        )

    if shop_snapshot and shop_snapshot.owner_id:
        from shop.publish_gates import assert_owner_phone_for_whatsapp
        verified = assert_owner_phone_for_whatsapp(client, shop_snapshot.owner_id)
        shop_snapshot.owner_phone_verified = verified
        if not verified:
            shop_snapshot.whatsapp_number = None


    if not is_owner and (row.get("status") != "active" or not row.get("is_published")):
        return None

    like_count = 0
    viewer_liked: bool | None = None
    boosted = False
    average_rating = 0.0
    review_count = 0

    def _likes() -> tuple[int, bool | None]:
        try:
            likes_r = (
                client.table("product_likes")
                .select("product_id", count="exact")
                .eq("product_id", product_id)
                .limit(1)
                .execute()
            )
            count = int(likes_r.count or 0)
            liked: bool | None = None
            if viewer_id:
                vr = (
                    client.table("product_likes")
                    .select("user_id")
                    .eq("product_id", product_id)
                    .eq("user_id", viewer_id)
                    .limit(1)
                    .execute()
                )
                liked = bool(vr.data)
            return count, liked
        except Exception:
            return 0, None

    def _boost() -> bool:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            boost_r = (
                client.table("listing_boosts")
                .select("id")
                .eq("listing_id", product_id)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .limit(1)
                .execute()
            )
            return bool(boost_r.data)
        except Exception:
            return False

    def _ratings() -> tuple[float, int]:
        try:
            rr = (
                client.table("product_reviews")
                .select("rating")
                .eq("product_id", product_id)
                .limit(200)
                .execute()
            )
            ratings = [float(rev["rating"]) for rev in (rr.data or []) if rev.get("rating")]
            if not ratings:
                return 0.0, 0
            return round(sum(ratings) / len(ratings), 2), len(ratings)
        except Exception:
            return 0.0, 0

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_likes = pool.submit(_likes)
        fut_boost = pool.submit(_boost)
        fut_ratings = pool.submit(_ratings)
        like_count, viewer_liked = fut_likes.result()
        boosted = fut_boost.result()
        average_rating, review_count = fut_ratings.result()

    image_urls = row.get("image_urls") or []
    if isinstance(image_urls, str):
        image_urls = [s.strip() for s in image_urls.split(",") if s.strip()]
    else:
        image_urls = [str(x).strip() for x in image_urls if x is not None and str(x).strip()]

    return ProductDetailResponse(
        id=str(row["id"]),
        shop_id=shop_id,
        title=row.get("title", ""),
        description=row.get("description"),
        price_ugx=float(row.get("price_ugx", 0)),
        discount_price=float(row["discount_price"]) if row.get("discount_price") is not None else None,
        discount_expires_at=str(row["discount_expires_at"]) if row.get("discount_expires_at") else None,
        stock_quantity=int(row.get("stock_quantity") or 0),
        image_urls=image_urls,
        category=row.get("category"),
        item_type=row.get("item_type"),
        status=row.get("status"),
        is_published=bool(row.get("is_published", True)),
        is_negotiable=row.get("is_negotiable", True) is not False,
        listing_score=int(row.get("listing_score") or 0),
        location_name=row.get("location_name"),
        is_online=listing_is_online(row.get("location_name"), row.get("listing_meta")),
        listing_meta=row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {},
        ai_seo_tags=row.get("ai_seo_tags"),
        ai_generated_desc=bool(row.get("ai_generated_desc", False)),
        review_notes=row.get("review_notes"),
        reviewed_at=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        like_count=like_count,
        view_count=int(row.get("view_count") or 0),
        viewer_liked=viewer_liked,
        whatsapp_clicks=0,
        messages=0,
        boosted=boosted,
        average_rating=average_rating,
        review_count=review_count,
        shop=shop_snapshot,
    )




def update_product(client: Any, product_id: str, data: ProductUpdate) -> dict | None:
    payload = data.model_dump(exclude_unset=True)
    if "image_urls" in payload:
        urls = _image_urls_for_db(payload.get("image_urls")) or []
        payload["image_urls"] = urls
        _attach_media_keys(payload, urls, data)
    _apply_location_update(client, product_id, payload)

    # Content-changing edits must go back through moderation. We reset status
    # to pending_review even if the merchant tried to set status=active, which
    # closes the "edit-around-the-gate" bypass.
    content_changed = any(key in payload for key in CONTENT_MODERATION_FIELDS)
    if content_changed:
        payload["status"] = "pending_review"
    elif "status" in payload and payload["status"] == "active":
        # Merchants cannot self-approve. Admin routes handle publish separately.
        payload.pop("status", None)
    if not payload:
        return get_product(client, product_id, viewer_id=None)
    r = _write_products(client, payload, product_id=product_id)
    if not r.data or len(r.data) == 0:
        return None
    updated_row = r.data[0]
    # Ranking / embeddings / moderation subscribe to `product.updated` and
    # `product.pending_review` from the PATCH route — not here.
    return _row_to_product_response(updated_row)


def delete_product(client: Any, product_id: str) -> bool:
    r = client.table("products").delete().eq("id", product_id).execute()
    return bool(r.data)


def repost_product(client: Any, product_id: str) -> dict | None:
    from datetime import datetime, timezone, timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    
    logs = client.table("product_reposts_log").select("id").eq("product_id", product_id).gte("created_at", yesterday).execute()
    
    if logs.data and len(logs.data) >= 2:
        raise ValueError("Repost limit reached (2x per 24 hours)")
        
    # Insert log
    client.table("product_reposts_log").insert({"product_id": product_id}).execute()
    
    # Update product created_at
    r = client.table("products").update({"created_at": datetime.now(timezone.utc).isoformat()}).eq("id", product_id).execute()
    
    if not r.data or len(r.data) == 0:
        return None
    from ranking.service import calculate_listing_score
    calculate_listing_score(product_id)
    return _row_to_product_response(r.data[0])


def _row_to_product_response(row: dict) -> dict:
    image_urls = row.get("image_urls")
    if isinstance(image_urls, list):
        image_urls = [str(x).strip() for x in image_urls if x is not None and str(x).strip()]
    elif isinstance(image_urls, str) and image_urls:
        image_urls = [s.strip() for s in image_urls.split(",")]
    elif not image_urls:
        image_urls = []
    return {
        "id": str(row["id"]),
        "shop_id": str(row["shop_id"]),
        "title": row.get("title", ""),
        "description": row.get("description"),
        "price_ugx": float(row.get("price_ugx", 0)),
        "discount_price": float(row["discount_price"]) if row.get("discount_price") is not None else None,
        "discount_expires_at": str(row["discount_expires_at"]) if row.get("discount_expires_at") else None,
        "stock_quantity": row.get("stock_quantity") or 0,
        "image_urls": image_urls,
        "category": row.get("category"),
        "item_type": row.get("item_type", "product"),
        "status": row.get("status", "active"),
        "listing_score": int(row.get("listing_score") or 0),
        "location_name": row.get("location_name"),
        "is_online": listing_is_online(row.get("location_name"), row.get("listing_meta")),
        "listing_meta": row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {},
        "ai_seo_tags": row.get("ai_seo_tags"),
        "ai_generated_desc": row.get("ai_generated_desc", False),
        "is_published": row.get("is_published", True),
        "is_negotiable": row.get("is_negotiable", True) if row.get("is_negotiable") is not False else False,
        "review_notes": row.get("review_notes"),
        "reviewed_at": str(row["reviewed_at"]) if row.get("reviewed_at") else None,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "like_count": 0,
        "view_count": int(row.get("view_count") or 0),
        "viewer_liked": None,
    }


_DETAIL_COLS = (
    "id,shop_id,title,description,price_ugx,discount_price,discount_expires_at,stock_quantity,image_urls,"
    "category,item_type,status,is_published,is_negotiable,listing_score,location_name,listing_meta,"
    "ai_seo_tags,ai_generated_desc,review_notes,reviewed_at,created_at,view_count,"
)
_SHOP_EMBED = (
    "shops(id,name,slug,logo_url,owner_id,whatsapp_number,"
    "is_active,trust_score,available_now,location,trust_badges,created_at,last_seen_at"
)
_CARD_SHOP_COLS = (
    "id,name,slug,whatsapp_number,owner_id,is_active,trust_badges,available_now,"
    "created_at,last_seen_at,is_personal"
)


def _select_product_detail(client: Any, product_id: str):
    for embed in (_SHOP_EMBED + ",is_personal)", _SHOP_EMBED + ")"):
        try:
            return (
                client.table("products")
                .select(_DETAIL_COLS + embed)
                .eq("id", product_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            if embed.endswith(",is_personal)") and is_undefined_column_error(exc, "is_personal"):
                continue
            raise
    raise RuntimeError("product detail select failed")


def _load_card_shops(client: Any, shop_ids: list[str]) -> dict[str, dict]:
    from shop.seller_display import overlay_personal_sellers

    if not shop_ids:
        return {}
    rows: list[dict] = []
    for cols in (_CARD_SHOP_COLS, _CARD_SHOP_COLS.replace(",is_personal", "")):
        try:
            sr = client.table("shops").select(cols).in_("id", shop_ids).execute()
            rows = sr.data or []
            break
        except APIError as exc:
            if "is_personal" in cols and is_undefined_column_error(exc, "is_personal"):
                continue
            return {}
        except Exception:
            return {}
    shops = {str(row["id"]): row for row in rows if row.get("id")}
    overlay_personal_sellers(client, shops)
    return shops


def _apply_location_update(client: Any, product_id: str, payload: dict) -> None:
    has_flag = "is_online" in payload
    flag = payload.pop("is_online", None)
    if not has_flag and "location_name" not in payload and "listing_meta" not in payload:
        return
    current = (
        client.table("products")
        .select("location_name,listing_meta")
        .eq("id", product_id)
        .limit(1)
        .execute()
    )
    row = current.data[0] if current.data else {}
    loc = payload["location_name"] if "location_name" in payload else row.get("location_name")
    meta = payload["listing_meta"] if "listing_meta" in payload else row.get("listing_meta")
    if not isinstance(meta, dict):
        meta = None
    loc, meta = apply_online_location(loc, meta, is_online=flag if has_flag else None)
    payload["location_name"] = loc
    if meta is not None:
        payload["listing_meta"] = meta


def list_owner_products(client: Any, owner_id: str, page: int = 1, limit: int = 20) -> dict:
    """Every listing the user owns, including ones on a personal seller profile."""
    limit = min(max(limit, 1), 100)
    offset = (page - 1) * limit
    shops = client.table("shops").select("id").eq("owner_id", owner_id).execute()
    shop_ids = [str(row["id"]) for row in (shops.data or []) if row.get("id")]
    empty = {"items": [], "total": 0, "page": page, "limit": limit, "total_pages": 0}
    if not shop_ids:
        return empty
    r = (
        client.table("products")
        .select(_PRODUCT_LIST_OWNER, count="exact")
        .in_("shop_id", shop_ids)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0
    items = []
    for row in r.data or []:
        image_urls = row.get("image_urls")
        if isinstance(image_urls, str):
            image_urls = [image_urls] if image_urls else []
        items.append(
            ProductListItem(
                id=str(row["id"]),
                shop_id=str(row["shop_id"]),
                title=row.get("title", ""),
                description=row.get("description"),
                price_ugx=float(row.get("price_ugx", 0)),
                discount_price=float(row["discount_price"]) if row.get("discount_price") is not None else None,
                discount_expires_at=str(row["discount_expires_at"]) if row.get("discount_expires_at") else None,
                image_urls=image_urls[:1] if image_urls else None,
                category=row.get("category"),
                is_published=row.get("is_published", True),
                is_negotiable=row.get("is_negotiable", True) is not False,
                stock_quantity=int(row.get("stock_quantity") or 0),
                listing_meta=row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {},
                item_type=row.get("item_type"),
                status=row.get("status"),
                listing_score=int(row.get("listing_score") or 0),
                location_name=row.get("location_name"),
                is_online=listing_is_online(row.get("location_name"), row.get("listing_meta")),
                review_notes=row.get("review_notes"),
                reviewed_at=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
                created_at=str(row["created_at"]) if row.get("created_at") else None,
                view_count=int(row.get("view_count") or 0),
            )
        )
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": total_pages}
