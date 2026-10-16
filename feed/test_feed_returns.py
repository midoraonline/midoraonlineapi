"""Cache-hit edge cases in get_algorithm_feed always return a 3-tuple."""
from datetime import datetime, timezone

import db.supabase as dbs
from feed import service


def _setup(monkeypatch, ids, *, exclusions=None, rows=None):
    monkeypatch.setattr(dbs, "get_supabase_admin", lambda: object())
    monkeypatch.setattr(service.C, "refresh_from_db", lambda *a, **k: {})
    monkeypatch.setattr(
        service, "_load_cached_ranked_ids",
        lambda db, uid: (ids, True, datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(service, "_merge_recent_unseen_ids", lambda db, r, t, **k: r)
    monkeypatch.setattr(
        service, "_apply_runtime_exclusions",
        lambda r, **k: [i for i in r if i not in (exclusions or set())],
    )
    monkeypatch.setattr(service, "_fetch_products_by_ids", lambda db, pids: rows or [])


def test_empty_cached_page_returns_three_values(monkeypatch):
    _setup(monkeypatch, ["a", "b"], exclusions={"a", "b"})
    out = service.get_algorithm_feed(object(), user_id="u", page=2, limit=10)
    assert out == ([], False, None)


def test_page_two_dead_ids_rescore_instead_of_empty(monkeypatch):
    _setup(monkeypatch, [f"p{i}" for i in range(30)])
    called = {}

    def boom(*a, **k):
        called["rescore"] = True
        raise RuntimeError("rescore reached")

    monkeypatch.setattr(service.sig, "collect_user_signals", boom)
    try:
        service.get_algorithm_feed(object(), user_id="u", page=2, limit=10)
    except RuntimeError:
        pass
    assert called.get("rescore")
