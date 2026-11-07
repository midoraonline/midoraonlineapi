from datetime import datetime, timedelta, timezone

from feed import service, sessions, snapshots
from feed.stages import StagePlan


def _snap(*ids, posted=None):
    items = [{"id": i, "posted": posted, "mode": "organic"} for i in ids]
    return snapshots.Snapshot(version="v1", plan=StagePlan.from_dict({}), items=items)


def _sess(known, shown, started):
    sess = sessions.start("u:1", started)
    sess.known = list(known)
    sess.pages = {"1": list(shown)}
    return sess


def test_listing_approved_after_session_start_counts_as_new():
    started = datetime.now(timezone.utc)
    old_post = (started - timedelta(hours=1)).isoformat()
    sess = _sess(["a", "b"], ["a"], started)
    assert service._new_since(sess, _snap("a", "b", posted=old_post)) is False
    assert service._new_since(sess, _snap("a", "b", "c", posted=old_post)) is True


def test_repost_after_session_start_counts_as_new():
    started = datetime.now(timezone.utc) - timedelta(minutes=10)
    sess = _sess(["a", "b"], ["a"], started)
    assert service._new_since(sess, _snap("a", "b", posted=datetime.now(timezone.utc).isoformat())) is True


def test_session_without_known_ids_never_restarts():
    sess = _sess([], ["a"], datetime.now(timezone.utc))
    assert service._new_since(sess, _snap("a", "z")) is False


def test_known_ids_survive_round_trip():
    sess = _sess(["a", "b"], ["a"], datetime.now(timezone.utc))
    again = sessions.FeedSession.from_dict(sess.as_dict())
    assert again is not None and again.known == ["a", "b"]


def test_status_change_rebuilds_feed(monkeypatch):
    from feed import listeners

    calls = []
    monkeypatch.setattr("feed.invalidate.invalidate_feed_caches", lambda *a, **k: calls.append(1))
    listeners.on_product_status_changed(object())
    assert calls == [1]
