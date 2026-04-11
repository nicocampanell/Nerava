from app.services.behavior_cloud import elasticity_estimate, get_cloud, participation, segment_users


class TestBehaviorCloudLogic:
    """Test behavior cloud v1 logic implementation."""
    
    def test_segment_users(self):
        """Test user segmentation logic."""
        segments_24h = segment_users("24h")
        segments_7d = segment_users("7d")
        
        assert len(segments_24h) == 4
        assert len(segments_7d) == 4
        
        # Check segment structure
        for segment in segments_24h:
            assert "name" in segment
            assert "size" in segment
            assert "avg_shift_kwh" in segment
            assert "elasticity" in segment
            assert "characteristics" in segment
            assert segment["size"] > 0
            assert segment["avg_shift_kwh"] > 0
        
        # 7d should have larger segments than 24h
        total_24h = sum(s["size"] for s in segments_24h)
        total_7d = sum(s["size"] for s in segments_7d)
        assert total_7d > total_24h
    
    def test_participation(self):
        """Test participation rate calculation."""
        part_24h = participation("24h")
        part_7d = participation("7d")
        
        assert "total_users" in part_24h
        assert "active_participants" in part_24h
        assert "participation_rate" in part_24h
        assert "avg_shift_kwh" in part_24h
        assert "by_hour" in part_24h
        
        assert 0 <= part_24h["participation_rate"] <= 1
        assert part_24h["active_participants"] <= part_24h["total_users"]
        assert len(part_24h["by_hour"]) == 24
        
        # 7d should have more users
        assert part_7d["total_users"] > part_24h["total_users"]
    
    def test_elasticity_estimate(self):
        """Test elasticity estimation."""
        elasticity = elasticity_estimate()
        
        assert "price_points" in elasticity
        assert "price_elasticity" in elasticity
        assert "time_elasticity" in elasticity
        assert "incentive_elasticity" in elasticity
        assert "confidence" in elasticity
        
        assert elasticity["price_elasticity"] < 0  # Negative: higher prices reduce demand
        assert elasticity["time_elasticity"] < 0   # Negative: peak hours reduce demand
        assert elasticity["incentive_elasticity"] > 0  # Positive: incentives increase participation
        assert 0 <= elasticity["confidence"] <= 1
        
        # Check price points structure
        for point in elasticity["price_points"]:
            assert "price_cents_kwh" in point
            assert "expected_lift" in point
            assert point["price_cents_kwh"] > 0
            assert point["expected_lift"] > 0
    
    def test_get_cloud_deterministic(self):
        """Test that cloud results are deterministic for same inputs."""
        result1 = get_cloud("utility_123", "24h")
        result2 = get_cloud("utility_123", "24h")
        
        assert result1["utility_id"] == result2["utility_id"]
        assert result1["window"] == result2["window"]
        assert len(result1["segments"]) == len(result2["segments"])
        assert result1["participation"]["total_users"] == result2["participation"]["total_users"]
    
    def test_get_cloud_structure(self):
        """Test cloud response structure."""
        result = get_cloud("utility_456", "7d")
        
        assert "utility_id" in result
        assert "window" in result
        assert "segments" in result
        assert "participation" in result
        assert "elasticity" in result
        assert "generated_at" in result
        
        # Check participation structure
        part = result["participation"]
        assert "total_users" in part
        assert "active_participants" in part
        assert "participation_rate" in part
        assert "avg_shift_kwh" in part
        
        # Check elasticity structure
        elast = result["elasticity"]
        assert "price_elasticity" in elast
        assert "time_elasticity" in elast
        assert "incentive_elasticity" in elast
        assert "confidence" in elast
    
    def test_window_impact(self):
        """Test that different windows produce different results."""
        result_24h = get_cloud("utility_123", "24h")
        result_7d = get_cloud("utility_123", "7d")
        
        assert result_24h["window"] == "24h"
        assert result_7d["window"] == "7d"
        
        # 7d should have more users than 24h
        assert result_7d["participation"]["total_users"] > result_24h["participation"]["total_users"]
        
        # Segments should be different sizes
        total_24h = sum(s["size"] for s in result_24h["segments"])
        total_7d = sum(s["size"] for s in result_7d["segments"])
        assert total_7d > total_24h
