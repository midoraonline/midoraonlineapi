from datetime import datetime, timedelta, timezone

from feed import config as C
from feed import service, sessions
from feed.snapshots import Snapshot
from feed.stages import StagePlan

NOW = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)


def _snap(n, *, prefix="l", posted=None, plan=None):
    items = [
        {"id": f"{prefix}{i}", "shop": f"s{i}", "lane": "product", "score": 100 - i, "quality": 80,
         "posted": (posted or NOW - timedelta(days=2)).isoformat(), "mode": "organic"}
        for i in range(n)
    ]
    return Snapshot(version="v1", plan=plan or StagePlan(stage=1, eligible=n), items=items)


def _fresh(key="s:abcdef123456"):
    return sessions.start(key, NOW)


def test_seven_listings_give_seven_unique_cards_and_end():
    sess = _fresh()
    ids, more, _ = service.build_session_page(sess, _snap(7), page=1, limit=20)
    assert len(ids) == len(set(ids)) == 7 and not more


def test_thirty_listings_are_all_reached_before_recycling():
    sess, snap = _fresh(), _snap(30)
    p1, more1, _ = service.build_session_page(sess, snap, page=1, limit=20)
    p2, more2, _ = service.build_session_page(sess, snap, page=2, limit=20)
    assert more1 and not more2
    assert len(p1) == 20 and len(p2) == 10 and not set(p1) & set(p2)


def test_reloading_a_page_keeps_the_same_cards():
    sess, snap = _fresh(), _snap(30)
    first, _, _ = service.build_session_page(sess, snap, page=1, limit=20)
    reshuffled = Snapshot(version="v2", plan=snap.plan, items=list(reversed(snap.items)))
    again, _, _ = service.build_session_page(sess, reshuffled, page=1, limit=20)
    assert again == first


def test_new_inventory_enters_the_next_block_not_the_current_one():
    sess = _fresh()
    snap = _snap(25)
    p1, _, _ = service.build_session_page(sess, snap, page=1, limit=20)
    arrived = {"id": "brand_new", "shop": "n", "lane": "product", "score": 999, "quality": 90,
               "posted": (NOW + timedelta(minutes=5)).isoformat(), "mode": "organic"}
    later = Snapshot(version="v2", plan=snap.plan, items=[arrived] + snap.items)
    again, _, _ = service.build_session_page(sess, later, page=1, limit=20)
    p2, _, _ = service.build_session_page(sess, later, page=2, limit=20)
    assert "brand_new" not in again and again == p1
    assert p2[0] == "brand_new"


def test_new_session_shows_unseen_cards_first():
    snap = _snap(30)
    old = _fresh()
    p1, _, _ = service.build_session_page(old, snap, page=1, limit=20)
    new = sessions.start(old.key, NOW + timedelta(hours=9), previous=old)
    fresh_first, _, _ = service.build_session_page(new, snap, page=1, limit=20)
    assert fresh_first[:10] == [f"l{i}" for i in range(20, 30)]
    assert set(fresh_first[10:]) <= set(p1)


def test_session_expiry_and_refresh(monkeypatch):
    store = {}
    monkeypatch.setattr(sessions, "load", lambda db, key: sessions.FeedSession.from_dict(store.get(key)))
    sess = sessions.resolve(None, user_id=None, session_id="abcdef123456", refresh=False, now=NOW)
    store[sess.key] = sess.as_dict()
    same = sessions.resolve(None, user_id=None, session_id="abcdef123456", refresh=False, now=NOW + timedelta(hours=1))
    assert same.id == sess.id
    later = NOW + timedelta(hours=C.FEED_SESSION_HOURS + 1)
    assert sessions.resolve(None, user_id=None, session_id="abcdef123456", refresh=False, now=later).id != sess.id
    assert sessions.resolve(None, user_id=None, session_id="abcdef123456", refresh=True, now=NOW).id != sess.id


def test_signing_in_keeps_the_guest_context(monkeypatch):
    store = {}
    monkeypatch.setattr(sessions, "load", lambda db, key: sessions.FeedSession.from_dict(store.get(key)))
    guest = sessions.resolve(None, user_id=None, session_id="abcdef123456", refresh=False, now=NOW)
    guest.pages["1"] = ["a", "b"]
    store[guest.key] = guest.as_dict()
    user = sessions.resolve(None, user_id="u1", session_id="abcdef123456", refresh=False, now=NOW)
    assert user.key == "u:u1" and user.id == guest.id and user.pages == {"1": ["a", "b"]}


def test_session_keys_need_a_plausible_id():
    assert sessions.key_for("u1", "x") == "u:u1"
    assert sessions.key_for(None, "abcdef123456") == "s:abcdef123456"
    assert sessions.key_for(None, "bad id!") is None and sessions.key_for(None, None) is None


def test_new_listing_count(monkeypatch):
    sess = _fresh()
    sess.pages["1"] = ["l0"]
    snap = _snap(3, posted=NOW + timedelta(minutes=10))
    monkeypatch.setattr(sessions, "load", lambda db, key: sess)
    monkeypatch.setattr(service, "current_snapshot", lambda db: snap)
    assert service.new_listing_count(None, user_id=None, session_id="abcdef123456") == 2


# --- session stays put; own posts refresh it; the type mix survives rings and recents ---

from datetime import timedelta as _td  # noqa: E402


class _Shops:
    def __init__(self, owned):
        self.owned, self._ids = owned, []

    def table(self, _name):
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def in_(self, _k, ids):
        self._ids = ids
        return self

    def limit(self, _n):
        return self

    def execute(self):
        from types import SimpleNamespace

        return SimpleNamespace(data=[{"id": i} for i in self._ids if i in self.owned])


def _session_with_new(shop):
    snap = _snap(30)
    sess = _fresh()
    service.build_session_page(sess, snap, page=1, limit=20)
    sess.known = snap.ids()
    later = _snap(30)
    later.items.append(
        {"id": "new1", "shop": shop, "lane": "product", "score": 99, "quality": 80,
         "posted": (NOW + _td(minutes=5)).isoformat(), "mode": "organic"}
    )
    return sess, later


def test_other_peoples_new_listing_does_not_restart_the_session():
    sess, snap = _session_with_new("someone_else")
    assert service._own_listing_new(_Shops({"mine"}), sess, snap, "u1") is False
    assert [i["id"] for i in service._new_listings(sess, snap)] == ["new1"]  # counted, so the banner can say so


def test_own_new_listing_restarts_the_session():
    sess, snap = _session_with_new("mine")
    assert service._own_listing_new(_Shops({"mine"}), sess, snap, "u1") is True
    assert service._own_listing_new(_Shops({"mine"}), sess, snap, None) is False


def test_type_mix_holds_across_location_rings_and_recents():
    items = []
    for lane, n in (("product", 80), ("service", 40), ("opportunity", 30)):
        for i in range(n):
            items.append({"id": f"{lane[0]}{i}", "shop": f"{lane[0]}s{i}", "lane": lane, "score": 100 - i,
                          "quality": 80, "posted": (NOW - _td(days=2)).isoformat(), "mode": "organic",
                          "cat": f"c{i % 7}"})
    snap = Snapshot(version="v1", plan=StagePlan(stage=3, eligible=len(items), type_mix=True), items=items)

    def rings(pool):  # products are all "local"; services and opportunities only exist further out
        out = [[], [], []]
        for e in pool:
            out[{"product": 0, "service": 1, "opportunity": 2}[e["lane"]]].append(e)
        return out

    sess = _fresh()
    sess.recent = [f"p{i}" for i in range(10)]
    ids, _, _ = service.build_session_page(sess, snap, page=1, limit=40, order=rings)
    by_id = {i["id"]: i["lane"] for i in items}
    for block in (ids[:20], ids[20:40]):
        lanes = [by_id[x] for x in block]
        assert (lanes.count("product"), lanes.count("service"), lanes.count("opportunity")) == (12, 5, 3)
