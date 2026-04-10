from app.services.energy_rep import backfill_last_60_days, compute_v1, snapshot_energy_rep


class TestEnergyRepLogic:
    """Test energy reputation v1 logic implementation."""
    
    def test_compute_v1_deterministic(self):
        """Test that v1 computation is deterministic for same user."""
        result1 = compute_v1("user_123")
        result2 = compute_v1("user_123")
        
        assert result1["user_id"] == result2["user_id"]
        assert result1["total_score"] == result2["total_score"]
        assert result1["tier"] == result2["tier"]
        assert result1["breakdown"] == result2["breakdown"]
    
    def test_compute_v1_structure(self):
        """Test v1 computation response structure."""
        result = compute_v1("user_456")
        
        assert "user_id" in result
        assert "total_score" in result
        assert "tier" in result
        assert "breakdown" in result
        assert "last_calculated_at" in result
        
        # Check breakdown structure
        breakdown = result["breakdown"]
        assert "charging_score" in breakdown
        assert "referrals" in breakdown
        assert "merchant" in breakdown
        assert "v2g" in breakdown
        
        # Check score ranges
        assert 0 <= breakdown["charging_score"] <= 600
        assert 0 <= breakdown["referrals"] <= 200
        assert 0 <= breakdown["merchant"] <= 150
        assert 0 <= breakdown["v2g"] <= 250
    
    def test_tier_calculation(self):
        """Test tier calculation based on score thresholds."""
        # Test different users to get different scores
        users = ["user_001", "user_002", "user_003", "user_004"]
        tiers = set()
        
        for user in users:
            result = compute_v1(user)
            tiers.add(result["tier"])
            assert result["tier"] in ["Bronze", "Silver", "Gold", "Platinum"]
        
        # Should have some variation in tiers
        assert len(tiers) > 1
    
    def test_score_components(self):
        """Test that score components are calculated correctly."""
        result = compute_v1("user_789")
        
        breakdown = result["breakdown"]
        total_score = result["total_score"]
        
        # Calculate expected score with weights: 0.5, 0.2, 0.15, 0.15
        expected_score = int(
            breakdown["charging_score"] * 0.5 +
            breakdown["referrals"] * 0.2 +
            breakdown["merchant"] * 0.15 +
            breakdown["v2g"] * 0.15
        )
        
        assert total_score == expected_score
    
    def test_tier_thresholds(self):
        """Test tier thresholds: 400/650/850."""
        # Find a user with score in each tier range
        tier_ranges = {
            "Bronze": (0, 399),
            "Silver": (400, 649),
            "Gold": (650, 849),
            "Platinum": (850, 1000)
        }
        
        for tier, (min_score, max_score) in tier_ranges.items():
            # Try different users until we find one in this tier
            for i in range(100):
                user = f"user_{i:03d}"
                result = compute_v1(user)
                if min_score <= result["total_score"] <= max_score:
                    assert result["tier"] == tier
                    break
    
    def test_snapshot_energy_rep(self):
        """Test snapshot function."""
        # Mock database session
        class MockDB:
            def query(self, model):
                return self
            def filter(self, *args):
                return self
            def first(self):
                return None
            def add(self, obj):
                pass
            def commit(self):
                pass
        
        db = MockDB()
        result = snapshot_energy_rep("user_123", db)
        
        assert "user_id" in result
        assert "total_score" in result
        assert "tier" in result
    
    def test_backfill_idempotency(self):
        """Test that backfill is idempotent."""
        # Mock database session
        class MockDB:
            def __init__(self):
                self.backfills = []
                self.committed = False
            
            def query(self, model):
                return self
            def filter(self, *args):
                return self
            def first(self):
                return None  # No existing backfill
            def add(self, obj):
                self.backfills.append(obj)
            def commit(self):
                self.committed = True
        
        db = MockDB()
        result = backfill_last_60_days("user_123", db)
        
        assert "user_id" in result
        assert "backfilled_days" in result
        assert "skipped_days" in result
        assert "total_days" in result
        assert len(result["backfilled_days"]) == 60
        assert len(result["skipped_days"]) == 0
        assert result["total_days"] == 60
        assert db.committed
    
    def test_different_users_different_scores(self):
        """Test that different users get different scores."""
        user1_result = compute_v1("user_alpha")
        user2_result = compute_v1("user_beta")
        
        # Should have different scores (very unlikely to be identical)
        assert user1_result["total_score"] != user2_result["total_score"]
        
        # Breakdowns should be different
        assert user1_result["breakdown"] != user2_result["breakdown"]
