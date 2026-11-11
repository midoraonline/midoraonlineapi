from __future__ import annotations

import math
from dataclasses import dataclass, field

from feed import config as C

LANES = ("product", "service", "opportunity")


def lane_of(item_type: str | None) -> str:
    kind = (item_type or "product").strip().lower()
    if kind == "service":
        return "service"
    if kind in ("opportunity", "job"):
        return "opportunity"
    return "product"


def stage_for(eligible: int) -> int:
    return sum(1 for minimum in C.stage_minimums() if eligible >= minimum)


def lane_targets() -> dict[str, int]:
    return {lane: math.ceil(share * C.BLOCK_SIZE) for lane, share in C.lane_shares().items()}


@dataclass(frozen=True)
class StagePlan:
    stage: int
    eligible: int
    by_lane: dict[str, int] = field(default_factory=dict)
    type_mix: bool = False
    exploration_per_block: int = 0
    paid: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "eligible": self.eligible,
            "by_lane": dict(self.by_lane),
            "type_mix": self.type_mix,
            "exploration_per_block": self.exploration_per_block,
            "paid": self.paid,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object] | None) -> "StagePlan":
        raw = raw or {}
        by_lane = raw.get("by_lane") if isinstance(raw.get("by_lane"), dict) else {}
        return cls(
            stage=int(raw.get("stage") or 0),
            eligible=int(raw.get("eligible") or 0),
            by_lane={str(k): int(v) for k, v in by_lane.items()},
            type_mix=bool(raw.get("type_mix")),
            exploration_per_block=int(raw.get("exploration_per_block") or 0),
            paid=bool(raw.get("paid")),
        )


def plan_for(by_lane: dict[str, int], *, eligible: int | None = None) -> StagePlan:
    total = sum(by_lane.values()) if eligible is None else eligible
    stage = stage_for(total)
    exploration = 0
    if stage == 3:
        exploration = C.EXPLORATION_STAGE_3
    elif stage >= 4:
        exploration = C.EXPLORATION_STAGE_4
    return StagePlan(
        stage=stage,
        eligible=total,
        by_lane=dict(by_lane),
        type_mix=stage >= C.TYPE_MIX_MIN_STAGE,
        exploration_per_block=exploration,
        paid=stage >= C.PAID_MIN_STAGE,
    )
