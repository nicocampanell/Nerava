"""
Unit tests for While You Charge service - merchant ranking and selection
"""

from app.services.geo import haversine_m as haversine_distance
from app.services.while_you_charge import (
    build_recommended_merchants,
    build_recommended_merchants_from_chargers,
)


class TestHaversineDistance:
    """Test distance calculation"""

    def test_same_location_returns_zero(self):
        """Same location should return zero distance"""
        distance = haversine_distance(30.4, -97.7, 30.4, -97.7)
        assert distance == 0.0

    def test_distance_calculation(self):
        """Should calculate approximate distance between two points"""
        # Austin to Dallas is roughly 278 km
        # Domain area (30.4021, -97.7266) to downtown Dallas (32.7767, -96.7970)
        distance = haversine_distance(30.4021, -97.7266, 32.7767, -96.7970)
        # Should be approximately 278km = 278000m (with some tolerance)
        assert 270000 < distance < 290000

    def test_close_locations_small_distance(self):
        """Close locations should return small distance"""
        # Two points about 1km apart
        distance = haversine_distance(30.4, -97.7, 30.409, -97.7)
        assert 900 < distance < 1100  # Roughly 1km


class TestBuildRecommendedMerchants:
    """Test merchant recommendation building"""

    def test_empty_merchant_list_returns_empty(self):
        """Empty merchant list should return empty list (no exceptions)"""
        result = build_recommended_merchants([], limit=10)
        assert result == []

    def test_respects_limit(self):
        """Should respect the limit parameter"""
        merchants = [
            {"id": f"m{i}", "name": f"Merchant {i}", "lat": 30.4 + i * 0.001, "lng": -97.7}
            for i in range(20)
        ]

        result = build_recommended_merchants(merchants, limit=5)
        assert len(result) == 5

    def test_returns_merchant_dicts(self):
        """Should return list of merchant dicts with expected fields"""
        merchants = [
            {"id": "m1", "name": "Test Merchant", "lat": 30.4, "lng": -97.7, "category": "coffee"}
        ]

        result = build_recommended_merchants(merchants, limit=10)
        assert len(result) == 1
        assert "id" in result[0]
        assert "name" in result[0]


class TestBuildRecommendedMerchantsFromChargers:
    """Test merchant recommendations from charger data"""

    def test_empty_chargers_returns_empty(self):
        """Empty charger list should return empty list (no exceptions)"""
        result = build_recommended_merchants_from_chargers([], limit=10)
        assert result == []

    def test_respects_limit(self):
        """Should respect the limit parameter"""
        chargers = [{"id": f"ch{i}", "lat": 30.4 + i * 0.001, "lng": -97.7} for i in range(15)]

        result = build_recommended_merchants_from_chargers(chargers, limit=8)
        assert len(result) <= 8

    def test_chargers_with_no_merchants_returns_empty(self):
        """Chargers with no merchants array or empty merchants should return empty list (no crash)"""
        chargers = [
            {"id": "ch1", "lat": 30.4, "lng": -97.7},  # No merchants key
            {"id": "ch2", "lat": 30.5, "lng": -97.8, "merchants": []},  # Empty merchants
            {"id": "ch3", "lat": 30.6, "lng": -97.9, "merchants": None},  # None merchants
        ]

        result = build_recommended_merchants_from_chargers(chargers, limit=10)
        # Should return empty list without crashing
        assert result == []
        assert isinstance(result, list)
