from app.services.merchant_intel import (
    cohort_buckets,
    dynamic_promos,
    forecast_footfall,
    get_overview,
)


class TestMerchantIntelLogic:
    """Test merchant intelligence v1 logic implementation."""
    
    def test_cohort_buckets(self):
        """Test cohort analysis with mock events."""
        events = [
            {"hour": 22, "day_of_week": 1, "amount": 45.20},
            {"hour": 8, "day_of_week": 2, "amount": 67.80},
            {"hour": 14, "day_of_week": 6, "amount": 89.50},
            {"hour": 19, "day_of_week": 3, "amount": 34.20},
            {"hour": 23, "day_of_week": 5, "amount": 56.70}
        ]
        
        result = cohort_buckets(events)
        
        assert "night_owls" in result
        assert "green_commuters" in result
        assert "weekend_fast" in result
        assert result["night_owls"]["count"] >= 0
        assert result["green_commuters"]["count"] >= 0
        assert result["weekend_fast"]["count"] >= 0
    
    def test_forecast_footfall(self):
        """Test footfall forecasting logic."""
        result_24h = forecast_footfall("merchant_123", 24)
        result_168h = forecast_footfall("merchant_123", 168)
        
        assert "expected_visits" in result_24h
        assert "confidence" in result_24h
        assert "peak_hours" in result_24h
        assert result_24h["expected_visits"] > 0
        assert 0 <= result_24h["confidence"] <= 1
        
        assert result_168h["expected_visits"] > result_24h["expected_visits"]
    
    def test_dynamic_promos(self):
        """Test dynamic promotion generation."""
        # Low grid load
        promos_low = dynamic_promos("merchant_123", 65.0)
        assert len(promos_low) > 0
        assert any("Green Hour" in promo["name"] for promo in promos_low)
        
        # High grid load
        promos_high = dynamic_promos("merchant_123", 95.0)
        assert len(promos_high) > 0
        assert any("Peak Avoidance" in promo["name"] for promo in promos_high)
        
        # Coffee merchant specific
        promos_coffee = dynamic_promos("coffee_shop_456", 75.0)
        assert any("Coffee" in promo["name"] for promo in promos_coffee)
    
    def test_get_overview_deterministic(self):
        """Test that overview results are deterministic for same inputs."""
        result1 = get_overview("merchant_123", 75.0)
        result2 = get_overview("merchant_123", 75.0)
        
        assert result1["merchant_id"] == result2["merchant_id"]
        assert len(result1["cohorts"]) == len(result2["cohorts"])
        assert len(result1["forecasts"]) == len(result2["forecasts"])
        assert len(result1["promos"]) == len(result2["promos"])
    
    def test_get_overview_structure(self):
        """Test overview response structure."""
        result = get_overview("merchant_456", 80.0)
        
        assert "merchant_id" in result
        assert "cohorts" in result
        assert "forecasts" in result
        assert "promos" in result
        assert "last_updated" in result
        
        # Check cohorts structure
        assert len(result["cohorts"]) == 3
        for cohort in result["cohorts"]:
            assert "name" in cohort
            assert "size" in cohort
            assert "avg_monthly_spend" in cohort
            assert "retention_rate" in cohort
        
        # Check forecasts structure
        assert "next_24h" in result["forecasts"]
        assert "next_7d" in result["forecasts"]
        
        # Check promos structure
        assert "active" in result["promos"]
        assert "recommended" in result["promos"]
    
    def test_grid_load_impact(self):
        """Test that grid load affects promotion recommendations."""
        result_low = get_overview("merchant_123", 60.0)
        result_high = get_overview("merchant_123", 90.0)
        
        # Should have different recommended promotions
        low_promos = result_low["promos"]["recommended"]
        high_promos = result_high["promos"]["recommended"]
        
        # At least one should have different content
        assert len(low_promos) != len(high_promos) or any(
            promo not in high_promos for promo in low_promos
        )
