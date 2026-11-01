import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from marketplace.routes import reports


@pytest.fixture
def admin():
    client = MagicMock()
    with patch.object(reports, "get_supabase_admin", return_value=client):
        yield client


def test_other_without_details_is_rejected():
    # Arrange / Act
    with pytest.raises(HTTPException) as exc:
        reports.clean_report_details("Other", "  ")
    # Assert
    assert exc.value.status_code == 400


def test_other_with_details_is_trimmed():
    # Act
    result = reports.clean_report_details("Other", "  fake photos  ")
    # Assert
    assert result == "fake photos"


def test_overlong_details_rejected():
    with pytest.raises(HTTPException) as exc:
        reports.clean_report_details("Spam", "x" * 1001)
    assert exc.value.status_code == 400


def test_duplicate_product_report_returns_409(admin):
    # Arrange
    admin.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "r1"}]
    # Act
    with pytest.raises(HTTPException) as exc:
        asyncio.run(reports.report_product("p1", "Spam or duplicate", None, current_user_id="u1"))
    # Assert
    assert exc.value.status_code == 409
    assert "already reported" in exc.value.detail


def test_seller_other_without_details_rejected_before_db(admin):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(reports.report_seller("s1", "Other", None, None, current_user_id="u1"))
    assert exc.value.status_code == 400
    admin.table.assert_not_called()
