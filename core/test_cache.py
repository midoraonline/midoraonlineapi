import time

import pytest

from core import cache


class FakeRedis:
    def __init__(self):
        self.data: dict[str, tuple[str, float | None]] = {}

    def _live(self, name):
        hit = self.data.get(name)
        if hit and hit[1] is not None and hit[1] <= time.monotonic():
            self.data.pop(name, None)
            return None
        return hit

    def ping(self):
        return True

    def get(self, name):
        hit = self._live(name)
        return hit[0] if hit else None

    def set(self, name, value, ex=None, nx=False):
        if nx and self._live(name):
            return None
        self.data[name] = (str(value), time.monotonic() + ex if ex else None)
        return True

    def delete(self, *names):
        for n in names:
            self.data.pop(n, None)

    def incr(self, name):
        hit = self._live(name)
        n = int(hit[0]) + 1 if hit else 1
        self.data[name] = (str(n), hit[1] if hit else None)
        return n

    def expire(self, name, seconds):
        hit = self._live(name)
        if hit:
            self.data[name] = (hit[0], time.monotonic() + seconds)

    def ttl(self, name):
        hit = self._live(name)
        if not hit:
            return -2
        return -1 if hit[1] is None else int(hit[1] - time.monotonic()) + 1


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "_client", fake)
    yield fake
    cache.reset_redis()


def test_json_round_trip_and_lock(fake_redis):
    assert cache.set_json(cache.key("t"), {"a": 1}, ttl_seconds=10)
    assert cache.get_json(cache.key("t")) == {"a": 1}
    assert cache.acquire_lock(cache.key("lock"), ttl_seconds=10)
    assert not cache.acquire_lock(cache.key("lock"), ttl_seconds=10)
    cache.release_lock(cache.key("lock"))
    assert cache.acquire_lock(cache.key("lock"), ttl_seconds=10)


def test_generation_bump(fake_redis):
    g = cache.key("gen")
    assert cache.generation(g) == 0
    cache.bump(g)
    assert cache.generation(g) == 1


def test_rate_limit_uses_redis_window(fake_redis, monkeypatch):
    from fastapi import HTTPException

    from core import rate_limit

    monkeypatch.setattr(rate_limit, "_persistent_retry_after", lambda *a: pytest.fail("db should not be hit"))
    for _ in range(3):
        rate_limit.check_rate_limit("t:ip:1", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as err:
        rate_limit.check_rate_limit("t:ip:1", limit=3, window_seconds=60)
    assert err.value.status_code == 429


def test_snapshot_shared_across_workers(fake_redis):
    from feed import snapshots
    from feed.stages import StagePlan

    class NoDB:
        def table(self, *_a):
            raise AssertionError("postgres should not be read when redis has the order")

    snap = snapshots.Snapshot(version="abc", plan=StagePlan(stage=1, eligible=2), items=[{"id": "p1", "shop": "s", "score": 1.0, "mode": "organic"}])

    class WriteOnly:
        def table(self, *_a):
            raise RuntimeError("no db")

    snapshots.save(WriteOnly(), snap)
    snapshots.clear_memory()
    with snapshots._lock:
        snapshots._memory.clear()
    got = snapshots.current(NoDB())
    assert got is not None and got.version == "abc" and got.ids() == ["p1"]
    snapshots.invalidate(WriteOnly())
    assert snapshots.current(NoDB()) is None
    assert snapshots.latest(NoDB()).version == "abc"


def test_unconfigured_redis_degrades(monkeypatch):
    cache.reset_redis()
    monkeypatch.setattr(cache, "get_settings", lambda: type("S", (), {"redis_url": ""})())
    assert cache.get_redis() is None
    assert cache.get_json("x") is None
    assert cache.acquire_lock("x", ttl_seconds=1) is True
    assert cache.hit_window("x", window_seconds=1) is None
    cache.reset_redis()
