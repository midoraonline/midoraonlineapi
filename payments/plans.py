BASIC = "basic"
STANDARD = "standard"
PREMIUM = "premium"

PLAN_ORDER = [BASIC, STANDARD, PREMIUM]

PLANS = { 
        BASIC: {
            "key": BASIC,
            "name": "Basic",
            "price_ugx": 0,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 1,
            "max_products_per_shop": 10,
            "analytics_enabled": False,
            "frequency_per_day": 1,
            "frequency_per_day_boosted": 0, 
        },
        STANDARD: {
            "key": STANDARD,
            "name": "Standard",
            "price_ugx": 30000,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 3,
            "max_products_per_shop": 20,
            "analytics_enabled": True,
            "frequency_per_day": 0,
            "frequency_per_day_boosted": 2,
    
        },
        PREMIUM: {
            "key": PREMIUM,
            "name": "Premium",
            "price_ugx": 80000,
            "currency": "UGX",
            "billing_period_days": 30,
            "max_shops": 10,
            "max_products_per_shop": 50,
            "analytics_enabled": True,
            "frequency_per_day": 0,
            "frequency_per_day_boosted": 5,
        },
}

DEFAULT_PLAN = BASIC


def is_valid_tier(tier: str | None) -> bool:
    return tier in PLANS


def get_plan(tier: str | None) -> dict:
    return PLANS.get(tier or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def list_plans() -> list[dict]:
    return [PLANS[key] for key in PLAN_ORDER]
