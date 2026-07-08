"""Canonical browse categories — single source of truth for API validation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryDef:
    slug: str
    label: str
    sort_order: int
    parent_slug: str | None = None


def _slugify(label: str) -> str:
    s = label.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# Top-level category → subcategory labels (ecommerce-aligned, marketplace-friendly).
_CATEGORY_TREE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "food-beverage",
        "Food & Beverage",
        (
            "Fresh Produce & Groceries",
            "Packaged Foods & Snacks",
            "Beverages & Drinks",
            "Bakery & Confectionery",
            "Spices, Sauces & Condiments",
            "Organic & Health Foods",
            "Catering & Ready Meals",
            "Baby & Toddler Food",
            "Coffee, Tea & Hot Drinks",
        ),
    ),
    (
        "fashion",
        "Fashion",
        (
            "Men's Clothing",
            "Women's Clothing",
            "Children's Clothing",
            "Shoes & Footwear",
            "Bags & Luggage",
            "Accessories",
            "Traditional & Cultural Wear",
            "Work & Uniform Wear",
            "Underwear & Sleepwear",
            "Watches (Fashion)",
            "Sunglasses & Eyewear",
        ),
    ),
    (
        "electronics",
        "Electronics",
        (
            "Mobile Phones & Tablets",
            "Phone & Tablet Accessories",
            "Computers & Laptops",
            "Computer Accessories",
            "TVs & Home Entertainment",
            "Audio & Headphones",
            "Cameras & Photography",
            "Gaming Consoles & Accessories",
            "Smart Home & IoT Devices",
            "Wearable Technology",
            "Cables, Chargers & Power Banks",
            "Solar & Power Equipment",
            "Electronic Components & Parts",
        ),
    ),
    (
        "beauty",
        "Beauty",
        (
            "Skincare",
            "Makeup & Cosmetics",
            "Hair Care & Styling",
            "Fragrances & Perfumes",
            "Nail Care",
            "Men's Grooming",
            "Beauty Tools & Accessories",
            "Salon & Spa Products",
            "Bath & Body",
        ),
    ),
    (
        "home-living",
        "Home & Living",
        (
            "Furniture",
            "Home Decor",
            "Kitchen & Dining",
            "Bedding & Linens",
            "Bathroom Essentials",
            "Cleaning & Household Supplies",
            "Lighting & Lamps",
            "Storage & Organization",
            "Garden & Outdoor Living",
            "Home Appliances",
            "Cookware & Bakeware",
            "Curtains, Rugs & Textiles",
        ),
    ),
    (
        "services",
        "Services",
        (
            "Repair & Maintenance",
            "Cleaning Services",
            "Delivery & Logistics",
            "Professional & Consulting",
            "Beauty & Personal Care Services",
            "Education & Tutoring",
            "Tech & IT Services",
            "Events & Entertainment",
            "Photography & Videography",
            "Legal & Financial Services",
            "Home Improvement Services",
            "Health & Wellness Services",
        ),
    ),
    (
        "agriculture",
        "Agriculture",
        (
            "Seeds & Plants",
            "Fertilizers & Pesticides",
            "Farm Tools & Equipment",
            "Livestock & Poultry",
            "Fresh Farm Produce",
            "Animal Feed & Supplements",
            "Irrigation & Water Systems",
            "Greenhouse & Nursery Supplies",
            "Harvesting & Processing",
        ),
    ),
    (
        "health-wellness",
        "Health & Wellness",
        (
            "Vitamins & Supplements",
            "Medical Supplies & Equipment",
            "First Aid & Safety",
            "Personal Care & Hygiene",
            "Fitness & Nutrition",
            "Herbal & Natural Remedies",
            "Mobility & Disability Aids",
            "Maternity & Nursing Care",
            "Sexual Wellness",
        ),
    ),
    (
        "sports-outdoors",
        "Sports & Outdoors",
        (
            "Exercise & Fitness Equipment",
            "Team Sports",
            "Outdoor & Camping Gear",
            "Cycling",
            "Water Sports",
            "Athletics & Running",
            "Sportswear & Activewear",
            "Hunting & Fishing",
            "Yoga & Pilates",
        ),
    ),
    (
        "automotive",
        "Automotive",
        (
            "Car Parts & Accessories",
            "Motorcycles & Boda Boda",
            "Tires & Wheels",
            "Car Electronics & GPS",
            "Oils, Fluids & Lubricants",
            "Tools & Garage Equipment",
            "Car Care & Cleaning",
            "Bicycles & Scooters",
            "Vehicle Batteries",
        ),
    ),
    (
        "books-stationery",
        "Books & Stationery",
        (
            "Books & Literature",
            "School Supplies",
            "Office Supplies",
            "Art & Craft Supplies",
            "Writing Instruments",
            "Notebooks & Paper",
            "Educational Materials",
            "Magazines & Media",
            "Calendars & Planners",
        ),
    ),
    (
        "kids-baby",
        "Kids & Baby",
        (
            "Baby Clothing",
            "Kids Clothing",
            "Diapering & Bathing",
            "Baby Gear & Furniture",
            "Feeding & Nursing",
            "Baby Toys & Activity",
            "Strollers & Car Seats",
            "Kids Shoes & Accessories",
            "Maternity Products",
        ),
    ),
    (
        "pets",
        "Pets",
        (
            "Dog Supplies",
            "Cat Supplies",
            "Bird Supplies",
            "Fish & Aquarium",
            "Pet Food & Treats",
            "Pet Grooming",
            "Pet Toys & Accessories",
            "Pet Health & Medicine",
            "Small Animal Supplies",
        ),
    ),
    (
        "jewelry-watches",
        "Jewelry & Watches",
        (
            "Necklaces & Pendants",
            "Earrings",
            "Rings",
            "Bracelets & Bangles",
            "Watches",
            "Traditional & Cultural Jewelry",
            "Fine Jewelry",
            "Fashion Jewelry",
            "Jewelry Making Supplies",
        ),
    ),
    (
        "toys-games",
        "Toys & Games",
        (
            "Action Figures & Dolls",
            "Board Games & Puzzles",
            "Educational Toys",
            "Outdoor Play Equipment",
            "Remote Control & Electronic Toys",
            "Building & Construction Toys",
            "Arts & Craft Kits",
            "Party Games & Supplies",
            "Collectibles",
        ),
    ),
    (
        "arts-crafts",
        "Arts & Crafts",
        (
            "Painting & Drawing",
            "Sewing & Knitting",
            "Handmade & Artisan Goods",
            "Craft Materials & Supplies",
            "Scrapbooking & Paper Crafts",
            "Beading & Jewelry Making",
            "Woodworking & Carpentry Crafts",
            "Musical Instruments (Handmade)",
            "Custom & Personalized Gifts",
        ),
    ),
    (
        "building-hardware",
        "Building & Hardware",
        (
            "Building Materials",
            "Plumbing Supplies",
            "Electrical Supplies",
            "Paint & Finishes",
            "Tools & Power Tools",
            "Doors, Windows & Locks",
            "Roofing & Insulation",
            "Tiles & Flooring",
            "Safety Equipment & PPE",
            "Garden Tools & Equipment",
        ),
    ),
    (
        "other",
        "Other",
        (
            "General Merchandise",
            "Vintage & Antiques",
            "Wholesale & Bulk",
            "Uncategorized",
        ),
    ),
)


def _build_all_categories() -> tuple[CategoryDef, ...]:
    rows: list[CategoryDef] = []
    for parent_order, (parent_slug, parent_label, children) in enumerate(_CATEGORY_TREE, start=1):
        rows.append(
            CategoryDef(
                slug=parent_slug,
                label=parent_label,
                sort_order=parent_order,
                parent_slug=None,
            )
        )
        for child_index, child_label in enumerate(children, start=1):
            child_slug = _slugify(child_label)
            # Avoid slug collisions with parent or other categories.
            if child_slug == parent_slug:
                child_slug = f"{parent_slug}-{child_slug}"
            rows.append(
                CategoryDef(
                    slug=child_slug,
                    label=child_label,
                    sort_order=parent_order * 100 + child_index,
                    parent_slug=parent_slug,
                )
            )
    return tuple(rows)


ALL_CATEGORIES: tuple[CategoryDef, ...] = _build_all_categories()

CANONICAL_CATEGORIES: tuple[CategoryDef, ...] = tuple(
    c for c in ALL_CATEGORIES if c.parent_slug is None
)


def _norm_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("&", "and").split())


_LABEL_BY_NORM: dict[str, str] = {}
_SLUG_BY_NORM: dict[str, str] = {}
for _c in ALL_CATEGORIES:
    _LABEL_BY_NORM[_norm_key(_c.label)] = _c.label
    _SLUG_BY_NORM[_norm_key(_c.slug)] = _c.label


def category_labels() -> list[str]:
    return [c.label for c in ALL_CATEGORIES]


def top_level_category_labels() -> list[str]:
    return [c.label for c in CANONICAL_CATEGORIES]


def categories_for_prompt() -> str:
    return ", ".join(top_level_category_labels())


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
        f"Invalid category. Choose one of: {', '.join(category_labels()[:20])}…"
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
        {
            "slug": c.slug,
            "label": c.label,
            "sort_order": c.sort_order,
            "parent_slug": c.parent_slug,
        }
        for c in ALL_CATEGORIES
    ]
