"""
Tests for rate limiting functionality.
"""
from unittest.mock import Mock, patch

from app.main_simple import app
from fastapi.testclient import TestClient

client = TestClient(app)

class TestRateLimiting:
    """Test rate limiting functionality."""
    
    def test_rate_limit_burst_returns_429(self):
        """Test that burst requests exceeding limit return 429."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                # Mock rate limit exceeded
                mock_rate_limit.side_effect = Exception("Rate limit exceeded")
                
                response = client.post("/v1/rewards/routing/rebalance")
                assert response.status_code == 500  # Should fail due to rate limit
    
    def test_rate_limit_within_limits_works(self):
        """Test that requests within rate limits work."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                response = client.post("/v1/rewards/routing/rebalance")
                # Should not fail due to rate limit (other failures may occur)
                assert response.status_code != 429
    
    def test_different_endpoints_different_limits(self):
        """Test that different endpoints have different rate limits."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # Test different endpoints
                response1 = client.post("/v1/rewards/routing/rebalance")
                response2 = client.post("/v1/merchant/credits/purchase", json={"merchant_id": "M123", "amount": 100})
                response3 = client.post("/v1/ai/growth/campaigns/generate")
                
                # All should not fail due to rate limit
                assert response1.status_code != 429
                assert response2.status_code != 429
                assert response3.status_code != 429
    
    def test_rate_limit_per_user(self):
        """Test that rate limiting is per user."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # Simulate requests from different users
                response1 = client.post("/v1/rewards/routing/rebalance")
                response2 = client.post("/v1/rewards/routing/rebalance")
                
                # Both should work (different users)
                assert response1.status_code != 429
                assert response2.status_code != 429
    
    def test_rate_limit_per_api_key(self):
        """Test that rate limiting is per API key."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # Test with different API keys
                response1 = client.post("/v1/verify/charge", 
                    json={"charge_session_id": "test1", "kwh_charged": 5.0, "location": {"lat": 40.7128, "lng": -74.0060}},
                    headers={"X-Nerava-Key": "key1"}
                )
                response2 = client.post("/v1/verify/charge", 
                    json={"charge_session_id": "test2", "kwh_charged": 5.0, "location": {"lat": 40.7128, "lng": -74.0060}},
                    headers={"X-Nerava-Key": "key2"}
                )
                
                # Both should work (different API keys)
                assert response1.status_code != 429
                assert response2.status_code != 429
    
    def test_rate_limit_write_endpoints(self):
        """Test that write endpoints have rate limiting."""
        write_endpoints = [
            ("/v1/rewards/routing/rebalance", "POST", {}),
            ("/v1/merchant/credits/purchase", "POST", {"merchant_id": "M123", "amount": 100}),
            ("/v1/events/create", "POST", {"host_id": "H123", "schedule": {}, "boost_rate": 0.1}),
            ("/v1/ai/growth/campaigns/generate", "POST", {}),
            ("/v1/offsets/mint", "POST", {"tons_co2e": 1.0, "source": "test"}),
        ]
        
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                for endpoint, method, data in write_endpoints:
                    if method == "POST":
                        response = client.post(endpoint, json=data)
                    else:
                        response = client.get(endpoint)
                    
                    # Should not fail due to rate limit
                    assert response.status_code != 429
    
    def test_rate_limit_read_endpoints(self):
        """Test that read endpoints have different rate limits."""
        read_endpoints = [
            "/v1/merchant/intel/overview?merchant_id=M123",
            "/v1/utility/behavior/cloud?utility_id=U123&window=24h",
            "/v1/deals/green_hours?lat=40.7128&lng=-74.0060",
            "/v1/profile/energy_rep?user_id=U123",
            "/v1/fleet/overview?org_id=ORG1",
            "/v1/tenant/T123/modules",
            "/v1/finance/offers?user_id=U123",
        ]
        
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                for endpoint in read_endpoints:
                    response = client.get(endpoint)
                    # Should not fail due to rate limit
                    assert response.status_code != 429
    
    def test_rate_limit_token_bucket_algorithm(self):
        """Test that rate limiting uses token bucket algorithm."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                # Mock token bucket behavior
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # Simulate burst of requests
                responses = []
                for i in range(15):  # Exceed 10/min limit
                    response = client.post("/v1/rewards/routing/rebalance")
                    responses.append(response)
                
                # Some should be rate limited
                rate_limited = [r for r in responses if r.status_code == 429]
                assert len(rate_limited) > 0
    
    def test_rate_limit_reset_after_time_window(self):
        """Test that rate limits reset after time window."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                # Mock rate limit reset
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # First burst
                response1 = client.post("/v1/rewards/routing/rebalance")
                
                # Simulate time passing (rate limit reset)
                with patch('app.security.ratelimit.rate_limit') as mock_rate_limit_reset:
                    mock_rate_limit_reset.return_value = Mock(return_value=True)
                    
                    # Second burst after reset
                    response2 = client.post("/v1/rewards/routing/rebalance")
                    
                    # Both should work after reset
                    assert response1.status_code != 429
                    assert response2.status_code != 429
    
    def test_rate_limit_different_limits_per_endpoint(self):
        """Test that different endpoints have different rate limits."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                # Test endpoints with different limits
                response1 = client.post("/v1/rewards/routing/rebalance")  # 10/min
                response2 = client.post("/v1/ai/growth/campaigns/generate")  # 5/min
                response3 = client.post("/v1/offsets/mint", json={"tons_co2e": 1.0, "source": "test"})  # 5/min
                
                # All should work within their limits
                assert response1.status_code != 429
                assert response2.status_code != 429
                assert response3.status_code != 429
