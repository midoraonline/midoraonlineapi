from collections import deque
from time import monotonic
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.rate_limit import _hits, _persistent_retry_after as _real_persistent, check_rate_limit


@pytest.fixture(autouse=True)
def _memory_only():
    _hits.clear()
    with patch("core.rate_limit._persistent_retry_after", return_value=None):
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


def test_persistent_store_blocks_with_retry_after():
    # Arrange
    with patch("core.rate_limit._persistent_retry_after", return_value=42):
        # Act
        with pytest.raises(HTTPException) as exc:
            check_rate_limit("test.persist", limit=1, window_seconds=60)
    # Assert
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "42"


def test_persistent_store_allow_skips_memory():
    # Arrange
    with patch("core.rate_limit._persistent_retry_after", return_value=0):
        # Act
        check_rate_limit("test.persist.ok", limit=1, window_seconds=60)
    # Assert
    assert "test.persist.ok" not in _hits


def test_rpc_called_with_bucket_and_limits():
    # Arrange
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = 7
    with patch("db.supabase.get_supabase_admin", return_value=client):
        # Act
        result = _real_persistent("auth.otp:ip:1", 5, 60)
    # Assert
    assert result == 7
    client.rpc.assert_called_once_with(
        "rate_limit_hit", {"p_bucket": "auth.otp:ip:1", "p_limit": 5, "p_window_seconds": 60}
    )


def test_rpc_failure_returns_none():
    # Arrange
    with patch("db.supabase.get_supabase_admin", side_effect=RuntimeError("down")):
        # Act
        result = _real_persistent("b", 5, 60)
    # Assert
    assert result is None
