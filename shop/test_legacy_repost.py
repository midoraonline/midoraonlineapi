import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from shop.routes import products


@pytest.fixture(autouse=True)
def _admin():
    with patch.object(products, "get_supabase_admin", return_value=MagicMock()):
        yield


def test_legacy_repost_uses_plan_limits():
    # Arrange
    with patch.object(products.listing_actions, "repost_listing") as repost, \
         patch.object(products.shop_service, "get_product", return_value={"id": "p1"}):
        # Act
        result = asyncio.run(products.repost_product("p1", MagicMock(), user_id="u1"))
    # Assert
    repost.assert_called_once()
    assert repost.call_args.args[1:] == ("p1", "u1")
    assert result == {"id": "p1"}


def test_legacy_repost_surfaces_plan_limit_error():
    # Arrange
    limited = HTTPException(status_code=429, detail={"detail": "used all", "code": "repost_limited"})
    with patch.object(products.listing_actions, "repost_listing", side_effect=limited):
        # Act
        with pytest.raises(HTTPException) as exc:
            asyncio.run(products.repost_product("p1", MagicMock(), user_id="u1"))
    # Assert
    assert exc.value.status_code == 429
