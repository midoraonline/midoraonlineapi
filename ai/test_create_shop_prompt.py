from ai.service import CREATE_SHOP_SYSTEM, _clean_list, format_create_shop_context


def test_context_marks_verified_contacts_and_lists_listings():
    text = format_create_shop_context(
        {
            "email": "a@b.ug",
            "email_verified": True,
            "phone": "+256700000000",
            "phone_verified": False,
            "listings": [{"item_type": "service", "title": "Laundry", "price_ugx": 15000, "location_name": "Ntinda"}],
        }
    )
    assert "a@b.ug (verified)" in text
    assert "+256700000000 (not verified)" in text
    assert "service | Laundry | UGX 15,000 | Ntinda" in text


def test_empty_context():
    assert format_create_shop_context(None) == ""
    assert format_create_shop_context({}) == ""


def test_clean_list_limits():
    assert _clean_list(["a", "", " b ", "c"], 2) == ["a", "b"]
    assert _clean_list("nope", 3) == []


def test_prompt_is_uganda_specific():
    assert "UGX" in CREATE_SHOP_SYSTEM and "opportunities" in CREATE_SHOP_SYSTEM
