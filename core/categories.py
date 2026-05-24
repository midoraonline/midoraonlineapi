"""Canonical browse categories — single source of truth for API validation."""

from __future__ import annotations

from dataclasses import dataclass
@dataclass(frozen=True)
class CategoryDef:
    slug: str
    label: str
    sort_order: int


# Shared taxonomy for shops and products (display label stored in DB).
CANONICAL_CATEGORIES: tuple[CategoryDef, ...] = (
    CategoryDef("food-beverage", "Food & Beverage", 1),
    CategoryDef("fashion", "Fashion", 2),
    CategoryDef("electronics", "Electronics", 3),
    CategoryDef("beauty", "Beauty", 4),
    CategoryDef("home-living", "Home & Living", 5),
    CategoryDef("services", "Services", 6),
    CategoryDef("agriculture", "Agriculture", 7),
    CategoryDef("health-wellness", "Health & Wellness", 8),
    CategoryDef("sports-outdoors", "Sports & Outdoors", 9),
    CategoryDef("automotive", "Automotive", 10),
    CategoryDef("books-stationery", "Books & Stationery", 11),
    CategoryDef("kids-baby", "Kids & Baby", 12),
    CategoryDef("pets", "Pets", 13),
    CategoryDef("other", "Other", 99),
)


def _norm_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("&", "and").split())


_LABEL_BY_NORM: dict[str, str] = {}
_SLUG_BY_NORM: dict[str, str] = {}
for _c in CANONICAL_CATEGORIES:
    _LABEL_BY_NORM[_norm_key(_c.label)] = _c.label
    _SLUG_BY_NORM[_norm_key(_c.slug)] = _c.label


def category_labels() -> list[str]:
    return [c.label for c in CANONICAL_CATEGORIES]


def categories_for_prompt() -> str:
    return ", ".join(category_labels())


def normalize_category(value: str | None) -> str | None:
    """Map user/AI input to a canonical label, or None if empty."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    key = _norm_key(raw)
    if key in _LABEL_BY_NORM:
        return _LABEL_BY_NORM[key]
    if key in _SLUG_BY_NORM:
        return _SLUG_BY_NORM[key]
    slugish = key.replace(" ", "-")
    if slugish in _SLUG_BY_NORM:
        return _SLUG_BY_NORM[slugish]
    raise ValueError(
        f"Invalid category. Choose one of: {', '.join(category_labels())}"
    )


def validate_category_field(value: str | None) -> str | None:
    """Pydantic-friendly validator."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return normalize_category(str(value))


def seed_rows() -> list[dict]:
    return [
        {"slug": c.slug, "label": c.label, "sort_order": c.sort_order}
        for c in CANONICAL_CATEGORIES
    ]
