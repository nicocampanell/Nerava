"""
EV Arrival Code Checkin Service.

Core business logic for:
- V0 arrival code flow (QR pairing)
- Phone-first flow (SMS session links)
- Verification (browser geofence, phone geofence, QR scan)
- Code generation and SMS
- Redemption and merchant confirmation
"""
import hashlib
import logging
import math
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from app.models.arrival_session import ACTIVE_STATUSES, ArrivalSession
from app.models.billing_event import BillingEvent
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

# Constants
CODE_TTL_MINUTES = 30
PAIRING_TTL_MINUTES = 5
SESSION_TTL_MINUTES = 90  # Phone-first session TTL (shorter than 2hr QR flow)
SESSION_TTL_HOURS = 2
CHARGER_RADIUS_M = 250
MAX_VERIFICATION_ATTEMPTS = 10

# Code alphabet (no confusing chars: 0, O, I, L, 1)
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

# Session code length (phone-first flow)
SESSION_CODE_LENGTH = 6


class CheckinService:
    """Service for EV Arrival Code operations."""

    def generate_arrival_code(self, db: Session) -> str:
        """
        Generate a unique arrival code.
        Format: NVR-XXXX where X is from CODE_ALPHABET.
        """
        for _ in range(10):  # Max 10 attempts to find unique code
            suffix = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(4))
            code = f"NVR-{suffix}"

            # Check uniqueness
            existing = db.query(ArrivalSession).filter(
                ArrivalSession.arrival_code == code
            ).first()

            if not existing:
                return code

        # Fallback to longer code if collisions persist
        suffix = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        return f"NVR-{suffix}"

    def generate_pairing_token(self) -> str:
        """Generate a secure pairing token."""
        return secrets.token_urlsafe(32)

    def mask_phone(self, phone: str) -> str:
        """Mask phone number for display: (512) ***-1234"""
        if not phone or len(phone) < 4:
            return "***"
        # Get all digits
        digits = ''.join(c for c in phone if c.isdigit())
        # Strip US country code (1) if present
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if len(digits) >= 10:
            return f"({digits[:3]}) ***-{digits[-4:]}"
        return f"***-{digits[-4:]}" if len(digits) >= 4 else "***"

    async def start_checkin(
        self,
        db: Session,
        lat: float,
        lng: float,
        accuracy_m: Optional[float],
        user: Optional[User],
        charger_id: Optional[str],
        ev_browser_info: Dict[str, Any],
        idempotency_key: Optional[str],
    ) -> Tuple[ArrivalSession, bool, Optional[str], List[Dict]]:
        """
        Start a checkin session.

        Returns: (session, pairing_required, pairing_url, nearby_merchants)
        """
        # Idempotency check
        if idempotency_key:
            existing = db.query(ArrivalSession).filter(
                ArrivalSession.idempotency_key == idempotency_key
            ).first()
            if existing:
                nearby = self._get_nearby_merchants(db, existing.charger_id, lat, lng)
                pairing_required = existing.driver_id is None and existing.paired_at is None
                pairing_url = None
                if pairing_required and existing.pairing_token:
                    pairing_url = f"{settings.PUBLIC_BASE_URL}/pair?token={existing.pairing_token}"
                return existing, pairing_required, pairing_url, nearby

        # Find nearest charger if not provided
        charger = None
        if charger_id:
            charger = db.query(Charger).filter(Charger.id == charger_id).first()
        else:
            charger = self._find_nearest_charger(db, lat, lng)

        # Check if user has active session
        if user:
            active = db.query(ArrivalSession).filter(
                ArrivalSession.driver_id == user.id,
                ArrivalSession.status.in_(ACTIVE_STATUSES),
            ).first()
            if active and active.flow_type == 'arrival_code':
                nearby = self._get_nearby_merchants(db, active.charger_id, lat, lng)
                return active, False, None, nearby

        now = datetime.utcnow()
        pairing_required = user is None
        pairing_token = None
        pairing_url = None

        if pairing_required:
            pairing_token = self.generate_pairing_token()
            pairing_url = f"{settings.PUBLIC_BASE_URL}/pair?token={pairing_token}"

        # Create session
        session = ArrivalSession(
            id=uuid.uuid4(),
            driver_id=user.id if user else None,
            merchant_id=charger.primary_merchant_id if charger and hasattr(charger, 'primary_merchant_id') else None,
            charger_id=charger.id if charger else None,
            flow_type='arrival_code',
            arrival_type='ev_curbside',  # Default for V0
            status='pending_pairing' if pairing_required else 'pending_verification',
            browser_source=ev_browser_info.get('browser_source', 'web'),
            ev_brand=ev_browser_info.get('brand'),
            ev_firmware=ev_browser_info.get('firmware_version'),
            pairing_token=pairing_token,
            pairing_token_expires_at=now + timedelta(minutes=PAIRING_TTL_MINUTES) if pairing_token else None,
            created_at=now,
            expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
            idempotency_key=idempotency_key,
        )

        db.add(session)
        db.commit()
        db.refresh(session)

        nearby = self._get_nearby_merchants(db, charger.id if charger else None, lat, lng)

        logger.info(f"Created checkin session {session.id} (pairing_required={pairing_required})")

        return session, pairing_required, pairing_url, nearby

    def _find_nearest_charger(self, db: Session, lat: float, lng: float) -> Optional[Charger]:
        """Find the nearest charger within radius using SQL spatial query.

        Uses a bounding-box pre-filter followed by the Haversine formula in SQL
        so that only a handful of rows are examined instead of the full table.
        """
        radius_m = CHARGER_RADIUS_M

        # Bounding box pre-filter (approx 1 degree latitude = 111 km)
        lat_delta = radius_m / 111000
        lng_delta = radius_m / (111000 * math.cos(math.radians(lat)))

        # SQL Haversine distance expression
        distance_expr = (
            6371000 * func.acos(
                func.cos(func.radians(lat)) * func.cos(func.radians(Charger.lat)) *
                func.cos(func.radians(Charger.lng) - func.radians(lng)) +
                func.sin(func.radians(lat)) * func.sin(func.radians(Charger.lat))
            )
        ).label("distance_m")

        result = db.query(Charger, distance_expr).filter(
            Charger.lat.isnot(None),
            Charger.lng.isnot(None),
            Charger.lat.between(lat - lat_delta, lat + lat_delta),
            Charger.lng.between(lng - lng_delta, lng + lng_delta),
        ).order_by("distance_m").limit(1).first()

        if result:
            charger, distance_m = result
            if distance_m <= radius_m:
                return charger

        return None

    def _get_nearby_merchants(
        self,
        db: Session,
        charger_id: Optional[str],
        lat: float,
        lng: float,
    ) -> List[Dict]:
        """Get nearby merchants for display.

        Uses a single JOIN query (when charger_id is provided) or a SQL spatial
        query (fallback) to avoid N+1 individual merchant lookups.
        """
        merchants = []

        if charger_id:
            # Single JOIN query: ChargerMerchant + Merchant in one round-trip
            rows = (
                db.query(ChargerMerchant, Merchant)
                .join(Merchant, ChargerMerchant.merchant_id == Merchant.id)
                .filter(ChargerMerchant.charger_id == charger_id)
                .limit(5)
                .all()
            )

            for assoc, merchant in rows:
                distance = haversine_m(lat, lng, merchant.lat, merchant.lng) if merchant.lat else None
                walk_time = max(1, int(distance / 80)) if distance else None  # ~80m/min
                merchants.append({
                    "id": merchant.id,
                    "name": merchant.name,
                    "category": getattr(merchant, 'category', None),
                    "distance_m": int(distance) if distance else None,
                    "walk_time_minutes": walk_time,
                    "ordering_url": getattr(merchant, 'ordering_url', None),
                    "image_url": getattr(merchant, 'image_url', None),
                })
        else:
            # SQL spatial query: bounding box + Haversine instead of loading all merchants
            radius_m = 1000  # 1km
            lat_delta = radius_m / 111000
            lng_delta = radius_m / (111000 * math.cos(math.radians(lat)))

            distance_expr = (
                6371000 * func.acos(
                    func.cos(func.radians(lat)) * func.cos(func.radians(Merchant.lat)) *
                    func.cos(func.radians(Merchant.lng) - func.radians(lng)) +
                    func.sin(func.radians(lat)) * func.sin(func.radians(Merchant.lat))
                )
            ).label("distance_m")

            rows = (
                db.query(Merchant, distance_expr)
                .filter(
                    Merchant.lat.isnot(None),
                    Merchant.lng.isnot(None),
                    Merchant.lat.between(lat - lat_delta, lat + lat_delta),
                    Merchant.lng.between(lng - lng_delta, lng + lng_delta),
                )
                .order_by("distance_m")
                .limit(5)
                .all()
            )

            for merchant, distance in rows:
                if distance <= radius_m:
                    walk_time = max(1, int(distance / 80))
                    merchants.append({
                        "id": merchant.id,
                        "name": merchant.name,
                        "category": getattr(merchant, 'category', None),
                        "distance_m": int(distance),
                        "walk_time_minutes": walk_time,
                        "ordering_url": getattr(merchant, 'ordering_url', None),
                        "image_url": getattr(merchant, 'image_url', None),
                    })

        return merchants

    async def complete_pairing(
        self,
        db: Session,
        pairing_token: str,
        user: User,
    ) -> Optional[ArrivalSession]:
        """
        Complete pairing after OTP verification.
        Links user to session.
        """
        session = db.query(ArrivalSession).filter(
            ArrivalSession.pairing_token == pairing_token,
        ).first()

        if not session:
            logger.warning(f"Pairing token not found: {pairing_token[:8]}...")
            return None

        # Check expiry
        if session.pairing_token_expires_at and session.pairing_token_expires_at < datetime.utcnow():
            logger.warning(f"Pairing token expired for session {session.id}")
            return None

        # Link user to session
        session.driver_id = user.id
        session.paired_at = datetime.utcnow()
        session.paired_phone = self.mask_phone(user.phone)
        session.status = 'pending_verification'

        db.commit()
        db.refresh(session)

        logger.info(f"Completed pairing for session {session.id} with user {user.id}")

        return session

    async def verify_checkin(
        self,
        db: Session,
        session: ArrivalSession,
        method: str,
        lat: Optional[float],
        lng: Optional[float],
        qr_payload: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify arrival using one of three methods.

        Returns: (verified, error_message)
        """
        # Check session state
        if session.status not in ('pending_verification', 'pending_pairing'):
            if session.verified_at:
                return True, None  # Already verified
            return False, "Session not in verifiable state"

        # Rate limit
        session.verification_attempts += 1
        if session.verification_attempts > MAX_VERIFICATION_ATTEMPTS:
            db.commit()
            return False, "Too many verification attempts"

        verified = False
        error = None

        if method == 'browser_geofence':
            verified, error = self._verify_browser_geofence(db, session, lat, lng)
        elif method == 'phone_geofence':
            verified, error = self._verify_phone_geofence(db, session, lat, lng)
        elif method == 'qr_scan':
            verified, error = self._verify_qr_scan(db, session, qr_payload)
        else:
            error = f"Unknown verification method: {method}"

        if verified:
            session.verification_method = method
            session.verified_at = datetime.utcnow()
            session.status = 'verified'
            session.arrival_lat = lat
            session.arrival_lng = lng
            logger.info(f"Session {session.id} verified via {method}")

        db.commit()

        return verified, error

    def _verify_browser_geofence(
        self,
        db: Session,
        session: ArrivalSession,
        lat: Optional[float],
        lng: Optional[float],
    ) -> Tuple[bool, Optional[str]]:
        """Verify browser location is within charger radius."""
        if lat is None or lng is None:
            return False, "Location required for browser geofence"

        if not session.charger_id:
            return False, "No charger associated with session"

        charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
        if not charger:
            return False, "Charger not found"

        distance = haversine_m(lat, lng, charger.lat, charger.lng)

        if distance > CHARGER_RADIUS_M:
            return False, f"Too far from charger ({int(distance)}m, max {CHARGER_RADIUS_M}m)"

        return True, None

    def _verify_phone_geofence(
        self,
        db: Session,
        session: ArrivalSession,
        lat: Optional[float],
        lng: Optional[float],
    ) -> Tuple[bool, Optional[str]]:
        """Verify phone location is within charger radius."""
        # Same logic as browser geofence for V0
        return self._verify_browser_geofence(db, session, lat, lng)

    def _verify_qr_scan(
        self,
        db: Session,
        session: ArrivalSession,
        qr_payload: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """Verify QR code scan (charger_id encoded in QR)."""
        if not qr_payload:
            return False, "QR payload required"

        # QR payload format: charger_id:nonce or just charger_id
        parts = qr_payload.split(':')
        qr_charger_id = parts[0]

        if session.charger_id and qr_charger_id != session.charger_id:
            return False, "QR code does not match expected charger"

        # Verify charger exists
        charger = db.query(Charger).filter(Charger.id == qr_charger_id).first()
        if not charger:
            return False, "Invalid charger in QR code"

        # Update session with charger if not set
        if not session.charger_id:
            session.charger_id = qr_charger_id

        return True, None

    async def generate_code(
        self,
        db: Session,
        session: ArrivalSession,
        merchant_id: Optional[str],
    ) -> Tuple[ArrivalSession, List[Dict]]:
        """
        Generate arrival code after verification.

        Returns: (updated_session, nearby_merchants)
        """
        # Check if code already generated (idempotent)
        if session.arrival_code:
            nearby = self._get_nearby_merchants(
                db, session.charger_id,
                session.arrival_lat or 0, session.arrival_lng or 0
            )
            return session, nearby

        # Validate session state
        if session.status != 'verified':
            raise ValueError(f"Session must be verified to generate code (current: {session.status})")

        now = datetime.utcnow()

        # Generate unique code
        code = self.generate_arrival_code(db)

        # Update merchant if selected
        if merchant_id:
            merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
            if merchant:
                session.merchant_id = merchant_id
                session.checkout_url_sent = getattr(merchant, 'ordering_url', None)

        session.arrival_code = code
        session.arrival_code_generated_at = now
        session.arrival_code_expires_at = now + timedelta(minutes=CODE_TTL_MINUTES)
        session.status = 'code_generated'

        db.commit()
        db.refresh(session)

        nearby = self._get_nearby_merchants(
            db, session.charger_id,
            session.arrival_lat or 0, session.arrival_lng or 0
        )

        logger.info(f"Generated code {code} for session {session.id}")

        return session, nearby

    async def send_code_sms(
        self,
        db: Session,
        session: ArrivalSession,
        phone: str,
    ) -> bool:
        """
        Send SMS with arrival code.

        Returns: True if sent successfully.
        """
        if not session.arrival_code:
            logger.error(f"Cannot send SMS: no code for session {session.id}")
            return False

        # Build message
        checkout_url = session.checkout_url_sent or f"{settings.PUBLIC_BASE_URL}/order"

        message = (
            f"Nerava: Your EV Arrival Code is {session.arrival_code}\n\n"
            f"Order here: {checkout_url}\n"
            f"Enter code at checkout for priority service.\n\n"
            f"Valid for {CODE_TTL_MINUTES} min."
        )

        # Send via Twilio (mock for now if no credentials)
        try:
            from app.services.auth.twilio_verify import get_twilio_client

            client = get_twilio_client()
            if client:
                msg = client.messages.create(
                    body=message,
                    from_=settings.TWILIO_PHONE_NUMBER if hasattr(settings, 'TWILIO_PHONE_NUMBER') else None,
                    to=phone,
                )
                session.sms_message_sid = msg.sid
            else:
                logger.warning(f"Twilio not configured, skipping SMS for session {session.id}")
                session.sms_message_sid = "mock_" + secrets.token_hex(8)

            session.sms_sent_at = datetime.utcnow()
            db.commit()

            logger.info(f"Sent SMS with code {session.arrival_code} to {self.mask_phone(phone)}")
            return True

        except Exception as e:
            logger.error(f"Failed to send SMS for session {session.id}: {e}")
            return False

    async def redeem_code(
        self,
        db: Session,
        code: str,
        order_number: Optional[str] = None,
        order_total_cents: Optional[int] = None,
    ) -> Tuple[Optional[ArrivalSession], bool, Optional[str]]:
        """
        Mark code as redeemed.

        Returns: (session, already_redeemed, error)
        """
        # Find session by code with row lock
        session = db.query(ArrivalSession).filter(
            ArrivalSession.arrival_code == code,
        ).with_for_update().first()

        if not session:
            return None, False, "Code not found"

        # Check expiry
        if session.arrival_code_expires_at and session.arrival_code_expires_at < datetime.utcnow():
            return session, False, "Code has expired"

        # Check if already redeemed
        if session.arrival_code_redeemed_at:
            return session, True, None

        # Mark redeemed
        session.arrival_code_redeemed_at = datetime.utcnow()
        session.arrival_code_redemption_count += 1
        session.status = 'code_redeemed'

        if order_number:
            session.order_number = order_number
        if order_total_cents:
            session.order_total_cents = order_total_cents
            session.total_source = 'driver_reported'

        db.commit()
        db.refresh(session)

        logger.info(f"Redeemed code {code} for session {session.id}")

        return session, False, None

    async def merchant_confirm(
        self,
        db: Session,
        code: Optional[str] = None,
        session_id: Optional[str] = None,
        order_total_cents: Optional[int] = None,
    ) -> Tuple[Optional[ArrivalSession], Optional[BillingEvent], Optional[str]]:
        """
        Merchant confirms fulfillment.

        Returns: (session, billing_event, error)
        """
        session = None

        if code:
            # Look up by code (last 4 chars or full code)
            if len(code) == 4:
                session = db.query(ArrivalSession).filter(
                    ArrivalSession.arrival_code.like(f"%-{code}"),
                ).first()
            else:
                session = db.query(ArrivalSession).filter(
                    ArrivalSession.arrival_code == code,
                ).first()
        elif session_id:
            session = db.query(ArrivalSession).filter(
                ArrivalSession.id == session_id,
            ).first()

        if not session:
            return None, None, "Session not found"

        # Check if already confirmed (idempotent)
        if session.status in ('merchant_confirmed', 'completed'):
            existing_billing = db.query(BillingEvent).filter(
                BillingEvent.arrival_session_id == session.id
            ).first()
            return session, existing_billing, None

        now = datetime.utcnow()
        session.merchant_confirmed_at = now
        session.status = 'merchant_confirmed'

        # Update order total if provided
        if order_total_cents and order_total_cents > 0:
            session.order_total_cents = order_total_cents
            session.total_source = 'merchant_reported'

        billing_event = None

        # Create billing event if we have a total
        total = order_total_cents or session.order_total_cents
        if total and total > 0:
            fee_bps = session.platform_fee_bps or settings.PLATFORM_FEE_BPS
            billable_cents = (total * fee_bps) // 10000

            # Apply min/max
            billable_cents = max(50, min(500, billable_cents))  # $0.50 - $5.00

            billing_event = BillingEvent(
                arrival_session_id=session.id,
                merchant_id=session.merchant_id,
                order_total_cents=total,
                fee_bps=fee_bps,
                billable_cents=billable_cents,
                total_source=session.total_source or 'merchant_reported',
            )
            db.add(billing_event)

            session.billable_amount_cents = billable_cents
            session.billing_status = 'pending'

        # Auto-grant Nova to driver on merchant confirmation
        if session.driver_id and billing_event:
            try:
                from app.services.nova_service import NovaService
                NovaService.grant_to_driver(
                    db,
                    driver_id=session.driver_id,
                    amount=billable_cents,
                    session_id=str(session.id),
                    idempotency_key=f"merchant_confirm_{session.id}",
                    metadata={"source": "merchant_confirm", "order_total_cents": total},
                    auto_commit=False,
                )
            except Exception as e:
                logger.error(f"Failed to auto-grant Nova for session {session.id}: {e}")

        session.completed_at = now

        db.commit()
        if billing_event:
            db.refresh(billing_event)

        logger.info(f"Merchant confirmed session {session.id}, billing: ${billable_cents/100 if billing_event else 0:.2f}")

        return session, billing_event, None

    def get_session_by_id(self, db: Session, session_id: str) -> Optional[ArrivalSession]:
        """Get session by ID."""
        return db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()

    def get_session_by_pairing_token(self, db: Session, token: str) -> Optional[ArrivalSession]:
        """Get session by pairing token."""
        return db.query(ArrivalSession).filter(ArrivalSession.pairing_token == token).first()

    def get_session_by_code(self, db: Session, code: str) -> Optional[ArrivalSession]:
        """Get session by arrival code."""
        return db.query(ArrivalSession).filter(ArrivalSession.arrival_code == code).first()

    # ─── Phone-First Flow Methods ──────────────────────────────────────────

    def generate_session_code(self, db: Session) -> str:
        """
        Generate a unique session code for phone-first flow.
        Format: 6 alphanumeric chars (no prefix, e.g., ABC123)
        """
        for _ in range(10):  # Max 10 attempts
            code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(SESSION_CODE_LENGTH))

            # Check uniqueness (in active sessions)
            existing = db.query(ArrivalSession).filter(
                ArrivalSession.arrival_code == code,
                ArrivalSession.status.in_(ACTIVE_STATUSES),
            ).first()

            if not existing:
                return code

        # Fallback to longer code
        return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))

    def hash_phone(self, phone: str) -> str:
        """Hash phone number for storage."""
        return hashlib.sha256(phone.encode()).hexdigest()

    async def phone_start_checkin(
        self,
        db: Session,
        phone: str,
        charger_hint: Optional[str],
        ev_browser_info: Dict[str, Any],
        client_ip: str,
    ) -> Tuple[Optional[ArrivalSession], Optional[str], Optional[str]]:
        """
        Start a phone-first checkin session.

        Args:
            db: Database session
            phone: E.164 formatted phone number
            charger_hint: Optional charger ID hint
            ev_browser_info: EV browser detection info
            client_ip: Client IP for rate limiting

        Returns:
            (session, session_token, error)
        """
        from app.utils.rate_limit import get_rate_limiter
        from app.utils.session_token import generate_session_token, hash_phone

        rate_limiter = get_rate_limiter()

        # Check rate limits
        phone_allowed, _ = rate_limiter.check_phone_limit(phone)
        if not phone_allowed:
            logger.warning(f"Phone rate limit exceeded for {self.mask_phone(phone)}")
            return None, None, "rate_limit_phone"

        ip_allowed, _ = rate_limiter.check_ip_limit(client_ip)
        if not ip_allowed:
            logger.warning(f"IP rate limit exceeded for {client_ip}")
            return None, None, "rate_limit_ip"

        # Check for existing active session for this phone
        phone_hash = hash_phone(phone)
        existing = db.query(ArrivalSession).filter(
            ArrivalSession.paired_phone == self.mask_phone(phone),
            ArrivalSession.flow_type == 'phone_first',
            ArrivalSession.status.in_(ACTIVE_STATUSES),
            ArrivalSession.expires_at > datetime.utcnow(),
        ).first()

        if existing:
            # Return existing session
            token = generate_session_token(str(existing.id), phone_hash)
            return existing, token, None

        now = datetime.utcnow()

        # Find charger if hint provided
        charger = None
        if charger_hint:
            charger = db.query(Charger).filter(Charger.id == charger_hint).first()

        # Generate session code
        session_code = self.generate_session_code(db)

        # Create session
        session = ArrivalSession(
            id=uuid.uuid4(),
            driver_id=None,  # Will be linked after OTP
            merchant_id=charger.primary_merchant_id if charger and hasattr(charger, 'primary_merchant_id') else None,
            charger_id=charger.id if charger else None,
            flow_type='phone_first',
            arrival_type='ev_curbside',
            status='pending_pairing',  # Waiting for phone OTP
            browser_source=ev_browser_info.get('browser_source', 'tesla_browser'),
            ev_brand=ev_browser_info.get('brand'),
            ev_firmware=ev_browser_info.get('firmware_version'),
            arrival_code=session_code,  # Use arrival_code field for session code
            arrival_code_generated_at=now,
            arrival_code_expires_at=now + timedelta(minutes=SESSION_TTL_MINUTES),
            paired_phone=self.mask_phone(phone),  # Store masked for display
            created_at=now,
            expires_at=now + timedelta(minutes=SESSION_TTL_MINUTES),
        )

        db.add(session)
        db.commit()
        db.refresh(session)

        # Generate signed token for SMS link
        token = generate_session_token(str(session.id), phone_hash)

        logger.info(f"Created phone-first session {session.id} for {self.mask_phone(phone)}")

        return session, token, None

    async def send_session_sms(
        self,
        db: Session,
        session: ArrivalSession,
        phone: str,
        token: str,
    ) -> bool:
        """
        Send SMS with session link and code for phone-first flow.

        Args:
            db: Database session
            session: Arrival session
            phone: Phone number to send to
            token: Signed session token

        Returns:
            True if sent successfully
        """
        if not session.arrival_code:
            logger.error(f"Cannot send SMS: no code for session {session.id}")
            return False

        # Build session link
        base_url = getattr(settings, 'PUBLIC_BASE_URL', 'https://app.nerava.network')
        session_link = f"{base_url}/s/{token}"

        # Get charger name for context
        charger_name = None
        if session.charger_id:
            charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
            if charger:
                charger_name = charger.name

        # Build message - include code as backup if link doesn't work
        ttl_minutes = SESSION_TTL_MINUTES
        if charger_name:
            message = (
                f"Nerava Check-In: Open {session_link}\n"
                f"Backup code: {session.arrival_code} (valid {ttl_minutes} min).\n"
                f"Near: {charger_name}"
            )
        else:
            message = (
                f"Nerava Check-In: Open {session_link}\n"
                f"Backup code: {session.arrival_code} (valid {ttl_minutes} min)."
            )

        # Send via Twilio
        try:
            from app.services.auth.twilio_verify import get_twilio_client

            client = get_twilio_client()
            if client:
                twilio_phone = getattr(settings, 'TWILIO_PHONE_NUMBER', None)
                msg = client.messages.create(
                    body=message,
                    from_=twilio_phone,
                    to=phone,
                )
                session.sms_message_sid = msg.sid
            else:
                logger.warning(f"Twilio not configured, mocking SMS for session {session.id}")
                session.sms_message_sid = "mock_" + secrets.token_hex(8)

            session.sms_sent_at = datetime.utcnow()
            session.checkout_url_sent = session_link
            db.commit()

            logger.info(f"Sent session SMS to {self.mask_phone(phone)} for session {session.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to send session SMS: {e}")
            return False

    def get_session_by_token(
        self,
        db: Session,
        token: str,
    ) -> Tuple[Optional[ArrivalSession], Optional[Dict]]:
        """
        Get session by verifying HMAC-signed token.

        Args:
            db: Database session
            token: Signed session token

        Returns:
            (session, token_payload) or (None, None) if invalid
        """
        from app.utils.session_token import verify_session_token

        payload = verify_session_token(token)
        if not payload:
            return None, None

        session_id = payload.get('session_id')
        if not session_id:
            return None, None

        session = db.query(ArrivalSession).filter(
            ArrivalSession.id == session_id
        ).first()

        if not session:
            return None, None

        # Check expiry
        if session.expires_at and session.expires_at < datetime.utcnow():
            return None, None

        return session, payload

    async def activate_session(
        self,
        db: Session,
        session: ArrivalSession,
        user: User,
        phone_hash: str,
    ) -> bool:
        """
        Activate a phone-first session after OTP verification.

        Links user to session and updates status.

        Args:
            db: Database session
            session: Arrival session to activate
            user: Authenticated user
            phone_hash: Hash of phone used in token

        Returns:
            True if activated successfully
        """
        # Verify phone hash matches (prevents token reuse with different phone)
        from app.utils.session_token import hash_phone
        expected_hash = hash_phone(user.phone)[:16]

        if phone_hash != expected_hash:
            logger.warning(f"Phone hash mismatch for session {session.id}")
            return False

        # Check if already activated
        if session.driver_id and session.driver_id != user.id:
            logger.warning(f"Session {session.id} already activated by different user")
            return False

        # Activate
        session.driver_id = user.id
        session.paired_at = datetime.utcnow()
        session.status = 'pending_verification'

        db.commit()
        db.refresh(session)

        logger.info(f"Activated session {session.id} for user {user.id}")
        return True

    def get_session_status_response(
        self,
        db: Session,
        session: ArrivalSession,
        include_sensitive: bool = False,
    ) -> Dict[str, Any]:
        """
        Build session status response for API.

        Args:
            db: Database session
            session: Arrival session
            include_sensitive: Include sensitive data (for authenticated users)

        Returns:
            Status response dict
        """
        # Get charger name
        charger_name = None
        if session.charger_id:
            charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
            if charger:
                charger_name = charger.name

        # Get merchant name
        merchant_name = None
        if session.merchant_id:
            merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
            if merchant:
                merchant_name = merchant.name

        # Calculate TTL
        ttl_seconds = 0
        if session.expires_at:
            ttl_seconds = max(0, int((session.expires_at - datetime.utcnow()).total_seconds()))

        response = {
            "session_id": str(session.id),
            "session_code": session.arrival_code,
            "status": session.status,
            "flow_type": session.flow_type,
            "verified": session.verified_at is not None,
            "redeemed": session.arrival_code_redeemed_at is not None,
            "charger_name": charger_name,
            "merchant_name": merchant_name,
            "expires_in_seconds": ttl_seconds,
            "expires_at": session.expires_at.isoformat() + "Z" if session.expires_at else None,
        }

        if include_sensitive:
            response["charger_id"] = session.charger_id
            response["merchant_id"] = session.merchant_id
            response["verified_at"] = session.verified_at.isoformat() + "Z" if session.verified_at else None
            response["verification_method"] = session.verification_method

        return response


# Singleton instance
_checkin_service: Optional[CheckinService] = None


def get_checkin_service() -> CheckinService:
    """Get singleton checkin service."""
    global _checkin_service
    if _checkin_service is None:
        _checkin_service = CheckinService()
    return _checkin_service
