import random
from db.supabase import Client
from shop.schemas import ProductResponse

def get_latest_feed(client: Client, limit: int = 20) -> list[ProductResponse]:
    """Fetch the latest published products."""
    resp = (
        client.table("products")
        .select("*")
        .eq("is_published", True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [ProductResponse(**item) for item in resp.data]

def get_algorithm_feed(client: Client, user_id: str | None = None, limit: int = 20) -> list[ProductResponse]:
    """
    Fetch a personalized feed.
    If user_id is provided, try to find categories they are interested in based on likes.
    Otherwise, fallback to globally trending (view_count).
    """
    preferred_categories = []
    recent_searches = []
    
    if user_id:
        # Get product likes
        likes_resp = (
            client.table("product_likes")
            .select("product_id")
            .eq("user_id", user_id)
            .limit(50)
            .execute()
        )
        if likes_resp.data:
            product_ids = [item["product_id"] for item in likes_resp.data]
            # Get categories for these products
            products_resp = (
                client.table("products")
                .select("category")
                .in_("id", product_ids)
                .execute()
            )
            for p in products_resp.data:
                if p.get("category"):
                    preferred_categories.append(p["category"])
                    
        # Get recent searches
        search_resp = (
            client.table("search_history")
            .select("query")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        if search_resp.data:
            for s in search_resp.data:
                q = s.get("query", "").strip()
                if q and q not in recent_searches:
                    recent_searches.append(q)
    
    # Remove duplicates
    preferred_categories = list(set(preferred_categories))
    
    query = client.table("products").select("*").eq("is_published", True)
    
    if preferred_categories or recent_searches:
        # Build OR condition
        or_conditions = []
        if preferred_categories:
            cats = ",".join([f'"{c}"' for c in preferred_categories])
            or_conditions.append(f"category.in.({cats})")
            
        for term in recent_searches:
            # Escape percent signs in term if necessary, or just use as is
            safe_term = term.replace(",", " ").replace('"', '')
            or_conditions.append(f"title.ilike.%{safe_term}%")
            or_conditions.append(f"description.ilike.%{safe_term}%")
            
        if or_conditions:
            query = query.or_(",".join(or_conditions))
            
        # Order by view_count to still get the best ones in those categories
        query = query.order("view_count", desc=True).limit(limit * 2)
        resp = query.execute()
        items = resp.data
        random.shuffle(items)
        items = items[:limit]
    else:
        # Fallback: trending products globally
        query = query.order("view_count", desc=True).limit(limit)
        resp = query.execute()
        items = resp.data
        
    return [ProductResponse(**item) for item in items]
