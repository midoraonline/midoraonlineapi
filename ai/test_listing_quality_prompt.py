from ai.service import _LISTING_QUALITY_PROMPT, format_listing_details


def test_details_listed_with_empty_marker():
    text = format_listing_details({"Price": "UGX 50,000", "Brand": "", "": "skip"})
    assert "- Price: UGX 50,000" in text
    assert "- Brand: (empty)" in text
    assert "skip" not in text


def test_no_details():
    assert format_listing_details(None) == "- none"


def test_prompt_forbids_repeating_structured_fields():
    assert "NEVER suggest adding to the description" in _LISTING_QUALITY_PROMPT
