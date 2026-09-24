"""Phase 3 identity unlock helpers (no network)."""

from payments.plan_service import (
    IDENTITY_LISTING_UNLOCK_THRESHOLD,
    IDENTITY_REQUIRED_CODE,
)


def test_identity_listing_threshold():
    assert IDENTITY_LISTING_UNLOCK_THRESHOLD == 5
    assert IDENTITY_REQUIRED_CODE == "identity_verification_required"
