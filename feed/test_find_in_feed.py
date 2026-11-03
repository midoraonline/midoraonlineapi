from datetime import datetime, timedelta, timezone

from feed import service, sessions
from feed.snapshots import Snapshot
from feed.stages import StagePlan
from shop import listing_actions as LA

NOW = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)


def _snap(n):
    items = [
        {"id": f"l{i}", "shop": f"s{i % 12}", "lane": "product", "score": 100 - i, "quality": 80,
         "posted": (NOW - timedelta(days=2)).isoformat(), "mode": "organic"}
        for i in range(n)
    ]
    return Snapshot(version="v1", plan=StagePlan(stage=1, eligible=n), items=items)


def _patch(monkeypatch, snap):
    monkeypatch.setattr(service, "current_snapshot", lambda db: snap)


def test_position_matches_what_a_new_visitor_scrolls_through(monkeypatch):
    snap = _snap(95)
    _patch(monkeypatch, snap)
    order = service.neutral_home_order(None)
    sess = sessions.start("s:abcdef123456", NOW)
    scrolled = []
    for page in (1, 2, 3):
        ids, _, _, _ = service._page_ids(None, sess, snap, page=page, limit=service.HOME_PAGE_SIZE)
        scrolled += ids
    assert order[: len(scrolled)] == scrolled


def test_position_is_found_and_counted_from_one(monkeypatch):
    _patch(monkeypatch, _snap(60))
    first = service.neutral_home_order(None)[0]
    position, scanned, in_ranking = LA._home_position(None, first)
    assert (position, in_ranking) == (1, True) and scanned == 1


def test_position_stops_scanning_at_the_listing(monkeypatch):
    _patch(monkeypatch, _snap(100))
    target = service.neutral_home_order(None)[57]
    position, scanned, _ = LA._home_position(None, target)
    assert position == 58 and scanned == 58


def test_listing_outside_the_ranking_is_reported(monkeypatch):
    _patch(monkeypatch, _snap(30))
    position, _, in_ranking = LA._home_position(None, "not-ranked")
    assert position is None and in_ranking is False


def test_neutral_session_ignores_history_and_area(monkeypatch):
    snap = _snap(30)
    _patch(monkeypatch, snap)
    old = sessions.start("u:1", NOW)
    old.area = "kampala"
    service.build_session_page(old, snap, page=1, limit=20)
    seen_first = old.pages["1"]
    fresh = sessions.start("u:1", NOW + timedelta(hours=1), area="all")
    ids, _, _ = service.build_session_page(fresh, snap, page=1, limit=20)
    assert fresh.recent == [] and fresh.area == "all" and ids == seen_first


def test_later_pages_never_restart_the_session_over_an_area_change(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "get_supabase_admin", lambda: None)
    monkeypatch.setattr(service, "_session_home_page", lambda db, sess, **kw: calls.append(sess) or "page")
    seen = []

    def fake_resolve(db, **kw):
        seen.append(kw["area"])
        return sessions.start("s:abcdef123456", NOW)

    monkeypatch.setattr(sessions, "resolve", fake_resolve)
    service.get_session_feed(user_id=None, session_id="abcdef123456", page=2, limit=40, area="kampala")
    service.get_session_feed(user_id=None, session_id="abcdef123456", page=1, limit=40, area="kampala")
    assert seen == [None, "kampala"]
