from datetime import datetime, timezone

from shop import listing_stats as LS


class _RPC:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        rows = self.rows.get(name, [])
        return type("Q", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()


def test_since_is_local_midnight_days_back():
    now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
    assert LS.since_for(1, now) == datetime(2026, 10, 31, 21, tzinfo=timezone.utc)
    assert LS.since_for(7, now) == datetime(2026, 10, 25, 21, tzinfo=timezone.utc)


def test_rates_and_contacts():
    row = {**LS.empty(), "impressions": 200, "views": 20, "whatsapp_clicks": 3, "call_clicks": 1, "messages": 1}
    out = LS.with_rates(row)
    assert out["contacts"] == 5
    assert out["view_rate"] == 0.1 and out["contact_rate"] == 0.25
    assert LS.with_rates(LS.empty())["view_rate"] == 0.0


def test_by_listing_fills_missing_and_totals():
    db = _RPC({"listing_activity_stats": [{"listing_id": "a", "impressions": 5, "views": 2, "reports": 1}]})
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    stats = LS.by_listing(db, ["a", "b"], since)
    assert stats["a"]["impressions"] == 5 and stats["a"]["reports"] == 1
    assert stats["b"] == LS.empty()
    assert LS.totals(stats)["views"] == 2


def test_daily_is_zero_filled_in_local_days():
    db = _RPC({"listing_activity_daily": [{"day": "2026-11-01", "impressions": 4, "views": 1, "contacts": 1}]})
    since = LS.since_for(3, datetime(2026, 11, 1, 10, tzinfo=timezone.utc))
    series = LS.daily(db, ["a"], since, 3)
    assert [d["day"] for d in series] == ["2026-10-30", "2026-10-31", "2026-11-01"]
    assert series[-1] == {"day": "2026-11-01", "impressions": 4, "views": 1, "contacts": 1}
    assert series[0]["impressions"] == 0


def test_rpc_failure_returns_zeros():
    class Broken:
        def rpc(self, *_a):
            raise RuntimeError("missing function")

    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert LS.by_listing(Broken(), ["a"], since) == {"a": LS.empty()}
    assert LS.daily(Broken(), ["a"], since, 2)[1]["views"] == 0
