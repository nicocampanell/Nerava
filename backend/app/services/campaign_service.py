"""
Campaign Service — CRUD + budget management for campaigns.

Key mechanics per review:
- Prepaid campaign budget (admin-managed initially)
- Budget depletion → auto-pause
- Driver caps enforced
- Refund/clawback support
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.session_event import IncentiveGrant

logger = logging.getLogger(__name__)


class CampaignService:
    """CRUD and budget management for campaigns."""

    @staticmethod
    def create_campaign(
        db: Session,
        *,
        sponsor_name: str,
        name: str,
        campaign_type: str,
        budget_cents: int,
        cost_per_session_cents: int,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        description: Optional[str] = None,
        sponsor_email: Optional[str] = None,
        sponsor_logo_url: Optional[str] = None,
        sponsor_type: Optional[str] = None,
        priority: int = 100,
        rule_min_duration_minutes: int = 15,
        rules: Optional[Dict[str, Any]] = None,
        caps: Optional[Dict[str, Any]] = None,
        created_by_user_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        offer_url: Optional[str] = None,
    ) -> Campaign:
        """Create a new campaign in draft status."""
        campaign = Campaign(
            id=str(uuid.uuid4()),
            sponsor_name=sponsor_name,
            sponsor_email=sponsor_email,
            sponsor_logo_url=sponsor_logo_url,
            sponsor_type=sponsor_type,
            name=name,
            description=description,
            campaign_type=campaign_type,
            status="draft",
            priority=priority,
            budget_cents=budget_cents,
            cost_per_session_cents=cost_per_session_cents,
            start_date=start_date,
            end_date=end_date,
            rule_min_duration_minutes=max(rule_min_duration_minutes, 1),
            created_by_user_id=created_by_user_id,
            metadata_json=metadata,
            offer_url=offer_url,
        )

        # Apply targeting rules
        if rules:
            campaign.rule_charger_ids = rules.get("charger_ids")
            campaign.rule_charger_networks = rules.get("charger_networks")
            campaign.rule_zone_ids = rules.get("zone_ids")
            campaign.rule_geo_center_lat = rules.get("geo_center_lat")
            campaign.rule_geo_center_lng = rules.get("geo_center_lng")
            campaign.rule_geo_radius_m = rules.get("geo_radius_m")
            campaign.rule_time_start = rules.get("time_start")
            campaign.rule_time_end = rules.get("time_end")
            campaign.rule_days_of_week = rules.get("days_of_week")
            if rules.get("min_duration_minutes"):
                campaign.rule_min_duration_minutes = max(rules["min_duration_minutes"], 1)
            campaign.rule_max_duration_minutes = rules.get("max_duration_minutes")
            campaign.rule_min_power_kw = rules.get("min_power_kw")
            campaign.rule_connector_types = rules.get("connector_types")
            campaign.rule_driver_session_count_min = rules.get("driver_session_count_min")
            campaign.rule_driver_session_count_max = rules.get("driver_session_count_max")
            campaign.rule_driver_allowlist = rules.get("driver_allowlist")

        # Apply driver caps
        if caps:
            campaign.max_grants_per_driver_per_day = caps.get("per_day")
            campaign.max_grants_per_driver_per_campaign = caps.get("per_campaign")
            campaign.max_grants_per_driver_per_charger = caps.get("per_charger")

        db.add(campaign)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.error("Failed to create campaign %s", campaign.id, exc_info=True)
            raise
        logger.info(f"Created campaign {campaign.id}: {name} (sponsor={sponsor_name})")
        return campaign

    @staticmethod
    def update_campaign(
        db: Session,
        campaign_id: str,
        **kwargs,
    ) -> Optional[Campaign]:
        """Update campaign fields. Only draft/paused campaigns can be edited."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None

        if campaign.status not in ("draft", "paused"):
            raise ValueError(f"Cannot edit campaign in '{campaign.status}' status")

        allowed_fields = {
            "name",
            "description",
            "campaign_type",
            "priority",
            "budget_cents",
            "cost_per_session_cents",
            "max_sessions",
            "start_date",
            "end_date",
            "auto_renew",
            "auto_renew_budget_cents",
            "max_grants_per_driver_per_day",
            "max_grants_per_driver_per_campaign",
            "max_grants_per_driver_per_charger",
            "rule_charger_ids",
            "rule_charger_networks",
            "rule_zone_ids",
            "rule_geo_center_lat",
            "rule_geo_center_lng",
            "rule_geo_radius_m",
            "rule_time_start",
            "rule_time_end",
            "rule_days_of_week",
            "rule_min_duration_minutes",
            "rule_max_duration_minutes",
            "rule_min_power_kw",
            "rule_connector_types",
            "rule_driver_session_count_min",
            "rule_driver_session_count_max",
            "rule_driver_allowlist",
            "sponsor_name",
            "sponsor_email",
            "sponsor_logo_url",
            "sponsor_type",
            "offer_url",
        }

        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(campaign, key, value)

        campaign.updated_at = datetime.utcnow()
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.error("Failed to update campaign %s", campaign.id, exc_info=True)
            raise
        return campaign

    @staticmethod
    def fund_campaign(
        db: Session,
        checkout_session_id: str,
        payment_intent_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Mark a campaign as funded after Stripe checkout completes.
        Called from the Stripe webhook handler.

        Fee-inclusive: the sponsor paid gross_amount, Nerava keeps the platform fee,
        and budget_cents is reduced to the net amount available for driver rewards.
        """
        campaign = (
            db.query(Campaign)
            .filter(Campaign.stripe_checkout_session_id == checkout_session_id)
            .first()
        )
        if not campaign:
            logger.warning(f"No campaign found for checkout session {checkout_session_id}")
            return {"status": "error", "message": "Campaign not found for checkout session"}

        if campaign.funding_status == "funded":
            # Still activate if somehow funded but stuck in draft
            if campaign.status == "draft":
                campaign.status = "active"
                campaign.updated_at = datetime.utcnow()
                db.commit()
                logger.info(
                    f"Campaign {campaign.id} activated on re-fund (was funded but stuck in draft)"
                )
                return {"status": "activated", "campaign_id": campaign.id}
            logger.info(f"Campaign {campaign.id} already funded (idempotent)")
            return {"status": "already_processed", "campaign_id": campaign.id}

        # Apply platform fee — reduce budget to net amount for driver rewards
        metadata = metadata or {}
        gross_cents = int(metadata.get("gross_amount_cents", 0))
        fee_cents = int(metadata.get("platform_fee_cents", 0))
        net_cents = int(metadata.get("net_reward_cents", 0))

        if gross_cents and fee_cents and net_cents:
            # New flow with fee breakdown in metadata
            campaign.gross_funding_cents = gross_cents
            campaign.platform_fee_cents = fee_cents
            campaign.budget_cents = net_cents
            logger.info(
                f"Campaign {campaign.id} fee applied: "
                f"gross=${gross_cents/100:.2f}, fee=${fee_cents/100:.2f}, "
                f"net=${net_cents/100:.2f}"
            )
        else:
            # Legacy: no fee metadata (older checkout sessions)
            campaign.gross_funding_cents = campaign.budget_cents
            campaign.platform_fee_cents = 0

        campaign.funding_status = "funded"
        campaign.stripe_payment_intent_id = payment_intent_id
        campaign.funded_at = datetime.utcnow()
        campaign.updated_at = datetime.utcnow()

        # Auto-activate campaign when funded (if still in draft)
        if campaign.status == "draft":
            campaign.status = "active"
            logger.info(f"Campaign {campaign.id} auto-activated on funding")

        db.commit()

        logger.info(f"Campaign {campaign.id} funded via Stripe checkout {checkout_session_id}")
        return {"status": "success", "campaign_id": campaign.id, "action": "funded"}

    @staticmethod
    def activate_campaign(db: Session, campaign_id: str) -> Optional[Campaign]:
        """Move campaign from draft → active. Requires funding_status == 'funded'."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None
        if campaign.status != "draft":
            raise ValueError(
                f"Can only activate draft campaigns, current status: {campaign.status}"
            )
        if getattr(campaign, "funding_status", "funded") not in ("funded",):
            raise ValueError(
                f"Campaign must be funded before activation (current: {campaign.funding_status}). "
                "Use the checkout endpoint to fund this campaign."
            )
        campaign.status = "active"
        campaign.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Activated campaign {campaign_id}")
        return campaign

    @staticmethod
    def pause_campaign(
        db: Session, campaign_id: str, reason: Optional[str] = None
    ) -> Optional[Campaign]:
        """Pause an active campaign."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None
        if campaign.status != "active":
            raise ValueError(f"Can only pause active campaigns, current status: {campaign.status}")
        campaign.status = "paused"
        campaign.updated_at = datetime.utcnow()
        if reason:
            meta = campaign.metadata_json or {}
            meta["pause_reason"] = reason
            meta["paused_at"] = datetime.utcnow().isoformat()
            campaign.metadata_json = meta
        db.commit()
        logger.info(f"Paused campaign {campaign_id}: {reason}")
        return campaign

    @staticmethod
    def resume_campaign(db: Session, campaign_id: str) -> Optional[Campaign]:
        """Resume a paused campaign."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return None
        if campaign.status != "paused":
            raise ValueError(f"Can only resume paused campaigns, current status: {campaign.status}")
        if campaign.spent_cents >= campaign.budget_cents:
            raise ValueError("Cannot resume: budget exhausted")
        campaign.status = "active"
        campaign.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Resumed campaign {campaign_id}")
        return campaign

    @staticmethod
    def get_active_campaigns(db: Session) -> List[Campaign]:
        """Get all campaigns with status='active' and budget remaining."""
        now = datetime.utcnow()
        return (
            db.query(Campaign)
            .filter(
                Campaign.status == "active",
                Campaign.start_date <= now,
                Campaign.spent_cents < Campaign.budget_cents,
            )
            .filter(
                # end_date is null (ongoing) OR end_date is in the future
                (Campaign.end_date.is_(None))
                | (Campaign.end_date >= now)
            )
            .order_by(Campaign.priority.asc())
            .all()
        )

    @staticmethod
    def get_campaign(db: Session, campaign_id: str) -> Optional[Campaign]:
        return db.query(Campaign).filter(Campaign.id == campaign_id).first()

    @staticmethod
    def list_campaigns(
        db: Session,
        sponsor_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        owner_user_id: Optional[int] = None,
    ) -> List[Campaign]:
        query = db.query(Campaign)
        if owner_user_id is not None:
            query = query.filter(Campaign.created_by_user_id == owner_user_id)
        if sponsor_name:
            query = query.filter(Campaign.sponsor_name == sponsor_name)
        if status:
            query = query.filter(Campaign.status == status)
        return query.order_by(Campaign.created_at.desc()).offset(offset).limit(limit).all()

    @staticmethod
    def check_budget(db: Session, campaign_id: str) -> dict:
        """Check remaining budget and session counts."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return {}
        remaining = campaign.budget_cents - campaign.spent_cents
        pct_used = (
            (campaign.spent_cents / campaign.budget_cents * 100) if campaign.budget_cents > 0 else 0
        )
        return {
            "budget_cents": campaign.budget_cents,
            "spent_cents": campaign.spent_cents,
            "remaining_cents": remaining,
            "pct_used": round(pct_used, 1),
            "sessions_granted": campaign.sessions_granted,
            "max_sessions": campaign.max_sessions,
        }

    @staticmethod
    def decrement_budget_atomic(db: Session, campaign_id: str, amount_cents: int) -> bool:
        """
        Atomically decrement campaign budget using SELECT FOR UPDATE.
        Returns False if insufficient budget or campaign not active.
        """
        # Lock the campaign row to prevent concurrent grants
        campaign = (
            db.query(Campaign)
            .filter(
                Campaign.id == campaign_id,
                Campaign.status == "active",
            )
            .with_for_update()
            .first()
        )

        if not campaign:
            logger.info(f"Budget decrement failed for {campaign_id}: not found or not active")
            return False

        if campaign.spent_cents + amount_cents > campaign.budget_cents:
            logger.info(
                f"Budget decrement failed for {campaign_id}: "
                f"spent={campaign.spent_cents} + {amount_cents} > budget={campaign.budget_cents}"
            )
            return False

        # Check max_sessions cap
        if campaign.max_sessions and campaign.sessions_granted >= campaign.max_sessions:
            logger.info(f"Budget decrement failed for {campaign_id}: max sessions reached")
            return False

        # Increment via ORM (tracked in same transaction, commits with everything else)
        campaign.spent_cents += amount_cents
        campaign.sessions_granted += 1
        campaign.updated_at = datetime.utcnow()

        logger.info(
            f"Budget decremented for {campaign_id}: spent={campaign.spent_cents}c "
            f"(+{amount_cents}c), sessions={campaign.sessions_granted}"
        )

        # Auto-pause if budget exhausted
        if campaign.spent_cents >= campaign.budget_cents:
            campaign.status = "exhausted"
            logger.info(f"Campaign {campaign_id} budget exhausted, auto-paused")
        elif campaign.max_sessions and campaign.sessions_granted >= campaign.max_sessions:
            campaign.status = "exhausted"
            logger.info(f"Campaign {campaign_id} max sessions reached, auto-paused")

        db.flush()
        return True

    @staticmethod
    def check_driver_caps(
        db: Session,
        campaign: Campaign,
        driver_id: int,
        charger_id: Optional[str] = None,
    ) -> bool:
        """
        Check if driver has exceeded any caps for this campaign.
        Returns True if driver is within caps (eligible).
        """
        # Per-campaign lifetime cap
        if campaign.max_grants_per_driver_per_campaign:
            total = (
                db.query(func.count(IncentiveGrant.id))
                .filter(
                    IncentiveGrant.campaign_id == campaign.id,
                    IncentiveGrant.driver_user_id == driver_id,
                )
                .scalar()
                or 0
            )
            if total >= campaign.max_grants_per_driver_per_campaign:
                return False

        # Per-day cap
        if campaign.max_grants_per_driver_per_day:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            daily = (
                db.query(func.count(IncentiveGrant.id))
                .filter(
                    IncentiveGrant.campaign_id == campaign.id,
                    IncentiveGrant.driver_user_id == driver_id,
                    IncentiveGrant.created_at >= today_start,
                )
                .scalar()
                or 0
            )
            if daily >= campaign.max_grants_per_driver_per_day:
                return False

        # Per-charger cap
        if campaign.max_grants_per_driver_per_charger and charger_id:
            from app.models.session_event import SessionEvent

            charger_grants = (
                db.query(func.count(IncentiveGrant.id))
                .join(SessionEvent, SessionEvent.id == IncentiveGrant.session_event_id)
                .filter(
                    IncentiveGrant.campaign_id == campaign.id,
                    IncentiveGrant.driver_user_id == driver_id,
                    SessionEvent.charger_id == charger_id,
                )
                .scalar()
                or 0
            )
            if charger_grants >= campaign.max_grants_per_driver_per_charger:
                return False

        return True

    @staticmethod
    def clawback_grant(db: Session, grant_id: str, reason: str = "session_invalidated") -> bool:
        """
        Clawback a grant (e.g., session later invalidated).
        Refunds campaign budget, debits driver Nova.
        """
        grant = db.query(IncentiveGrant).filter(IncentiveGrant.id == grant_id).first()
        if not grant or grant.status == "clawed_back":
            return False

        campaign = db.query(Campaign).filter(Campaign.id == grant.campaign_id).first()
        if campaign:
            campaign.spent_cents = max(0, campaign.spent_cents - grant.amount_cents)
            campaign.sessions_granted = max(0, campaign.sessions_granted - 1)
            # If campaign was exhausted, re-activate it
            if campaign.status == "exhausted":
                campaign.status = "active"

        grant.status = "clawed_back"
        grant.grant_metadata = grant.grant_metadata or {}
        grant.grant_metadata["clawback_reason"] = reason
        grant.grant_metadata["clawed_back_at"] = datetime.utcnow().isoformat()

        db.commit()
        logger.info(f"Clawed back grant {grant_id}: {reason}")
        return True
