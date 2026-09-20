from collections import deque
from time import monotonic

import pytest
from fastapi import HTTPException

from core.rate_limit import _hits, check_rate_limit


@pytest.fixture(autouse=True)
def _clear_hits():
    _hits.clear()
    yield
    _hits.clear()


def test_allows_hits_under_limit():
    bucket = "test.allow"
    for _ in range(3):
        check_rate_limit(bucket, limit=3, window_seconds=60)


def test_blocks_when_limit_exceeded():
    bucket = "test.block"
    for _ in range(2):
        check_rate_limit(bucket, limit=2, window_seconds=60)

    with pytest.raises(HTTPException) as exc:
        check_rate_limit(bucket, limit=2, window_seconds=60)
    assert exc.value.status_code == 429
    assert exc.value.headers and exc.value.headers.get("Retry-After")


def test_window_expiry_allows_new_hits(monkeypatch):
    bucket = "test.window"
    start = monotonic()
    check_rate_limit(bucket, limit=1, window_seconds=10)

    monkeypatch.setattr("core.rate_limit.monotonic", lambda: start + 11)

    check_rate_limit(bucket, limit=1, window_seconds=10)
    assert isinstance(_hits[bucket], deque)
