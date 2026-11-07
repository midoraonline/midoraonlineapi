import asyncio

from listingModeration import shops
from listingModeration.schemas import ModerationStatus


def _no_images(monkeypatch):
    async def fake_phash(urls, bad):
        return None, []

    monkeypatch.setattr(shops.phash, "check", fake_phash)


def test_clean_shop_is_approved_without_ai(monkeypatch):
    _no_images(monkeypatch)
    monkeypatch.setattr("platform_settings.flags.ai_moderation_enabled", lambda: False)
    monkeypatch.setattr(shops.profanity, "score", lambda *_: 0.01)
    decision = asyncio.run(shops.moderate_shop({"name": "Kampala Bakery", "description": "Fresh bread daily"}, []))
    assert decision.status == ModerationStatus.APPROVED


def test_banned_keyword_blocks_shop(monkeypatch):
    _no_images(monkeypatch)
    monkeypatch.setattr(shops.keywords, "check", lambda title, body: "badword")
    decision = asyncio.run(shops.moderate_shop({"name": "badword shop"}, []))
    assert decision.status == ModerationStatus.REJECTED


def test_vulgar_text_flagged_by_model(monkeypatch):
    _no_images(monkeypatch)
    monkeypatch.setattr("platform_settings.flags.ai_moderation_enabled", lambda: True)
    monkeypatch.setattr(shops.profanity, "score", lambda *_: None)

    async def text_check(title, body):
        return {"max_score": 0.99}

    monkeypatch.setattr(shops.text_moderation, "check", text_check)
    decision = asyncio.run(shops.moderate_shop({"name": "Some shop", "about": "nasty words"}, []))
    assert decision.status == ModerationStatus.REJECTED


def test_shop_text_and_images():
    shop = {
        "name": " Ada ",
        "description": "Shoes",
        "about": "Since 2010",
        "logo_url": "https://x/logo.png",
        "theme_config": {"metadata": {"banner_url": "https://x/banner.png"}},
    }
    assert shops.shop_text(shop) == ("Ada", "Shoes \nSince 2010")
    assert shops.shop_images(shop) == ["https://x/logo.png", "https://x/banner.png"]


def test_approved_shop_is_activated(monkeypatch):
    activated = []

    class _Q:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            return type("R", (), {"data": self.rows})()

    class _Admin:
        def table(self, _name):
            return _Q([{"id": "s1", "owner_id": "u1", "name": "Ada", "is_active": False, "is_personal": False}])

    async def approve(shop, bad):
        from listingModeration.schemas import ModerationDecision

        return ModerationDecision(status=ModerationStatus.APPROVED)

    monkeypatch.setattr("db.supabase.get_supabase_admin", lambda: _Admin())
    monkeypatch.setattr("listingModeration.service.load_bad_image_hashes", lambda: [])
    monkeypatch.setattr(shops, "moderate_shop", approve)
    monkeypatch.setattr("admin.service.set_shop_active", lambda sid, on: activated.append((sid, on)))
    monkeypatch.setattr(shops, "_notify_owner", lambda *a, **k: None)
    monkeypatch.setattr("core.next_cache.revalidate_nextjs_cache_tag", lambda *_: None)
    decision = asyncio.run(shops.review_shop("s1"))
    assert decision.status == ModerationStatus.APPROVED
    assert activated == [("s1", True)]
