"""
Tests for amenity votes API endpoint.

Tests vote creation, update, toggle, validation, and aggregation.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.while_you_charge import Merchant, AmenityVote
from app.core.security import create_access_token


@pytest.fixture
def test_merchant(db: Session):
    """Create a test merchant"""
    merchant = Merchant(
        id="test_merchant_amenity",
        name="Test Merchant for Amenities",
        lat=30.2680,
        lng=-97.7435,
        address="501 W Canyon Ridge Dr, Austin, TX 78753",
        city="Austin",
        state="TX",
        category="restaurant",
        primary_category="food",
    )
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant


@pytest.fixture
def auth_token(test_user, db: Session):
    """Create auth token for test user"""
    from app.services.refresh_token_service import RefreshTokenService
    _, refresh_token = RefreshTokenService.create_refresh_token(db, test_user)
    db.commit()
    
    access_token = create_access_token(test_user.public_id, auth_provider=test_user.auth_provider)
    return access_token


def test_vote_amenity_creates_vote(client: TestClient, test_user, test_merchant, auth_token):
    """Test POST vote creates a new vote"""
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "up"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["upvotes"] == 1
    assert data["downvotes"] == 0


def test_vote_amenity_updates_existing(client: TestClient, test_user, test_merchant, auth_token, db: Session):
    """Test POST same amenity different type updates vote"""
    # Create initial vote
    vote = AmenityVote(
        merchant_id=test_merchant.id,
        user_id=test_user.id,
        amenity="bathroom",
        vote_type="up"
    )
    db.add(vote)
    db.commit()
    
    # Update to different type
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "down"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["upvotes"] == 0
    assert data["downvotes"] == 1
    
    # Verify vote was updated in DB
    updated_vote = db.query(AmenityVote).filter(
        AmenityVote.merchant_id == test_merchant.id,
        AmenityVote.user_id == test_user.id,
        AmenityVote.amenity == "bathroom"
    ).first()
    assert updated_vote is not None
    assert updated_vote.vote_type == "down"


def test_vote_amenity_toggles_removes(client: TestClient, test_user, test_merchant, auth_token, db: Session):
    """Test POST same amenity same type removes vote (toggle)"""
    # Create initial vote
    vote = AmenityVote(
        merchant_id=test_merchant.id,
        user_id=test_user.id,
        amenity="bathroom",
        vote_type="up"
    )
    db.add(vote)
    db.commit()
    
    # Toggle: same vote type should remove
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "up"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["upvotes"] == 0
    assert data["downvotes"] == 0
    
    # Verify vote was removed from DB
    removed_vote = db.query(AmenityVote).filter(
        AmenityVote.merchant_id == test_merchant.id,
        AmenityVote.user_id == test_user.id,
        AmenityVote.amenity == "bathroom"
    ).first()
    assert removed_vote is None


def test_vote_amenity_invalid_amenity(client: TestClient, test_user, test_merchant, auth_token):
    """Test POST invalid amenity returns 400"""
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/invalid/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "up"}
    )
    
    assert response.status_code == 400
    assert "Invalid amenity" in response.json()["detail"]


def test_vote_amenity_invalid_vote_type(client: TestClient, test_user, test_merchant, auth_token):
    """Test POST invalid vote_type returns 400"""
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "invalid"}
    )
    
    assert response.status_code == 422  # Pydantic validation error


def test_vote_amenity_missing_merchant(client: TestClient, test_user, auth_token):
    """Test POST non-existent merchant returns 404"""
    response = client.post(
        "/v1/merchants/nonexistent/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "up"}
    )
    
    assert response.status_code == 404
    assert "Merchant not found" in response.json()["detail"]


def test_vote_amenity_requires_auth(client: TestClient, test_merchant):
    """Test POST without auth token returns 401"""
    response = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        json={"vote_type": "up"}
    )
    
    assert response.status_code == 401


def test_merchant_details_includes_amenities(client: TestClient, test_user, test_merchant, auth_token, db: Session):
    """Test GET merchant details includes amenities field"""
    # Create some votes
    vote1 = AmenityVote(
        merchant_id=test_merchant.id,
        user_id=test_user.id,
        amenity="bathroom",
        vote_type="up"
    )
    vote2 = AmenityVote(
        merchant_id=test_merchant.id,
        user_id=test_user.id,
        amenity="wifi",
        vote_type="down"
    )
    db.add(vote1)
    db.add(vote2)
    db.commit()
    
    # Get merchant details
    response = client.get(f"/v1/merchants/{test_merchant.id}")
    
    assert response.status_code == 200
    data = response.json()
    assert "amenities" in data["merchant"]
    assert data["merchant"]["amenities"]["bathroom"]["upvotes"] == 1
    assert data["merchant"]["amenities"]["bathroom"]["downvotes"] == 0
    assert data["merchant"]["amenities"]["wifi"]["upvotes"] == 0
    assert data["merchant"]["amenities"]["wifi"]["downvotes"] == 1


def test_merchant_details_amenities_zero_counts(client: TestClient, test_merchant):
    """Test GET merchant with no votes returns both amenities with 0 counts"""
    response = client.get(f"/v1/merchants/{test_merchant.id}")
    
    assert response.status_code == 200
    data = response.json()
    assert "amenities" in data["merchant"]
    assert data["merchant"]["amenities"]["bathroom"]["upvotes"] == 0
    assert data["merchant"]["amenities"]["bathroom"]["downvotes"] == 0
    assert data["merchant"]["amenities"]["wifi"]["upvotes"] == 0
    assert data["merchant"]["amenities"]["wifi"]["downvotes"] == 0


def test_vote_amenity_multiple_users_aggregates(client: TestClient, test_user, test_merchant, auth_token, db: Session):
    """Test multiple users voting aggregates correctly"""
    # Create second user
    from app.models import User
    user2 = User(
        email="test2@example.com",
        password_hash="hashed",
        is_active=True,
        role_flags="driver"
    )
    db.add(user2)
    db.commit()
    db.refresh(user2)
    
    # User 1 votes up
    response1 = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"vote_type": "up"}
    )
    assert response1.status_code == 200
    
    # User 2 votes up
    auth_token2 = create_access_token(user2.public_id, auth_provider=user2.auth_provider)
    response2 = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token2}"},
        json={"vote_type": "up"}
    )
    assert response2.status_code == 200
    assert response2.json()["upvotes"] == 2
    
    # User 2 changes to down
    response3 = client.post(
        f"/v1/merchants/{test_merchant.id}/amenities/bathroom/vote",
        headers={"Authorization": f"Bearer {auth_token2}"},
        json={"vote_type": "down"}
    )
    assert response3.status_code == 200
    assert response3.json()["upvotes"] == 1
    assert response3.json()["downvotes"] == 1
