from tenants.routes.verifications import _clean_fields, missing_required_docs


def _d(*types):
    return [{"type": t, "url": f"https://x/{t}"} for t in types]


def test_identity_needs_id_and_selfie():
    assert missing_required_docs(2, _d("passport", "selfie")) == []
    assert missing_required_docs(2, _d("national_id_front", "national_id_back", "selfie")) == []
    assert "the back of the national ID" in missing_required_docs(2, _d("national_id_front", "selfie"))
    assert any("selfie" in m for m in missing_required_docs(2, _d("passport")))


def test_business_and_professional():
    assert missing_required_docs(3, _d("tin")) != []
    assert missing_required_docs(3, _d("business_reg")) == []
    assert missing_required_docs(4, _d("professional_license")) == []


def test_clean_fields_drops_blank():
    assert _clean_fields({"tin": " 100 ", "x": "", "": "y"}) == {"tin": "100"}
