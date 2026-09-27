from listingModeration.pipeline import apply_review_policy
from listingModeration.schemas import ModerationStatus


def test_manual_review_downgrades_approval_only():
    status, reason = apply_review_policy(ModerationStatus.APPROVED, None, manual=True)
    assert status == ModerationStatus.NEEDS_REVIEW
    assert reason == "manual review required"
    kept, _reason = apply_review_policy(ModerationStatus.REJECTED, "banned", manual=True)
    assert kept == ModerationStatus.REJECTED
    auto, _reason = apply_review_policy(ModerationStatus.APPROVED, None, manual=False)
    assert auto == ModerationStatus.APPROVED
