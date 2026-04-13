"""
Tests for campaign offer_url field — verifies that partner/sponsor offer URLs
are stored on campaigns and served through the driver-facing API.

This test was created to confirm the EVject discount URL is served from
campaign data (backend) rather than hardcoded in the frontend.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.models.campaign import Campaign
from app.models.user import User
from app.services.campaign_service import CampaignService


def _make_user(db, email: str = "driver@test.com", role_flags: str = "driver"):
    user = User(
        email=email,
        password_hash="hashed",
        is_active=True,
        role_flags=role_flags,
    )
    db.add(user)
    db.flush()
    return user


def _make_campaign(
    db,
    offer_url=None,
    status: str = "active",
    sponsor_name: str = "EVject",
    name: str = "10% Off EVject Chargers",
):
    """Create a campaign with optional offer_url, defaulting to active status."""
    campaign = Campaign(
        id=str(uuid.uuid4()),
        sponsor_name=sponsor_name,
        name=name,
        description="Tap to claim your discount on EVject charging equipment",
        campaign_type="custom",
        status=status,
        priority=50,
        budget_cents=100000,
        spent_cents=0,
        cost_per_session_cents=0,
        sessions_granted=0,
        start_date=datetime.utcnow() - timedelta(days=1),
        end_date=datetime.utcnow() + timedelta(days=30),
        rule_min_duration_minutes=15,
        offer_url=offer_url,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


class TestCampaignOfferUrl:
    """Tests for the offer_url column on the Campaign model."""

    def test_campaign_model_has_offer_url_column(self, db):
        """Campaign model should have an offer_url column that accepts strings."""
        url = "https://evject.com/discount/nerava26"
        campaign = _make_campaign(db, offer_url=url)

        assert campaign.offer_url == url

    def test_campaign_offer_url_defaults_to_none(self, db):
        """Campaign without offer_url should default to None."""
        campaign = _make_campaign(db)

        assert campaign.offer_url is None

    def test_campaign_offer_url_persists_after_refresh(self, db):
        """offer_url should survive a DB round-trip."""
        url = "https://evject.com/discount/nerava26"
        campaign = _make_campaign(db, offer_url=url)
        campaign_id = campaign.id

        # Re-query from DB
        loaded = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        assert loaded is not None
        assert loaded.offer_url == url

    def test_create_campaign_service_with_offer_url(self, db):
        """CampaignService.create_campaign should accept and store offer_url."""
        user = _make_user(db, email="admin@test.com", role_flags="admin")
        campaign = CampaignService.create_campaign(
            db,
            sponsor_name="EVject",
            name="10% Off EVject Chargers",
            campaign_type="custom",
            budget_cents=100000,
            cost_per_session_cents=0,
            start_date=datetime.utcnow(),
            offer_url="https://evject.com/discount/nerava26",
            created_by_user_id=user.id,
        )

        assert campaign.offer_url == "https://evject.com/discount/nerava26"

    def test_update_campaign_offer_url(self, db):
        """CampaignService.update_campaign should allow updating offer_url."""
        campaign = _make_campaign(db, status="draft")
        assert campaign.offer_url is None

        updated = CampaignService.update_campaign(
            db, campaign.id, offer_url="https://evject.com/discount/nerava26"
        )

        assert updated is not None
        assert updated.offer_url == "https://evject.com/discount/nerava26"

    def test_update_campaign_clear_offer_url(self, db):
        """Setting offer_url to empty string should clear it."""
        campaign = _make_campaign(
            db, offer_url="https://evject.com/discount/nerava26", status="draft"
        )
        assert campaign.offer_url is not None

        # Update to empty string — validator normalizes to None
        updated = CampaignService.update_campaign(db, campaign.id, offer_url="")
        assert updated is not None
        assert updated.offer_url is None

    def test_offer_url_in_driver_active_response(self, db):
        """offer_url should appear in the driver/active campaign API response dict."""
        url = "https://evject.com/discount/nerava26"
        campaign = _make_campaign(db, offer_url=url)

        # Simulate the response dict built by the /driver/active endpoint
        response_dict = {
            "id": campaign.id,
            "name": campaign.name,
            "sponsor_name": campaign.sponsor_name,
            "sponsor_logo_url": campaign.sponsor_logo_url,
            "description": campaign.description,
            "reward_cents": campaign.cost_per_session_cents,
            "campaign_type": campaign.campaign_type,
            "eligible": True,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            "offer_url": campaign.offer_url,
        }

        assert response_dict["offer_url"] == url

    def test_campaign_without_offer_url_returns_none(self, db):
        """Campaigns without offer_url should return None in the response."""
        campaign = _make_campaign(db)

        response_dict = {
            "offer_url": campaign.offer_url,
        }

        assert response_dict["offer_url"] is None

    def test_offer_url_in_campaign_to_dict(self, db):
        """_campaign_to_dict helper should include offer_url."""
        from app.routers.campaigns import _campaign_to_dict

        url = "https://evject.com/discount/nerava26"
        campaign = _make_campaign(db, offer_url=url)

        result = _campaign_to_dict(campaign)
        assert "offer_url" in result
        assert result["offer_url"] == url

    def test_offer_url_long_url_accepted(self, db):
        """offer_url should accept URLs up to 500 characters."""
        long_url = "https://evject.com/discount/" + "a" * 460
        assert len(long_url) <= 500

        campaign = _make_campaign(db, offer_url=long_url)
        assert campaign.offer_url == long_url

    def test_get_active_campaigns_includes_offer_url(self, db):
        """Active campaigns returned by CampaignService should have offer_url populated."""
        url = "https://evject.com/discount/nerava26"
        _make_campaign(db, offer_url=url, status="active")

        active = CampaignService.get_active_campaigns(db)
        assert len(active) >= 1

        evject = next((c for c in active if c.sponsor_name == "EVject"), None)
        assert evject is not None
        assert evject.offer_url == url
