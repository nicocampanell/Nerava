"""
Tests for Domain Hub API endpoint.

Tests the GET /v1/hubs/domain endpoint that returns Domain hub chargers and merchants.
"""
import uuid

import pytest
from app.db import Base, SessionLocal, get_engine
from app.domains.domain_hub import DOMAIN_CHARGERS, HUB_ID, HUB_NAME
from app.main_simple import app
from app.models_while_you_charge import Charger, ChargerMerchant, Merchant, MerchantPerk
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

client = TestClient(app)


@pytest.fixture
def db():
    """Create a test database session."""
    # Drop all tables and recreate them to ensure schema matches models
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Clean up after test
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def seeded_domain_hub(db: Session):
    """
    Fixture that seeds Domain hub with chargers and merchants.
    
    Returns the seeded charger and merchant IDs.
    """
    # Seed Domain chargers
    chargers = []
    for charger_config in DOMAIN_CHARGERS:
        charger = Charger(
            id=charger_config["id"],
            external_id=charger_config.get("external_id"),
            name=charger_config["name"],
            network_name=charger_config["network_name"],
            lat=charger_config["lat"],
            lng=charger_config["lng"],
            address=charger_config.get("address"),
            city=charger_config.get("city", "Austin"),
            state=charger_config.get("state", "TX"),
            zip_code=charger_config.get("zip_code"),
            connector_types=charger_config.get("connector_types", []),
            power_kw=charger_config.get("power_kw"),
            is_public=charger_config.get("is_public", True),
            status="available"
        )
        db.add(charger)
        chargers.append(charger)
    
    # Seed a test merchant
    merchant = Merchant(
        id=f"m_test_{uuid.uuid4().hex[:8]}",
        external_id=None,
        name="Test Domain Merchant",
        category="coffee",
        lat=30.4025,
        lng=-97.7260,
        address="11500 Domain Dr, Austin, TX 78758",
        city="Austin",
        state="TX",
        rating=4.5
    )
    db.add(merchant)
    
    # Link merchant to first Domain charger
    if chargers:
        charger_merchant_link = ChargerMerchant(
            charger_id=chargers[0].id,
            merchant_id=merchant.id,
            distance_m=150.0,
            walk_duration_s=180,  # 3 minutes
            walk_distance_m=200.0
        )
        db.add(charger_merchant_link)
        
        # Add a perk for the merchant
        perk = MerchantPerk(
            merchant_id=merchant.id,
            title="Earn 12 Nova",
            description="Test perk",
            nova_reward=12,
            is_active=True
        )
        db.add(perk)
    
    db.commit()
    
    return {
        "chargers": chargers,
        "merchant": merchant
    }


def test_domain_hub_endpoint_returns_hub_info(seeded_domain_hub, db: Session):
    """Test that the Domain hub endpoint returns correct hub information."""
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check hub metadata
    assert "hub_id" in data
    assert data["hub_id"] == HUB_ID
    assert "hub_name" in data
    assert data["hub_name"] == HUB_NAME


def test_domain_hub_endpoint_returns_chargers(seeded_domain_hub, db: Session):
    """Test that the Domain hub endpoint returns at least one charger."""
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check chargers
    assert "chargers" in data
    assert isinstance(data["chargers"], list)
    assert len(data["chargers"]) >= 1, "Should return at least one charger"
    
    # Verify charger structure
    charger = data["chargers"][0]
    assert "id" in charger
    assert "name" in charger
    assert "lat" in charger
    assert "lng" in charger
    assert "network_name" in charger
    
    # Verify charger IDs match Domain config
    charger_ids = [ch["id"] for ch in data["chargers"]]
    expected_charger_ids = {ch["id"] for ch in DOMAIN_CHARGERS}
    assert set(charger_ids) == expected_charger_ids, "Should return all Domain chargers"


def test_domain_hub_endpoint_returns_merchants(seeded_domain_hub, db: Session):
    """Test that the Domain hub endpoint returns merchants when linked."""
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Check merchants
    assert "merchants" in data
    assert isinstance(data["merchants"], list)
    assert len(data["merchants"]) >= 1, "Should return at least one merchant"
    
    # Verify merchant structure
    merchant = data["merchants"][0]
    assert "id" in merchant
    assert "name" in merchant
    assert "lat" in merchant
    assert "lng" in merchant
    assert "category" in merchant
    assert "nova_reward" in merchant
    assert "walk_minutes" in merchant


def test_domain_hub_endpoint_charger_merchant_linking(seeded_domain_hub, db: Session):
    """Test that merchants are properly linked to Domain chargers."""
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify merchants have walk times (indicating they're linked)
    for merchant in data["merchants"]:
        assert merchant.get("walk_minutes") is not None, "Merchants should have walk_minutes"
        assert merchant.get("walk_minutes") >= 0, "Walk minutes should be non-negative"


def test_domain_hub_endpoint_merchant_perks(seeded_domain_hub, db: Session):
    """Test that merchants include perk information."""
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Find the test merchant we seeded
    test_merchant = next((m for m in data["merchants"] if m["name"] == "Test Domain Merchant"), None)
    
    if test_merchant:
        assert "nova_reward" in test_merchant
        assert test_merchant["nova_reward"] == 12, "Merchant should have the perk's nova_reward"


def test_domain_hub_endpoint_without_seeded_data(db: Session):
    """Test that the endpoint still works even if chargers aren't seeded (returns config data)."""
    # Clear any existing data
    db.query(ChargerMerchant).delete()
    db.query(Merchant).delete()
    db.query(Charger).delete()
    db.commit()
    
    response = client.get("/v1/hubs/domain")
    
    assert response.status_code == 200
    data = response.json()
    
    # Should still return hub info and charger config
    assert data["hub_id"] == HUB_ID
    assert len(data["chargers"]) == len(DOMAIN_CHARGERS), "Should return all Domain charger configs"
    # Merchants may be empty if none are seeded
    assert isinstance(data["merchants"], list)

