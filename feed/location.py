"""Buyer browsing area: LocationFit by listing type, geographic rings and 'why it's shown' labels."""

from __future__ import annotations

import re
from typing import Any, Callable

from feed import config as C
from feed.stages import StagePlan, lane_of

ALL_UGANDA = "all"

AREAS: dict[str, dict[str, Any]] = {
    "kampala": {
        "label": "Kampala",
        "aliases": (
            "kampala", "ntinda", "kololo", "nakawa", "kawempe", "makindye", "rubaga", "lubaga", "bugolobi",
            "naguru", "wandegeya", "kansanga", "muyenga", "kabalagala", "nakasero", "bukoto", "kisaasi",
            "kyanja", "kamwokya", "mulago", "kibuli", "nsambya", "kisementi", "luzira", "mutungo", "kyebando",
        ),
        "nearby": ("wakiso", "mukono", "entebbe"),
    },
    "wakiso": {
        "label": "Wakiso",
        "aliases": (
            "wakiso", "kira", "najjera", "namugongo", "kyaliwajjala", "nansana", "gayaza", "matugga",
            "bweyogerere", "kireka", "kajjansi", "kasangati", "kakiri", "namasuba", "kitende", "bulenga",
        ),
        "nearby": ("kampala", "entebbe", "mukono"),
    },
    "mukono": {
        "label": "Mukono",
        "aliases": ("mukono", "seeta", "lugazi", "namanve", "goma"),
        "nearby": ("kampala", "wakiso"),
    },
    "entebbe": {
        "label": "Entebbe",
        "aliases": ("entebbe", "abaita", "kitooro", "kitoro", "katabi", "nkumba", "kigungu", "lunyo"),
        "nearby": ("wakiso", "kampala"),
    },
}

_ALIASES = sorted(((alias, key) for key, a in AREAS.items() for alias in a["aliases"]), key=lambda x: -len(x[0]))
_ONLINE = ("online", "remote", "anywhere", "nationwide", "countrywide", "all uganda")


def normalize_area(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in (ALL_UGANDA, "all uganda", "uganda"):
        return ALL_UGANDA
    return text if text in AREAS else area_of(text)


def area_label(key: str | None) -> str:
    if not key or key == ALL_UGANDA:
        return "All Uganda"
    return AREAS.get(key, {}).get("label") or key.title()


def area_of(text: Any) -> str | None:
    value = str(text or "").lower()
    if not value:
        return None
    for alias, key in _ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", value):
            return key
    return None


def _areas(values: Any) -> set[str]:
    items = values if isinstance(values, list) else [values] if values else []
    out: set[str] = set()
    for v in items:
        text = str(v or "").lower()
        if any(word in text for word in _ONLINE):
            out.add(ALL_UGANDA)
        key = area_of(v)
        if key:
            out.add(key)
    return out


def _truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in ("true", "yes", "1")


def geo_of(row: dict[str, Any]) -> dict[str, Any]:
    """Compact location facts stored on snapshot items."""
    meta = row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {}
    place = str(row.get("location_name") or "")
    online = any(word in place.lower() for word in ("online", "remote"))
    geo: dict[str, Any] = {}
    if area_of(place):
        geo["a"] = area_of(place)
    if place and not geo.get("a") and not online:
        geo["p"] = place[:40]
    if online:
        geo["o"] = 1
    delivery = _areas(meta.get("delivery_areas"))
    if _truthy(meta.get("nationwide_delivery")):
        delivery.add(ALL_UGANDA)
    if delivery:
        geo["d"] = sorted(delivery)
    served = _areas(meta.get("service_areas")) | _areas(meta.get("service_area"))
    if served:
        geo["s"] = sorted(served)
    if _truthy(meta.get("travels_to_customer")):
        geo["t"] = 1
    if _truthy(meta.get("remote_service")):
        geo["r"] = 1
    mode = str(meta.get("work_mode") or "").strip().lower()
    if mode in ("remote", "hybrid", "on_site"):
        geo["w"] = mode
    if _truthy(meta.get("relocation_supported")):
        geo["rel"] = 1
    return geo


def fit(lane: str, geo: dict[str, Any], area: str) -> tuple[float, str, str | None]:
    """(LocationFit 0-100, ring local|nearby|wider, reason label) for one listing."""
    label = area_label(area)
    home = geo.get("a")
    nearby = home in AREAS.get(area, {}).get("nearby", ())
    if lane == "service":
        served = set(geo.get("s") or [])
        if area in served:
            return 100.0, "local", f"Serves {label}"
        if geo.get("r") or geo.get("o"):
            return 90.0, "wider", "Remote service"
        if ALL_UGANDA in served:
            return 85.0, "wider", "Serves all of Uganda"
        if not served and home == area:
            return 70.0, "local", f"Based in {label}"
        if nearby and geo.get("t"):
            return 80.0, "nearby", f"Travels from {area_label(home)}"
        if geo.get("t"):
            return 40.0, "wider", "May travel to you"
        if nearby and not served:
            return 40.0, "nearby", f"Based in {area_label(home)}"
        return 0.0, "wider", None
    if lane == "opportunity":
        if geo.get("w") == "remote" or geo.get("o"):
            return 100.0, "wider", "Remote opportunity"
        if home == area:
            return 100.0, "local", f"In {label}"
        if nearby:
            return 80.0, "nearby", f"In {area_label(home)}" + (" · hybrid" if geo.get("w") == "hybrid" else "")
        if geo.get("rel"):
            return 60.0, "wider", "Relocation supported"
        return (20.0, "wider", f"In {area_label(home)}") if home or geo.get("p") else (20.0, "wider", None)
    delivery = set(geo.get("d") or [])
    if home == area:
        return 100.0, "local", f"In {label}"
    if nearby:
        return 80.0, "nearby", f"In {area_label(home)}"
    if area in delivery:
        return 75.0, "nearby", f"Delivers to {label}"
    if ALL_UGANDA in delivery or geo.get("o"):
        return 70.0, "wider", "Delivers nationwide" if ALL_UGANDA in delivery else "Online"
    if home or geo.get("p"):
        return 25.0, "wider", f"Pickup in {area_label(home) if home else geo.get('p')}"
    return 10.0, "wider", None


def location_weight(lane: str, eligible: int) -> float:
    if eligible < C.MVP_MID_MIN:
        return 0.0
    if eligible < C.MVP_LATE_MIN:
        return C.LOCATION_WEIGHT_MID
    if lane == "service":
        return C.LOCATION_WEIGHT_SERVICE
    if lane == "opportunity":
        return C.LOCATION_WEIGHT_OPPORTUNITY
    return C.LOCATION_WEIGHT_PRODUCT


def reason_for(item: dict[str, Any], area: str | None) -> str | None:
    if not area or area == ALL_UGANDA:
        return None
    return fit(item.get("lane") or lane_of(None), item.get("geo") or {}, area)[2]


def split_query_place(query: str) -> tuple[str, str | None]:
    """'plumber in Masaka' -> ('plumber', 'Masaka'); 'phones ntinda' -> ('phones', 'ntinda')."""
    text = (query or "").strip()
    m = re.search(r"\b(?:in|near|around|at)\s+([A-Za-z][A-Za-z\s'-]{1,30})$", text, re.IGNORECASE)
    if m:
        return text[: m.start()].strip(), m.group(1).strip()
    lowered = text.lower()
    for alias, _key in _ALIASES:
        hit = re.search(rf"\b{re.escape(alias)}\b", lowered)
        if hit:
            rest = (text[: hit.start()] + text[hit.end() :]).strip()
            return re.sub(r"\s{2,}", " ", rest), text[hit.start() : hit.end()]
    return text, None


def ring_order(area: str | None, plan: StagePlan) -> Callable[[list[dict[str, Any]]], list[list[dict[str, Any]]]] | None:
    if not area or area == ALL_UGANDA:
        return None

    def order(pool: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        rings: dict[str, list[dict[str, Any]]] = {"local": [], "nearby": [], "wider": []}
        for entry in pool:
            value, ring, _ = fit(entry["lane"], entry.get("geo") or {}, area)
            weight = location_weight(entry["lane"], plan.eligible) / 100.0
            entry["score"] = entry["score"] * (1.0 - weight) + value * weight
            rings[ring].append(entry)
        if plan.eligible < C.MVP_LATE_MIN:
            return [pool]
        return [rings["local"], rings["nearby"], rings["wider"]]

    return order
