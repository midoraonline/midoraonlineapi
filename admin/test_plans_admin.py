import pytest
from fastapi import HTTPException

from admin.routes.plans_admin import clean_plan_patch


def test_clean_plan_patch_ok():
    out = clean_plan_patch({"price_ugx": "40000", "analytics_enabled": True, "features": ["A", " ", "B"], "max_photos": 8})
    assert out == {"price_ugx": 40000, "analytics_enabled": True, "features": ["A", "B"], "max_photos": 8}


@pytest.mark.parametrize("body", [{"max_photos": 0}, {"bogus": 1}, {"analytics_enabled": "yes"}, {"name": ""}, {"price_ugx": True}])
def test_clean_plan_patch_rejects(body):
    with pytest.raises(HTTPException):
        clean_plan_patch(body)
