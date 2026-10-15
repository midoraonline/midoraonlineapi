from admin.routes import reports as rp


class _Q:
    def __init__(self, data):
        self.data = data

    def select(self, *_a, **_k):
        return self

    def in_(self, col, ids):
        self.data = [r for r in self.data if str(r.get(col)) in ids]
        return self

    def execute(self):
        return self


class _Admin:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(list(self.db.get(name, [])))


DB = {
    "products": [{"id": "p1", "title": "Phone", "price_ugx": 50000, "item_type": "product", "status": "active", "image_urls": ["https://x/a.jpg"], "shop_id": "s1"}],
    "shops": [{"id": "s1", "name": "Ada Shop", "slug": "ada", "owner_id": "u1", "logo_url": "https://x/l.png", "trust_badges": ["identity_verified"], "is_active": True}],
    "users": [{"id": "u1", "full_name": "Ada", "email": "a@x.ug"}, {"id": "r1", "full_name": "Rex", "email": "r@x.ug"}],
}


def test_product_report_enrichment():
    out = rp.enrich_product_reports(_Admin(DB), [{"id": "1", "product_id": "p1", "reporter_id": "r1"}])
    assert out[0]["listing"]["thumbnail_url"] == "https://x/a.jpg"
    assert out[0]["listing"]["seller_name"] == "Ada"
    assert out[0]["reporter"]["full_name"] == "Rex"


def test_seller_report_enrichment():
    out = rp.enrich_seller_reports(_Admin(DB), [{"id": "2", "seller_id": "u1", "reporter_id": "r1"}])
    s = out[0]["seller"]
    assert s["name"] == "Ada Shop" and s["verified"] and s["listing_count"] == 1 and s["slug"] == "ada"
