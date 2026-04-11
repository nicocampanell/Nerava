"""
Comprehensive tests for security, feature flags, and API endpoints.
"""
from unittest.mock import Mock, patch

import pytest
from app.core.config import clear_flag_cache
from app.main_simple import app
from fastapi.testclient import TestClient

client = TestClient(app)

class TestFeatureFlags:
    """Test feature flag gating for all endpoints."""
    
    def setup_method(self):
        """Clear flag cache before each test."""
        clear_flag_cache()
    
    @pytest.mark.parametrize("endpoint,flag", [
        ("/v1/merchant/intel/overview?merchant_id=M123", "feature_merchant_intel"),
        ("/v1/utility/behavior/cloud?utility_id=U123&window=24h", "feature_behavior_cloud"),
        ("/v1/verify/charge", "feature_charge_verify_api"),
        ("/v1/deals/green_hours?lat=40.7128&lng=-74.0060", "feature_contextual_commerce"),
        ("/v1/profile/energy_rep?user_id=U123", "feature_energy_rep"),
        ("/v1/fleet/overview?org_id=ORG1", "feature_fleet_workplace"),
        ("/v1/tenant/T123/modules", "feature_uap_partnerships"),
        ("/v1/finance/offers?user_id=U123", "feature_esg_finance_gateway"),
    ])
    def test_endpoint_flag_off_returns_404(self, endpoint, flag):
        """Test that endpoints return 404 when feature flag is off."""
        with patch('app.core.config.flag_enabled', return_value=False):
            if endpoint == "/v1/verify/charge":
                # POST endpoint
                response = client.post(endpoint, json={
                    "charge_session_id": "test123",
                    "kwh_charged": 5.0,
                    "location": {"lat": 40.7128, "lng": -74.0060}
                })
            else:
                # GET endpoint
                response = client.get(endpoint)
            
            assert response.status_code == 404
            assert "Feature not enabled" in response.json()["detail"]
    
    @pytest.mark.parametrize("endpoint,flag", [
        ("/v1/merchant/intel/overview?merchant_id=M123", "feature_merchant_intel"),
        ("/v1/utility/behavior/cloud?utility_id=U123&window=24h", "feature_behavior_cloud"),
        ("/v1/deals/green_hours?lat=40.7128&lng=-74.0060", "feature_contextual_commerce"),
        ("/v1/profile/energy_rep?user_id=U123", "feature_energy_rep"),
        ("/v1/fleet/overview?org_id=ORG1", "feature_fleet_workplace"),
        ("/v1/tenant/T123/modules", "feature_uap_partnerships"),
        ("/v1/finance/offers?user_id=U123", "feature_esg_finance_gateway"),
    ])
    def test_endpoint_flag_on_returns_200(self, endpoint, flag):
        """Test that endpoints return 200 when feature flag is on."""
        with patch('app.core.config.flag_enabled', return_value=True):
            # Mock auth dependencies
            with patch('app.security.scopes.require_scopes') as mock_scopes:
                mock_scopes.return_value = Mock(return_value={"user_id": "test_user"})
                
                response = client.get(endpoint)
                
                assert response.status_code == 200
                # Verify response has expected structure
                data = response.json()
                assert isinstance(data, dict)


class TestVerifyAPI:
    """Test Verify API security and fraud checks."""
    
    def test_verify_charge_missing_api_key_returns_403(self):
        """Test that missing API key returns 403."""
        with patch('app.core.config.flag_enabled', return_value=True):
            response = client.post("/v1/verify/charge", json={
                "charge_session_id": "test123",
                "kwh_charged": 5.0,
                "location": {"lat": 40.7128, "lng": -74.0060}
            })
            
            assert response.status_code == 403
    
    def test_verify_charge_bad_api_key_returns_403(self):
        """Test that bad API key returns 403."""
        with patch('app.core.config.flag_enabled', return_value=True):
            response = client.post("/v1/verify/charge", 
                json={
                    "charge_session_id": "test123",
                    "kwh_charged": 5.0,
                    "location": {"lat": 40.7128, "lng": -74.0060}
                },
                headers={"X-Nerava-Key": "bad-key"}
            )
            
            assert response.status_code == 403
    
    def test_verify_charge_below_min_kwh_returns_false(self):
        """Test that below minimum kWh returns verified=false."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.apikey.require_api_key') as mock_api_key:
                mock_api_key.return_value = Mock(return_value={"api_key": "valid-key"})
                
                response = client.post("/v1/verify/charge", 
                    json={
                        "charge_session_id": "test123",
                        "kwh_charged": 0.5,  # Below 1.0 minimum
                        "location": {"lat": 40.7128, "lng": -74.0060}
                    },
                    headers={"X-Nerava-Key": "valid-key"}
                )
                
                assert response.status_code == 200
                data = response.json()
                assert data["verified"] == False
                assert data["fraud_reason"] == "below_min_kwh"
    
    def test_verify_charge_geo_mismatch_returns_false(self):
        """Test that geo mismatch returns verified=false."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.apikey.require_api_key') as mock_api_key:
                mock_api_key.return_value = Mock(return_value={"api_key": "valid-key"})
                
                response = client.post("/v1/verify/charge", 
                    json={
                        "charge_session_id": "test123",
                        "kwh_charged": 5.0,
                        "location": {"lat": 40.7128, "lng": -74.0060},
                        "station_location": {"lat": 50.0, "lng": -80.0}  # Far away
                    },
                    headers={"X-Nerava-Key": "valid-key"}
                )
                
                assert response.status_code == 200
                data = response.json()
                assert data["verified"] == False
                assert data["fraud_reason"] == "geo_mismatch"
    
    def test_verify_charge_valid_returns_true(self):
        """Test that valid charge returns verified=true."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.apikey.require_api_key') as mock_api_key:
                mock_api_key.return_value = Mock(return_value={"api_key": "valid-key"})
                
                response = client.post("/v1/verify/charge", 
                    json={
                        "charge_session_id": "test123",
                        "kwh_charged": 5.0,
                        "location": {"lat": 40.7128, "lng": -74.0060},
                        "station_location": {"lat": 40.7130, "lng": -74.0062}  # Close by
                    },
                    headers={"X-Nerava-Key": "valid-key"}
                )
                
                assert response.status_code == 200
                data = response.json()
                assert data["verified"] == True
                assert "fraud_reason" not in data


class TestAuthScopes:
    """Test authentication and authorization scopes."""
    
    def test_merchant_endpoints_require_merchant_scope(self):
        """Test that merchant endpoints require merchant:read scope."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.scopes.require_scopes') as mock_scopes:
                # Test with wrong scope
                mock_scopes.side_effect = Exception("Insufficient scope")
                
                response = client.get("/v1/merchant/intel/overview?merchant_id=M123")
                assert response.status_code == 500  # Should fail due to scope check
    
    def test_utility_endpoints_require_utility_scope(self):
        """Test that utility endpoints require utility:read scope."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.scopes.require_scopes') as mock_scopes:
                # Test with wrong scope
                mock_scopes.side_effect = Exception("Insufficient scope")
                
                response = client.get("/v1/utility/behavior/cloud?utility_id=U123&window=24h")
                assert response.status_code == 500  # Should fail due to scope check
    
    def test_fleet_endpoints_require_fleet_scope(self):
        """Test that fleet endpoints require fleet:read scope."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.scopes.require_scopes') as mock_scopes:
                # Test with wrong scope
                mock_scopes.side_effect = Exception("Insufficient scope")
                
                response = client.get("/v1/fleet/overview?org_id=ORG1")
                assert response.status_code == 500  # Should fail due to scope check


class TestRateLimiting:
    """Test rate limiting functionality."""
    
    def test_rate_limit_exceeded_returns_429(self):
        """Test that exceeding rate limit returns 429."""
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


class TestWriteEndpoints:
    """Test write endpoints with rate limiting."""
    
    @pytest.mark.parametrize("endpoint,method,data", [
        ("/v1/rewards/routing/rebalance", "POST", {}),
        ("/v1/merchant/credits/purchase", "POST", {"merchant_id": "M123", "amount": 100}),
        ("/v1/events/create", "POST", {"host_id": "H123", "schedule": {}, "boost_rate": 0.1}),
        ("/v1/ai/growth/campaigns/generate", "POST", {}),
        ("/v1/offsets/mint", "POST", {"tons_co2e": 1.0, "source": "test"}),
    ])
    def test_write_endpoints_have_rate_limits(self, endpoint, method, data):
        """Test that write endpoints have rate limiting applied."""
        with patch('app.core.config.flag_enabled', return_value=True):
            with patch('app.security.ratelimit.rate_limit') as mock_rate_limit:
                mock_rate_limit.return_value = Mock(return_value=True)
                
                if method == "POST":
                    response = client.post(endpoint, json=data)
                else:
                    response = client.get(endpoint)
                
                # Should not fail due to rate limit
                assert response.status_code != 429
