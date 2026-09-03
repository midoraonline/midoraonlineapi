"""Analytics event ingest.

Accepts a batch of events from the browser and appends them to
`analytics_events`. The endpoint is deliberately generous:

  * anonymous callers are allowed (guests search, browse, WhatsApp-click)
  * client-supplied `actor_id` is ignored — we bind actor from the JWT
  * client-supplied `ts` is accepted but capped at "now" (no future dates)
  * unknown event types are stored anyway; new insights should be new
    queries, not new schema migrations

Rate limiting is TODO — for now this scales with Supabase inserts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from core.security import get_optional_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap payload size to avoid a hostile client shipping megabytes of JSON.
MAX_BATCH_SIZE = 50
MAX_PROPERTIES_KEYS = 40


class AnalyticsEventIn(BaseModel):
    """Wire format for a single event coming from the browser."""

    event_type: str = Field(..., min_length=1, max_length=80)
    target_type: str | None = Field(default=None, max_length=40)
    target_id: str | None = Field(default=None, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)
    properties: dict[str, Any] = Field(default_factory=dict)
    source: Literal["web", "mobile", "server"] = "web"
    # Client timestamp is informational; server clamps it to now if it's
    # in the future so nothing can be back- or forward-dated to skew trends.
    client_ts: datetime | None = None


class AnalyticsBatch(BaseModel):
    events: list[AnalyticsEventIn]


class AnalyticsAck(BaseModel):
    accepted: int
    rejected: int


def _clean_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Drop obviously oversized property blobs; keep the shape."""
    if not props:
        return {}
    if len(props) > MAX_PROPERTIES_KEYS:
        return dict(list(props.items())[:MAX_PROPERTIES_KEYS])
    return props


@router.post("/events", response_model=AnalyticsAck, status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(
    batch: AnalyticsBatch,
    current_user_id: Annotated[str | None, Depends(get_optional_user_id)] = None,
) -> AnalyticsAck:
    if not batch.events:
        return AnalyticsAck(accepted=0, rejected=0)

    if len(batch.events) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"batch exceeds {MAX_BATCH_SIZE} events",
        )

    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    rejected = 0

    for ev in batch.events:
        # Never trust a client-supplied future timestamp.
        ts = ev.client_ts if (ev.client_ts and ev.client_ts <= now) else now
        try:
            rows.append(
                {
                    "event_type": ev.event_type,
                    "actor_id": current_user_id,
                    "session_id": ev.session_id,
                    "target_type": ev.target_type,
                    "target_id": ev.target_id,
                    "properties": _clean_properties(ev.properties),
                    "source": ev.source,
                    "ts": ts.isoformat(),
                }
            )
        except ValidationError:
            rejected += 1

    if not rows:
        return AnalyticsAck(accepted=0, rejected=rejected)

    admin = get_supabase_admin()
    try:
        admin.table("analytics_events").insert(rows).execute()
    except Exception as exc:
        # Never surface DB internals to a public endpoint.
        logger.warning("analytics ingest failed for %d rows: %s", len(rows), exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="analytics ingest unavailable",
        ) from exc

    return AnalyticsAck(accepted=len(rows), rejected=rejected)
