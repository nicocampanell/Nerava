"""
Partner Session Service — Ingest charging sessions from external partners.

Handles session creation, driver resolution (shadow users), charger matching,
quality scoring, and incentive evaluation for partner-submitted sessions.

Supports candidate/pending session state for soft-signal partners,
vehicle info passthrough, and reward breakdown in responses.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.partner import Partner
from app.models.session_event import IncentiveGrant, SessionEvent
from app.models.user import User
from app.services.incentive_engine import IncentiveEngine

logger = logging.getLogger(__name__)


class PartnerSessionService:

    @staticmethod
    def ingest_session(
        db: Session,
        partner: Partner,
        partner_session_id: str,
        partner_driver_id: str,
        status: str,
        session_start: datetime,
        session_end: Optional[datetime] = None,
        charger_id: Optional[str] = None,
        charger_network: Optional[str] = None,
        connector_type: Optional[str] = None,
        power_kw: Optional[float] = None,
        kwh_delivered: Optional[float] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        vehicle_vin: Optional[str] = None,
        vehicle_make: Optional[str] = None,
        vehicle_model: Optional[str] = None,
        vehicle_year: Optional[int] = None,
        battery_start_pct: Optional[int] = None,
        battery_end_pct: Optional[int] = None,
        signal_confidence: Optional[float] = None,
        charging_state_hint: Optional[str] = None,
    ) -> dict:
        """
        Ingest a partner-submitted charging session.

        Idempotent: re-submitting the same partner_session_id returns the existing session.

        Supports status="candidate" for soft-signal partners — candidate sessions
        are NOT verified and do NOT trigger incentive evaluation.

        Returns dict with session_event_id, verified, quality_score, grant info.
        """
        source = f"partner_{partner.slug}"

        # --- Dedup check (idempotent) ---
        existing = db.query(SessionEvent).filter(
            SessionEvent.source == source,
            SessionEvent.source_session_id == partner_session_id,
        ).first()
        if existing:
            return PartnerSessionService._build_response(db, existing, is_new=False)

        # --- Driver resolution: shadow user ---
        driver = PartnerSessionService._resolve_driver(db, partner, partner_driver_id)

        # --- Charger resolution ---
        resolved_charger_id = charger_id
        if not resolved_charger_id and lat is not None and lng is not None:
            resolved_charger_id = PartnerSessionService._find_charger(db, lat, lng)

        # --- Compute duration ---
        duration_minutes = None
        if session_end and session_start:
            duration_minutes = int((session_end - session_start).total_seconds() / 60)

        # --- Candidate sessions are not verified ---
        is_candidate = (status == "candidate")
        verified = False if is_candidate else (partner.trust_tier <= 2)

        # --- Create SessionEvent ---
        session_event = SessionEvent(
            id=str(uuid.uuid4()),
            driver_user_id=driver.id,
            user_id=driver.id,
            charger_id=resolved_charger_id,
            charger_network=charger_network,
            connector_type=connector_type,
            power_kw=power_kw,
            session_start=session_start,
            session_end=session_end,
            duration_minutes=duration_minutes,
            kwh_delivered=kwh_delivered,
            source=source,
            source_session_id=partner_session_id,
            verified=verified,
            verification_method=partner.default_verification_method,
            lat=lat,
            lng=lng,
            battery_start_pct=battery_start_pct,
            battery_end_pct=battery_end_pct,
            vehicle_vin=vehicle_vin,
            vehicle_make=vehicle_make,
            vehicle_model=vehicle_model,
            vehicle_year=vehicle_year,
            partner_id=partner.id,
            partner_driver_id=partner_driver_id,
            partner_status=status,
            signal_confidence=signal_confidence,
        )

        # --- Quality score ---
        from app.services.session_event_service import SessionEventService
        quality = SessionEventService._compute_quality_score(session_event)
        quality = max(0, min(100, quality + partner.quality_score_modifier))
        session_event.quality_score = quality

        db.add(session_event)
        db.flush()

        # --- Incentive evaluation (only for completed sessions, NOT candidates) ---
        grant = None
        if status == "completed" and session_end:
            grant = IncentiveEngine.evaluate_session(db, session_event)
            # Route reward for partner shadow drivers
            if grant and driver.auth_provider == "partner":
                grant.reward_destination = "partner_managed"
                db.flush()

        db.commit()

        # --- Webhook delivery for grant events ---
        if grant and partner.webhook_enabled:
            PartnerSessionService._deliver_grant_webhook(partner, session_event, grant)

        return PartnerSessionService._build_response(db, session_event, is_new=True)

    @staticmethod
    def update_session(
        db: Session,
        partner: Partner,
        partner_session_id: str,
        status: Optional[str] = None,
        session_end: Optional[datetime] = None,
        kwh_delivered: Optional[float] = None,
        power_kw: Optional[float] = None,
        battery_end_pct: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Update telemetry or complete an in-progress partner session.

        Supports transitioning from candidate -> charging or candidate -> completed.
        When transitioning to completed, runs incentive evaluation.
        """
        source = f"partner_{partner.slug}"
        session_event = db.query(SessionEvent).filter(
            SessionEvent.source == source,
            SessionEvent.source_session_id == partner_session_id,
        ).first()
        if not session_event:
            return None

        if kwh_delivered is not None:
            session_event.kwh_delivered = kwh_delivered
        if power_kw is not None:
            session_event.power_kw = power_kw
        if battery_end_pct is not None:
            session_event.battery_end_pct = battery_end_pct

        # Handle status transitions
        if status:
            old_status = session_event.partner_status

            # Transition from candidate to charging: mark as verified
            if status == "charging" and old_status == "candidate":
                session_event.partner_status = "charging"
                session_event.verified = (partner.trust_tier <= 2)

            # Complete session
            if status == "completed" and not session_event.session_end:
                session_event.partner_status = "completed"
                session_event.verified = (partner.trust_tier <= 2)

                if session_end:
                    session_event.session_end = session_end
                    session_event.duration_minutes = int(
                        (session_end - session_event.session_start).total_seconds() / 60
                    )

                # Recompute quality
                from app.services.session_event_service import SessionEventService
                quality = SessionEventService._compute_quality_score(session_event)
                quality = max(0, min(100, quality + partner.quality_score_modifier))
                session_event.quality_score = quality

                # Evaluate incentive
                grant = IncentiveEngine.evaluate_session(db, session_event)
                if grant:
                    driver = db.query(User).filter(User.id == session_event.driver_user_id).first()
                    if driver and driver.auth_provider == "partner":
                        grant.reward_destination = "partner_managed"

        session_event.updated_at = datetime.utcnow()
        db.commit()

        # --- Webhook delivery for grant events on status transitions ---
        if status == "completed" and partner.webhook_enabled:
            existing_grant = db.query(IncentiveGrant).filter(
                IncentiveGrant.session_event_id == session_event.id
            ).first()
            if existing_grant:
                PartnerSessionService._deliver_grant_webhook(partner, session_event, existing_grant)

        return PartnerSessionService._build_response(db, session_event, is_new=False)

    @staticmethod
    def get_session(
        db: Session,
        partner: Partner,
        partner_session_id: str,
    ) -> Optional[dict]:
        source = f"partner_{partner.slug}"
        session_event = db.query(SessionEvent).filter(
            SessionEvent.source == source,
            SessionEvent.source_session_id == partner_session_id,
        ).first()
        if not session_event:
            return None
        return PartnerSessionService._build_response(db, session_event, is_new=False)

    @staticmethod
    def list_sessions(
        db: Session,
        partner: Partner,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        source = f"partner_{partner.slug}"
        sessions = db.query(SessionEvent).filter(
            SessionEvent.source == source,
        ).order_by(SessionEvent.created_at.desc()).offset(offset).limit(limit).all()
        return [PartnerSessionService._build_response(db, s, is_new=False) for s in sessions]

    @staticmethod
    def list_grants(
        db: Session,
        partner: Partner,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        source = f"partner_{partner.slug}"
        grants = db.query(IncentiveGrant).join(
            SessionEvent, IncentiveGrant.session_event_id == SessionEvent.id
        ).filter(
            SessionEvent.source == source,
        ).order_by(IncentiveGrant.created_at.desc()).offset(offset).limit(limit).all()
        results = []
        for g in grants:
            from app.models.campaign import Campaign
            campaign = db.query(Campaign).filter(Campaign.id == g.campaign_id).first()
            from app.core.config import settings
            platform_fee_bps = getattr(settings, 'PLATFORM_FEE_BPS', 2000)
            platform_fee_cents = (g.amount_cents * platform_fee_bps) // 10000
            results.append({
                "grant_id": g.id,
                "session_event_id": g.session_event_id,
                "campaign_id": g.campaign_id,
                "campaign_name": campaign.name if campaign else None,
                "amount_cents": g.amount_cents,
                "platform_fee_cents": platform_fee_cents,
                "net_reward_cents": g.amount_cents - platform_fee_cents,
                "reward_destination": g.reward_destination,
                "status": g.status,
                "granted_at": g.granted_at.isoformat() if g.granted_at else None,
            })
        return results

    # --- Private helpers ---

    @staticmethod
    def _resolve_driver(db: Session, partner: Partner, partner_driver_id: str) -> User:
        """Look up or create a shadow user for a partner's driver."""
        shadow_email = f"partner_{partner.slug}_{partner_driver_id}@partner.nerava.network"
        user = db.query(User).filter(
            User.email == shadow_email,
            User.auth_provider == "partner",
        ).first()
        if user:
            return user

        user = User(
            email=shadow_email,
            auth_provider="partner",
            role_flags="partner_driver",
            display_name=f"{partner.name} Driver {partner_driver_id[:8]}",
            is_active=True,
        )
        db.add(user)
        db.flush()
        return user

    @staticmethod
    def _deliver_grant_webhook(partner: Partner, session_event: SessionEvent, grant: IncentiveGrant) -> None:
        """Fire a grant.created webhook to the partner (best-effort, non-blocking of response)."""
        try:
            from app.services.partner_webhook_service import deliver_webhook

            payload = {
                "session_event_id": session_event.id,
                "partner_session_id": session_event.source_session_id,
                "grant_id": grant.id,
                "campaign_id": grant.campaign_id,
                "amount_cents": grant.amount_cents,
                "reward_destination": grant.reward_destination,
            }
            deliver_webhook(partner, "grant.created", payload)
        except Exception as e:
            logger.error(f"Failed to deliver grant webhook to partner {partner.slug}: {e}")

    @staticmethod
    def _find_charger(db: Session, lat: float, lng: float) -> Optional[str]:
        """Find nearest charger within 500m. Returns charger ID or None."""
        try:
            from app.services.intent_service import find_nearest_charger
            result = find_nearest_charger(db, lat, lng, radius_m=500)
            if result:
                charger, distance = result
                return charger.id
        except Exception as e:
            logger.warning(f"Charger lookup failed: {e}")
        return None

    @staticmethod
    def _build_response(db: Session, session_event: SessionEvent, is_new: bool) -> dict:
        """Build the API response dict for a session event."""
        grant_info = None
        grant = db.query(IncentiveGrant).filter(
            IncentiveGrant.session_event_id == session_event.id
        ).first()
        if grant:
            from app.core.config import settings
            from app.models.campaign import Campaign
            campaign = db.query(Campaign).filter(Campaign.id == grant.campaign_id).first()
            platform_fee_bps = getattr(settings, 'PLATFORM_FEE_BPS', 2000)
            platform_fee_cents = (grant.amount_cents * platform_fee_bps) // 10000
            grant_info = {
                "grant_id": grant.id,
                "campaign_id": grant.campaign_id,
                "campaign_name": campaign.name if campaign else None,
                "amount_cents": grant.amount_cents,
                "platform_fee_cents": platform_fee_cents,
                "net_reward_cents": grant.amount_cents - platform_fee_cents,
                "reward_destination": grant.reward_destination,
            }

        # Derive status: use partner_status if set, otherwise derive from session_end
        if session_event.partner_status:
            status = session_event.partner_status
        else:
            status = "completed" if session_event.session_end else "charging"

        response = {
            "session_event_id": session_event.id,
            "partner_session_id": session_event.source_session_id,
            "status": status,
            "verified": session_event.verified,
            "quality_score": session_event.quality_score,
            "session_start": session_event.session_start.isoformat() if session_event.session_start else None,
            "session_end": session_event.session_end.isoformat() if session_event.session_end else None,
            "duration_minutes": session_event.duration_minutes,
            "kwh_delivered": session_event.kwh_delivered,
            "charger_id": session_event.charger_id,
            "grant": grant_info,
            "_is_new": is_new,
        }

        # Include vehicle info if present
        if session_event.vehicle_vin:
            response["vehicle_vin"] = session_event.vehicle_vin
        if session_event.vehicle_make:
            response["vehicle_make"] = session_event.vehicle_make
        if session_event.vehicle_model:
            response["vehicle_model"] = session_event.vehicle_model
        if session_event.vehicle_year:
            response["vehicle_year"] = session_event.vehicle_year

        # Include signal confidence if present
        if session_event.signal_confidence is not None:
            response["signal_confidence"] = session_event.signal_confidence

        return response
