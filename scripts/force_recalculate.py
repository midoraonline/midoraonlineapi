"""Force recalculate listing_score for all products.

Usage:
    source .venv/bin/activate
    PYTHONPATH="$PWD" python scripts/force_recalculate.py

This checks which DB functions exist and recalculates every product's score.
"""

from db.supabase import get_supabase_admin


def main() -> None:
    admin = get_supabase_admin()

    print("=" * 60)
    print("  FORCE RECALCULATE LISTING SCORES")
    print("=" * 60)

    # 1. Check which version of the function is installed
    print("\n--- Checking installed DB function ---")
    try:
        r = admin.rpc("recalculate_product_listing_score", {"p_product_id": "00000000-0000-0000-0000-000000000000"}).execute()
        if r.data is not None:
            print(f"  Function recalculate_product_listing_score exists (returned: {r.data})")
        else:
            print("  Function returned None")
    except Exception as e:
        print(f"  ❌ Function call FAILED: {str(e)[:200]}")
        print("  Trying raw SQL query to check...")
        try:
            r2 = (
                admin.table("products")
                .select("id")
                .limit(1)
                .execute()
            )
            print(f"  Basic query works. Products exist: {len(r2.data or [])}")
        except Exception as e2:
            print(f"  ❌ Basic query also failed: {e2}")

    # 2. Get all active products
    print("\n--- Fetching all active products ---")
    r = (
        admin.table("products")
        .select("id,title,listing_score,view_count,created_at,shop_id")
        .eq("is_published", True)
        .execute()
    )
    products = r.data or []
    print(f"  Found {len(products)} published products")

    # 3. Recalculate each product
    print("\n--- Recalculating scores ---")
    success = 0
    fail = 0
    for p in products:
        pid = p["id"]
        try:
            r2 = admin.rpc("recalculate_product_listing_score", {"p_product_id": pid}).execute()
            new_score = r2.data
            old_score = p.get("listing_score", 0)
            delta = ""
            if new_score != old_score:
                delta = f" (was {old_score}, now {new_score})"
            print(f"  {str(pid)[:8]}...  score={new_score}{delta}")
            success += 1
        except Exception as e:
            print(f"  ❌ {str(pid)[:8]}... FAILED: {str(e)[:100]}")
            fail += 1

    print(f"\n  ✅ {success} recalculated, ❌ {fail} failed")

    # 4. Verify results
    print("\n--- Verification ---")
    r3 = (
        admin.table("products")
        .select("id,title,listing_score,view_count")
        .eq("is_published", True)
        .eq("status", "active")
        .order("listing_score", desc=True)
        .limit(50)
        .execute()
    )
    for p in r3.data or []:
        score = p.get("listing_score", 0)
        views = p.get("view_count", 0)
        title = (p.get("title") or "?")[:40]
        print(f"  score={score:>4}  views={views:>3}  {title}")

    print("\nDone.")


if __name__ == "__main__":
    main()
