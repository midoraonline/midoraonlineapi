"""Per-buyer Home sessions: cards already shown stay put, the next page sees current inventory."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core import cache
from feed import config as C
from feed.signals import parse_timestamp

logger = logging.getLogger(__name__)

TABLE = "feed_sessions"
_memory: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


@dataclass
class FeedSession:
    key: str
    id: str
    started_at: str
    expires_at: str
    pages: dict[str, list[str]] = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    area: str | None = None
    known: list[str] = field(default_factory=list)

    def shown(self) -> list[str]:
        out: list[str] = []
        for n in sorted(self.pages, key=int):
            out.extend(self.pages[n])
        return out

    def expired(self, now: datetime) -> bool:
        end = parse_timestamp(self.expires_at)
        return end is None or now >= end

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "id": self.id, "started_at": self.started_at, "expires_at": self.expires_at,
            "pages": self.pages, "recent": self.recent, "area": self.area, "known": self.known,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "FeedSession | None":
        if not isinstance(raw, dict) or not raw.get("key") or not raw.get("id"):
            return None
        pages = raw.get("pages") if isinstance(raw.get("pages"), dict) else {}
        return cls(
            key=str(raw["key"]),
            id=str(raw["id"]),
            started_at=str(raw.get("started_at") or ""),
            expires_at=str(raw.get("expires_at") or ""),
            pages={str(k): [str(i) for i in v] for k, v in pages.items() if isinstance(v, list)},
            recent=[str(i) for i in raw.get("recent") or []],
            area=raw.get("area") or None,
            known=[str(i) for i in raw.get("known") or []],
        )


def key_for(user_id: str | None, session_id: str | None) -> str | None:
    if user_id:
        return f"u:{user_id}"
    sid = (session_id or "").strip()
    if 8 <= len(sid) <= 80 and sid.replace("-", "").isalnum():
        return f"s:{sid}"
    return None


def _redis_key(key: str) -> str:
    return cache.key("feed", "sess", key)


def _ttl_seconds() -> float:
    return max(C.FEED_SESSION_HOURS, C.RECENTLY_SHOWN_HOURS) * 3600.0


def load(db: Any, key: str) -> FeedSession | None:
    raw = cache.get_json(_redis_key(key))
    if raw is None and db is not None:
        try:
            rows = db.table(TABLE).select("payload").eq("session_key", key).limit(1).execute().data or []
            raw = rows[0].get("payload") if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("feed session read skipped: %s", exc)
    if raw is None:
        with _lock:
            raw = _memory.get(key)
    return FeedSession.from_dict(raw)


def save(db: Any, sess: FeedSession) -> None:
    payload = sess.as_dict()
    with _lock:
        if len(_memory) > 5000:
            _memory.clear()
        _memory[sess.key] = payload
    if cache.set_json(_redis_key(sess.key), payload, ttl_seconds=_ttl_seconds()):
        return
    if db is None:
        return
    try:
        db.table(TABLE).upsert(
            {"session_key": sess.key, "payload": payload, "expires_at": sess.expires_at,
             "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="session_key",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("feed session write skipped: %s", exc)


def start(key: str, now: datetime, previous: FeedSession | None = None, area: str | None = None) -> FeedSession:
    recent: list[str] = []
    if previous is not None:
        started = parse_timestamp(previous.started_at)
        if started and now - started <= timedelta(hours=C.RECENTLY_SHOWN_HOURS):
            recent = previous.shown() + [i for i in previous.recent if i not in set(previous.shown())]
    return FeedSession(
        key=key,
        id=uuid.uuid4().hex[:12],
        started_at=now.isoformat(),
        expires_at=(now + timedelta(hours=C.FEED_SESSION_HOURS)).isoformat(),
        recent=recent[:800],
        area=area if area is not None else (previous.area if previous else None),
    )


def resolve(
    db: Any,
    *,
    user_id: str | None,
    session_id: str | None,
    refresh: bool,
    now: datetime,
    area: str | None = None,
) -> FeedSession | None:
    key = key_for(user_id, session_id)
    if key is None:
        return None
    sess = load(db, key)
    if sess is None and user_id:
        guest_key = key_for(None, session_id)
        guest = load(db, guest_key) if guest_key else None
        if guest is not None and not guest.expired(now):
            sess = FeedSession(**{**guest.as_dict(), "key": key})
    area_changed = area is not None and sess is not None and (sess.area or "") != area
    if sess is None or refresh or sess.expired(now) or area_changed:
        sess = start(key, now, previous=sess, area=area)
    return sess
