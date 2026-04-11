"""
Test suite for all 20 feature flag endpoints.
Tests both flag OFF (404) and flag ON (200) scenarios.
"""
from app.core.config import clear_flag_cache
from app.main_simple import app
from fastapi.testclient import TestClient

client = TestClient(app)

# Test data fixtures
TEST_MERCHANT_ID = "merchant_123"
TEST_UTILITY_ID = "utility_456"
TEST_USER_ID = "user_789"
TEST_CITY_SLUG = "austin"
TEST_TENANT_ID = "tenant_abc"

class TestFeatureFlags:
    """Test all 20 feature endpoints with flag gating."""
    
    def setup_method(self):
        """Clear flag cache before each test."""
        clear_flag_cache()
    
    def test_merchant_intel_flag_off(self):
        """Test merchant intel endpoint when flag is OFF."""
        response = client.get(f"/v1/merchant/intel/overview?merchant_id={TEST_MERCHANT_ID}")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_merchant_intel_flag_on(self):
        """Test merchant intel endpoint when flag is ON."""
        # Enable flag via environment
        import os
        os.environ["FEATURE_MERCHANT_INTEL"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/merchant/intel/overview?merchant_id={TEST_MERCHANT_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "merchant_id" in data
        assert "cohorts" in data
        assert "forecasts" in data
        assert "promos" in data
    
    def test_behavior_cloud_flag_off(self):
        """Test behavior cloud endpoint when flag is OFF."""
        response = client.get(f"/v1/utility/behavior/cloud?utility_id={TEST_UTILITY_ID}&window=24h")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_behavior_cloud_flag_on(self):
        """Test behavior cloud endpoint when flag is ON."""
        import os
        os.environ["FEATURE_BEHAVIOR_CLOUD"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/utility/behavior/cloud?utility_id={TEST_UTILITY_ID}&window=24h")
        assert response.status_code == 200
        data = response.json()
        assert "utility_id" in data
        assert "segments" in data
        assert "participation" in data
        assert "elasticity" in data
    
    def test_reward_routing_flag_off(self):
        """Test reward routing endpoint when flag is OFF."""
        response = client.post("/v1/rewards/routing/rebalance")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_reward_routing_flag_on(self):
        """Test reward routing endpoint when flag is ON."""
        import os
        os.environ["FEATURE_AUTONOMOUS_REWARD_ROUTING"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/rewards/routing/rebalance")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "run_id" in data
    
    def test_city_marketplace_flag_off(self):
        """Test city marketplace endpoint when flag is OFF."""
        response = client.get(f"/v1/city/impact?city_slug={TEST_CITY_SLUG}")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_city_marketplace_flag_on(self):
        """Test city marketplace endpoint when flag is ON."""
        import os
        os.environ["FEATURE_CITY_MARKETPLACE"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/city/impact?city_slug={TEST_CITY_SLUG}")
        assert response.status_code == 200
        data = response.json()
        assert "city" in data
        assert "mwh_saved" in data
        assert "rewards_paid" in data
        assert "leaderboard" in data
    
    def test_multimodal_flag_off(self):
        """Test multimodal endpoint when flag is OFF."""
        response = client.post("/v1/mobility/register_device", json={
            "user_id": TEST_USER_ID,
            "mode": "scooter"
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_multimodal_flag_on(self):
        """Test multimodal endpoint when flag is ON."""
        import os
        os.environ["FEATURE_MULTIMODAL"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/mobility/register_device", json={
            "user_id": TEST_USER_ID,
            "mode": "scooter"
        })
        assert response.status_code == 200
        data = response.json()
        assert "device_id" in data
        assert "mode" in data
        assert "status" in data
    
    def test_merchant_credits_flag_off(self):
        """Test merchant credits endpoint when flag is OFF."""
        response = client.post("/v1/merchant/credits/purchase", json={
            "merchant_id": TEST_MERCHANT_ID,
            "amount": 100
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_merchant_credits_flag_on(self):
        """Test merchant credits endpoint when flag is ON."""
        import os
        os.environ["FEATURE_MERCHANT_CREDITS"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/merchant/credits/purchase", json={
            "merchant_id": TEST_MERCHANT_ID,
            "amount": 100
        })
        assert response.status_code == 200
        data = response.json()
        assert "merchant_id" in data
        assert "credits_before" in data
        assert "credits_after" in data
        assert "price_cents" in data
    
    def test_verify_api_flag_off(self):
        """Test verify API endpoint when flag is OFF."""
        response = client.post("/v1/verify/charge", json={
            "charge_session_id": "session_123",
            "kwh_charged": 15.5,
            "location": {"lat": 37.7749, "lng": -122.4194},
            "timestamp": "2024-01-15T10:30:00Z"
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_verify_api_flag_on(self):
        """Test verify API endpoint when flag is ON."""
        import os
        os.environ["FEATURE_CHARGE_VERIFY_API"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/verify/charge", 
            json={
                "charge_session_id": "session_123",
                "kwh_charged": 15.5,
                "location": {"lat": 37.7749, "lng": -122.4194},
                "timestamp": "2024-01-15T10:30:00Z"
            },
            headers={"X-Nerava-Key": "nerava-verify-key-2024"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "request_id" in data
        assert "verified" in data
        assert "meta" in data
    
    def test_wallet_interop_flag_off(self):
        """Test wallet interop endpoint when flag is OFF."""
        response = client.get("/v1/wallet/interop/options")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_wallet_interop_flag_on(self):
        """Test wallet interop endpoint when flag is ON."""
        import os
        os.environ["FEATURE_ENERGY_WALLET_EXT"] = "true"
        clear_flag_cache()
        
        response = client.get("/v1/wallet/interop/options")
        assert response.status_code == 200
        data = response.json()
        assert "apple_pay_enabled" in data
        assert "visa_tokenization_enabled" in data
        assert "partners" in data
    
    def test_coop_pools_flag_off(self):
        """Test coop pools endpoint when flag is OFF."""
        response = client.post("/v1/coop/pools", json={
            "utility_id": TEST_UTILITY_ID,
            "merchants": ["merchant_1", "merchant_2"]
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_coop_pools_flag_on(self):
        """Test coop pools endpoint when flag is ON."""
        import os
        os.environ["FEATURE_MERCHANT_UTILITY_COOPS"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/coop/pools", json={
            "utility_id": TEST_UTILITY_ID,
            "merchants": ["merchant_1", "merchant_2"]
        })
        assert response.status_code == 200
        data = response.json()
        assert "pool_id" in data
        assert "utility_id" in data
        assert "merchants" in data
        assert "status" in data
    
    def test_sdk_flag_off(self):
        """Test SDK endpoint when flag is OFF."""
        response = client.get(f"/v1/sdk/config?tenant_id={TEST_TENANT_ID}")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_sdk_flag_on(self):
        """Test SDK endpoint when flag is ON."""
        import os
        os.environ["FEATURE_WHITELABEL_SDK"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/sdk/config?tenant_id={TEST_TENANT_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "tenant_id" in data
        assert "modules" in data
        assert "cdn_urls" in data
        assert "branding" in data
    
    def test_energy_rep_flag_off(self):
        """Test energy rep endpoint when flag is OFF."""
        response = client.get(f"/v1/profile/energy_rep?user_id={TEST_USER_ID}")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_energy_rep_flag_on(self):
        """Test energy rep endpoint when flag is ON."""
        import os
        os.environ["FEATURE_ENERGY_REP"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/profile/energy_rep?user_id={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "user_id" in data
        assert "score" in data
        assert "tier" in data
        assert "components" in data
    
    def test_offsets_flag_off(self):
        """Test offsets endpoint when flag is OFF."""
        response = client.post("/v1/offsets/mint", json={
            "tons_co2e": 2.5,
            "source": "charging_session"
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_offsets_flag_on(self):
        """Test offsets endpoint when flag is ON."""
        import os
        os.environ["FEATURE_CARBON_MICRO_OFFSETS"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/offsets/mint", json={
            "tons_co2e": 2.5,
            "source": "charging_session"
        })
        assert response.status_code == 200
        data = response.json()
        assert "batch_id" in data
        assert "tons_co2e" in data
        assert "credits_url" in data
    
    def test_fleet_flag_off(self):
        """Test fleet endpoint when flag is OFF."""
        response = client.get("/v1/fleet/overview?org_id=ORG123")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_fleet_flag_on(self):
        """Test fleet endpoint when flag is ON."""
        import os
        os.environ["FEATURE_FLEET_WORKPLACE"] = "true"
        clear_flag_cache()
        
        response = client.get("/v1/fleet/overview?org_id=ORG123")
        assert response.status_code == 200
        data = response.json()
        assert "org_id" in data
        assert "vehicles" in data
        assert "participation" in data
        assert "esg_report_url" in data
    
    def test_iot_flag_off(self):
        """Test IoT endpoint when flag is OFF."""
        response = client.post("/v1/iot/link_device", json={
            "provider": "nest",
            "device_id": "device_123",
            "user_id": TEST_USER_ID
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_iot_flag_on(self):
        """Test IoT endpoint when flag is ON."""
        import os
        os.environ["FEATURE_SMART_HOME_IOT"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/iot/link_device", json={
            "provider": "nest",
            "device_id": "device_123",
            "user_id": TEST_USER_ID
        })
        assert response.status_code == 200
        data = response.json()
        assert "provider" in data
        assert "device_id" in data
        assert "user_id" in data
        assert "status" in data
    
    def test_deals_flag_off(self):
        """Test deals endpoint when flag is OFF."""
        response = client.get("/v1/deals/green_hours?lat=37.7749&lng=-122.4194")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_deals_flag_on(self):
        """Test deals endpoint when flag is ON."""
        import os
        os.environ["FEATURE_CONTEXTUAL_COMMERCE"] = "true"
        clear_flag_cache()
        
        response = client.get("/v1/deals/green_hours?lat=37.7749&lng=-122.4194")
        assert response.status_code == 200
        data = response.json()
        assert "window" in data
        assert "deals" in data
    
    def test_events_flag_off(self):
        """Test events endpoint when flag is OFF."""
        response = client.post("/v1/events/create", json={
            "host_id": TEST_USER_ID,
            "schedule": {"start": "2024-01-15T14:00:00Z", "end": "2024-01-15T16:00:00Z"},
            "boost_rate": 1.5
        })
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_events_flag_on(self):
        """Test events endpoint when flag is ON."""
        import os
        os.environ["FEATURE_ENERGY_EVENTS"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/events/create", json={
            "host_id": TEST_USER_ID,
            "schedule": {"start": "2024-01-15T14:00:00Z", "end": "2024-01-15T16:00:00Z"},
            "boost_rate": 1.5
        })
        assert response.status_code == 200
        data = response.json()
        assert "event_id" in data
        assert "host_id" in data
        assert "schedule" in data
        assert "boost_rate" in data
    
    def test_tenant_flag_off(self):
        """Test tenant endpoint when flag is OFF."""
        response = client.get(f"/v1/tenant/{TEST_TENANT_ID}/modules")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_tenant_flag_on(self):
        """Test tenant endpoint when flag is ON."""
        import os
        os.environ["FEATURE_UAP_PARTNERSHIPS"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/tenant/{TEST_TENANT_ID}/modules")
        assert response.status_code == 200
        data = response.json()
        assert "tenant_id" in data
        assert "modules" in data
        assert "branding" in data
    
    def test_ai_rewards_flag_off(self):
        """Test AI rewards endpoint when flag is OFF."""
        response = client.post("/v1/ai/rewards/suggest")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_ai_rewards_flag_on(self):
        """Test AI rewards endpoint when flag is ON."""
        import os
        os.environ["FEATURE_AI_REWARD_OPT"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/ai/rewards/suggest")
        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data
    
    def test_finance_flag_off(self):
        """Test finance endpoint when flag is OFF."""
        response = client.get(f"/v1/finance/offers?user_id={TEST_USER_ID}")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_finance_flag_on(self):
        """Test finance endpoint when flag is ON."""
        import os
        os.environ["FEATURE_ESG_FINANCE_GATEWAY"] = "true"
        clear_flag_cache()
        
        response = client.get(f"/v1/finance/offers?user_id={TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "offers" in data
    
    def test_ai_growth_flag_off(self):
        """Test AI growth endpoint when flag is OFF."""
        response = client.post("/v1/ai/growth/campaigns/generate")
        assert response.status_code == 404
        assert "Feature not enabled" in response.json()["detail"]
    
    def test_ai_growth_flag_on(self):
        """Test AI growth endpoint when flag is ON."""
        import os
        os.environ["FEATURE_AI_GROWTH_AUTOMATION"] = "true"
        clear_flag_cache()
        
        response = client.post("/v1/ai/growth/campaigns/generate")
        assert response.status_code == 200
        data = response.json()
        assert "campaign_id" in data
        assert "variants" in data
