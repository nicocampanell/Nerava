"""
Tests for Intent Capture API
"""

from unittest.mock import AsyncMock, patch

import pytest
from app.models import Charger, IntentSession, User
from app.services.geo import haversine_m as haversine_distance
from app.services.intent_service import (
    assign_confidence_tier,
    find_nearest_charger,
    validate_location_accuracy,
)


@pytest.fixture
def mock_user(db):
    """Create a test user"""
    user = User(
        id=1,
        public_id="test-user-123",
        email="test@example.com",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def mock_charger(db):
    """Create a test charger"""
    charger = Charger(
        id="ch_test_1",
        name="Test Charger",
        lat=37.7749,
        lng=-122.4194,
        is_public=True,
        network_name="Test Network",
    )
    db.add(charger)
    db.commit()
    return charger


class TestIntentService:
    """Test IntentService functions"""

    def test_assign_confidence_tier_a(self):
        """Test Tier A assignment (<120m)"""
        assert assign_confidence_tier(100.0) == "A"
        assert assign_confidence_tier(120.0) == "A"
        assert assign_confidence_tier(0.0) == "A"

    def test_assign_confidence_tier_b(self):
        """Test Tier B assignment (<400m)"""
        assert assign_confidence_tier(121.0) == "B"
        assert assign_confidence_tier(400.0) == "B"
        assert assign_confidence_tier(200.0) == "B"

    def test_assign_confidence_tier_c(self):
        """Test Tier C assignment (no charger or >400m)"""
        assert assign_confidence_tier(None) == "C"
        assert assign_confidence_tier(401.0) == "C"
        assert assign_confidence_tier(1000.0) == "C"

    def test_validate_location_accuracy_good(self):
        """Test location accuracy validation - good accuracy"""
        assert validate_location_accuracy(50.0) is True
        assert validate_location_accuracy(100.0) is True
        assert validate_location_accuracy(None) is True  # None is allowed

    def test_validate_location_accuracy_poor(self):
        """Test location accuracy validation - poor accuracy"""
        assert validate_location_accuracy(101.0) is False
        assert validate_location_accuracy(200.0) is False

    def test_haversine_distance(self):
        """Test Haversine distance calculation"""
        # Distance between San Francisco and Oakland (approx 20km)
        sf_lat, sf_lng = 37.7749, -122.4194
        oak_lat, oak_lng = 37.8044, -122.2711

        distance = haversine_distance(sf_lat, sf_lng, oak_lat, oak_lng)
        assert 19000 < distance < 21000  # Approximately 20km

    def test_find_nearest_charger(self, db, mock_charger):
        """Test finding nearest charger"""
        # Search near the charger location
        result = find_nearest_charger(db, 37.7749, -122.4194)
        assert result is not None
        charger, distance = result
        assert charger.id == mock_charger.id
        assert distance < 100  # Should be very close

    def test_find_nearest_charger_none(self, db):
        """Test finding charger when none exist"""
        # Clear all chargers
        db.query(Charger).delete()
        db.commit()

        result = find_nearest_charger(db, 37.7749, -122.4194)
        assert result is None


class TestIntentCaptureEndpoint:
    """Test POST /v1/intent/capture endpoint"""

    @pytest.mark.asyncio
    @patch("app.services.intent_service.get_merchants_for_intent", new_callable=AsyncMock)
    @patch("app.services.intent_service.create_intent_session", new_callable=AsyncMock)
    @patch("app.routers.intent.get_current_user")
    async def test_capture_intent_tier_a(
        self,
        mock_get_user,
        mock_create_session,
        mock_get_merchants,
        client,
        mock_user,
        mock_charger,
    ):
        """Test intent capture with Tier A confidence"""
        mock_get_user.return_value = mock_user

        # Mock session creation
        session = IntentSession(
            id="session-123",
            user_id=mock_user.id,
            lat=37.7749,
            lng=-122.4194,
            charger_id=mock_charger.id,
            charger_distance_m=100.0,
            confidence_tier="A",
            source="web",
        )
        mock_create_session.return_value = session

        # Mock merchants
        mock_get_merchants.return_value = [
            {
                "place_id": "place_1",
                "name": "Test Merchant",
                "lat": 37.7750,
                "lng": -122.4195,
                "distance_m": 50,
                "types": ["restaurant"],
                "photo_url": "https://example.com/photo.jpg",
            }
        ]

        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 37.7749,
                "lng": -122.4194,
                "accuracy_m": 50.0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["confidence_tier"] == "A"
        assert len(data["merchants"]) > 0
        assert data["fallback_message"] is None

    @pytest.mark.asyncio
    @patch("app.services.intent_service.get_merchants_for_intent", new_callable=AsyncMock)
    @patch("app.services.intent_service.create_intent_session", new_callable=AsyncMock)
    @patch("app.routers.intent.get_current_user")
    async def test_capture_intent_tier_c(
        self,
        mock_get_user,
        mock_create_session,
        mock_get_merchants,
        client,
        mock_user,
    ):
        """Test intent capture with Tier C confidence (no charger)"""
        mock_get_user.return_value = mock_user

        session = IntentSession(
            id="session-123",
            user_id=mock_user.id,
            lat=37.7749,
            lng=-122.4194,
            charger_id=None,
            charger_distance_m=None,
            confidence_tier="C",
            source="web",
        )
        mock_create_session.return_value = session
        mock_get_merchants.return_value = []

        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 37.7749,
                "lng": -122.4194,
                "accuracy_m": 50.0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["confidence_tier"] == "C"
        assert len(data["merchants"]) == 0
        assert data["fallback_message"] is not None

    @patch("app.routers.intent.get_current_user")
    def test_capture_intent_poor_accuracy(
        self,
        mock_get_user,
        client,
        mock_user,
    ):
        """Test intent capture with poor location accuracy"""
        mock_get_user.return_value = mock_user

        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 37.7749,
                "lng": -122.4194,
                "accuracy_m": 200.0,  # Exceeds threshold
            },
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("app.services.intent_service.get_merchants_for_intent", new_callable=AsyncMock)
    @patch("app.services.intent_service.create_intent_session", new_callable=AsyncMock)
    @patch("app.dependencies.domain.get_current_user_public_id")
    async def test_capture_intent_dev_anon_enabled(
        self,
        mock_get_public_id,
        mock_create_session,
        mock_get_merchants,
        client,
        db,
        mock_user,
        mock_charger,
    ):
        """Test /v1/intent/capture returns 200 without auth when NERAVA_DEV_ALLOW_ANON_USER=true"""
        # Mock get_current_user_public_id to return dev user public_id (simulating dev mode)
        mock_get_public_id.return_value = mock_user.public_id

        # Mock session creation
        session = IntentSession(
            id="session-123",
            user_id=mock_user.id,
            lat=30.2672,
            lng=-97.7431,
            charger_id=mock_charger.id,
            charger_distance_m=100.0,
            confidence_tier="A",
            source="web",
        )
        mock_create_session.return_value = session

        # Mock merchants
        mock_get_merchants.return_value = [
            {
                "place_id": "place_1",
                "name": "Test Merchant",
                "lat": 30.2680,
                "lng": -97.7435,
                "distance_m": 50,
                "types": ["restaurant"],
                "photo_url": "https://example.com/photo.jpg",
            }
        ]

        # Make request without Authorization header
        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 30.2672,
                "lng": -97.7431,
                "accuracy_m": 50.0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["confidence_tier"] == "A"
        assert len(data["merchants"]) > 0

    @pytest.mark.asyncio
    @patch("app.dependencies.domain.get_current_user_public_id")
    async def test_capture_intent_auth_required(
        self,
        mock_get_public_id,
        client,
    ):
        """Test /v1/intent/capture returns 401 when flag is false and Authorization is missing"""
        from fastapi import HTTPException, status

        # Mock get_current_user_public_id to raise 401 (simulating auth required)
        mock_get_public_id.side_effect = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Sign in required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

        # Make request without Authorization header
        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 30.2672,
                "lng": -97.7431,
                "accuracy_m": 50.0,
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "unauthorized"
        assert data["message"] == "Sign in required"

    @pytest.mark.asyncio
    @patch("app.services.intent_service.get_merchants_for_intent", new_callable=AsyncMock)
    @patch("app.services.intent_service.create_intent_session", new_callable=AsyncMock)
    @patch("app.routers.intent.get_current_user")
    async def test_capture_intent_mock_places(
        self,
        mock_get_user,
        mock_create_session,
        mock_get_merchants,
        client,
        mock_user,
        mock_charger,
        monkeypatch,
    ):
        """Test /v1/intent/capture returns deterministic merchants when MOCK_PLACES=true"""
        monkeypatch.setenv("MOCK_PLACES", "true")
        mock_get_user.return_value = mock_user

        # Mock session creation
        session = IntentSession(
            id="session-123",
            user_id=mock_user.id,
            lat=30.2672,
            lng=-97.7431,
            charger_id=mock_charger.id,
            charger_distance_m=100.0,
            confidence_tier="A",
            source="web",
        )
        mock_create_session.return_value = session

        # Mock merchants should return fixture merchants from MOCK_PLACES
        # The actual implementation will call search_nearby which checks MOCK_PLACES
        # So we need to mock search_nearby to return fixture merchants
        from app.services.google_places_new import _get_mock_merchants

        mock_get_merchants.return_value = _get_mock_merchants(30.2672, -97.7431)

        # Use test coordinates (30.2672, -97.7431)
        response = client.post(
            "/v1/intent/capture",
            json={
                "lat": 30.2672,
                "lng": -97.7431,
                "accuracy_m": 50.0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["confidence_tier"] == "A"
        # Assert response contains Asadas Grill and Eggman ATX
        merchant_names = [m["name"] for m in data["merchants"]]
        assert "Asadas Grill" in merchant_names
        assert "Eggman ATX" in merchant_names
