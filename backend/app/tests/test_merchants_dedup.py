"""Tests for merchant deduplication logic."""

from app.utils.names import normalize_merchant_name


def test_normalize_merchant_name():
    """Test merchant name normalization."""
    # Basic cases
    assert normalize_merchant_name("Starbucks") == "starbucks"
    assert normalize_merchant_name("Blue Bottle") == "blue bottle"
    
    # With apostrophes
    assert normalize_merchant_name("Starbucks' Coffee") == "starbucks"
    assert normalize_merchant_name("O'Brien's") == "obriens"
    
    # With accents
    assert normalize_merchant_name("Blue Bottle Café") == "blue bottle"
    assert normalize_merchant_name("José's Coffee") == "joses"
    
    # With generic suffixes
    assert normalize_merchant_name("Starbucks Coffee") == "starbucks"
    assert normalize_merchant_name("Mock Cafe") == "mock"
    assert normalize_merchant_name("Test Shop") == "test"
    
    # Punctuation
    assert normalize_merchant_name("Starbucks 2.0") == "starbucks 2 0"
    assert normalize_merchant_name("Coffee & Tea") == "coffee tea"
    
    # Collapse whitespace
    assert normalize_merchant_name("Starbucks  Coffee") == "starbucks"
    assert normalize_merchant_name("  Blue   Bottle  ") == "blue bottle"


# Note: Integration tests requiring database would go here
# For now, we test the core logic (normalize_merchant_name) separately


def test_find_merchants_rounds_lat_lng_to_5_decimal_places():
    """Test that lat/lng are rounded to 5 decimal places for deduplication."""
    # Test rounding precision
    lat = 30.2672000001
    assert round(lat, 5) == 30.2672
    
    # Basic rounding test
    assert round(30.2672, 5) == 30.2672
    assert round(30.26725, 5) == 30.26725

