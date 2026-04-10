"""
Merchant Reward Service

Handles:
- Request-to-Join (demand capture for non-partner merchants)
- Reward Claims (claim → purchase → receipt → payout)
- Receipt OCR verification (Taggun API)
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.merchant_reward import (
    MerchantJoinRequest,
    ReceiptStatus,
    ReceiptSubmission,
    RewardClaim,
    RewardClaimStatus,
)

logger = logging.getLogger(__name__)

CLAIM_WINDOW_MINUTES = 120  # 2 hours to visit and upload receipt


# ---------------------------------------------------------------------------
# 1. Request-to-Join
# ---------------------------------------------------------------------------

def create_join_request(
    db: Session,
    driver_user_id: int,
    place_id: str,
    merchant_name: str,
    merchant_id: Optional[str] = None,
    charger_id: Optional[str] = None,
    interest_tags: Optional[list] = None,
    note: Optional[str] = None,
) -> MerchantJoinRequest:
    """Create or return existing join request (idempotent per driver+place_id)."""
    existing = db.query(MerchantJoinRequest).filter(
        MerchantJoinRequest.driver_user_id == driver_user_id,
        MerchantJoinRequest.place_id == place_id,
    ).first()

    if existing:
        # Update tags if provided
        if interest_tags:
            existing.interest_tags = interest_tags
        return existing

    request = MerchantJoinRequest(
        driver_user_id=driver_user_id,
        place_id=place_id,
        merchant_name=merchant_name,
        merchant_id=merchant_id,
        charger_id=charger_id,
        interest_tags=interest_tags,
        note=note,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get_join_request_count(db: Session, place_id: str) -> int:
    """Get total number of join requests for a merchant."""
    return db.query(MerchantJoinRequest).filter(
        MerchantJoinRequest.place_id == place_id,
    ).count()


def user_has_requested(db: Session, driver_user_id: int, place_id: str) -> bool:
    """Check if a specific driver has requested this merchant."""
    return db.query(MerchantJoinRequest).filter(
        MerchantJoinRequest.driver_user_id == driver_user_id,
        MerchantJoinRequest.place_id == place_id,
    ).first() is not None


def get_top_requested_merchants(db: Session, limit: int = 50) -> list:
    """Admin: get merchants sorted by demand count."""
    results = (
        db.query(
            MerchantJoinRequest.place_id,
            MerchantJoinRequest.merchant_name,
            func.count(MerchantJoinRequest.id).label("request_count"),
        )
        .group_by(MerchantJoinRequest.place_id, MerchantJoinRequest.merchant_name)
        .order_by(func.count(MerchantJoinRequest.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"place_id": r[0], "merchant_name": r[1], "request_count": r[2]}
        for r in results
    ]


# ---------------------------------------------------------------------------
# 2. Reward Claims
# ---------------------------------------------------------------------------

def create_reward_claim(
    db: Session,
    driver_user_id: int,
    merchant_name: str,
    merchant_id: Optional[str] = None,
    place_id: Optional[str] = None,
    reward_description: Optional[str] = None,
    charger_id: Optional[str] = None,
    session_event_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    reward_cents: Optional[int] = None,
) -> RewardClaim:
    """Create a reward claim. Driver intends to visit this merchant."""
    # Check for existing active claim at this merchant
    existing = db.query(RewardClaim).filter(
        RewardClaim.driver_user_id == driver_user_id,
        RewardClaim.place_id == place_id,
        RewardClaim.status == RewardClaimStatus.CLAIMED,
        RewardClaim.expires_at > datetime.utcnow(),
    ).first()

    if existing:
        return existing  # Idempotent — return existing claim

    claim = RewardClaim(
        driver_user_id=driver_user_id,
        merchant_name=merchant_name,
        merchant_id=merchant_id,
        place_id=place_id,
        reward_description=reward_description,
        charger_id=charger_id,
        session_event_id=session_event_id,
        campaign_id=campaign_id,
        reward_cents=reward_cents,
        expires_at=datetime.utcnow() + timedelta(minutes=CLAIM_WINDOW_MINUTES),
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def get_active_claims(db: Session, driver_user_id: int) -> List[RewardClaim]:
    """Get all active (non-expired, non-completed) claims for a driver."""
    return db.query(RewardClaim).filter(
        RewardClaim.driver_user_id == driver_user_id,
        RewardClaim.status.in_([
            RewardClaimStatus.CLAIMED,
            RewardClaimStatus.RECEIPT_UPLOADED,
        ]),
        RewardClaim.expires_at > datetime.utcnow(),
    ).all()


def get_claim_by_id(db: Session, claim_id: str, driver_user_id: int) -> Optional[RewardClaim]:
    """Get a claim by ID, ensuring it belongs to the requesting driver."""
    return db.query(RewardClaim).filter(
        RewardClaim.id == claim_id,
        RewardClaim.driver_user_id == driver_user_id,
    ).first()


def expire_stale_claims(db: Session) -> int:
    """Expire claims that have passed their expiry time."""
    count = db.query(RewardClaim).filter(
        RewardClaim.status == RewardClaimStatus.CLAIMED,
        RewardClaim.expires_at <= datetime.utcnow(),
    ).update({"status": RewardClaimStatus.EXPIRED.value})
    db.commit()
    return count


# ---------------------------------------------------------------------------
# 3. Receipt Submissions
# ---------------------------------------------------------------------------

def create_receipt_submission(
    db: Session,
    driver_user_id: int,
    reward_claim_id: str,
    image_url: str,
    image_key: Optional[str] = None,
) -> ReceiptSubmission:
    """Create a receipt submission for a claimed reward."""
    claim = db.query(RewardClaim).filter(
        RewardClaim.id == reward_claim_id,
        RewardClaim.driver_user_id == driver_user_id,
    ).first()

    if not claim:
        raise ValueError("Reward claim not found")
    if claim.status not in (RewardClaimStatus.CLAIMED, RewardClaimStatus.RECEIPT_UPLOADED):
        raise ValueError(f"Cannot upload receipt for claim in status: {claim.status}")
    if claim.expires_at < datetime.utcnow():
        claim.status = RewardClaimStatus.EXPIRED
        db.commit()
        raise ValueError("Reward claim has expired")

    submission = ReceiptSubmission(
        driver_user_id=driver_user_id,
        reward_claim_id=reward_claim_id,
        campaign_id=claim.campaign_id,
        merchant_id=claim.merchant_id,
        place_id=claim.place_id,
        image_url=image_url,
        image_key=image_key,
    )
    db.add(submission)

    # Update claim status
    claim.status = RewardClaimStatus.RECEIPT_UPLOADED
    claim.receipt_submission_id = submission.id
    db.commit()
    db.refresh(submission)
    return submission


async def process_receipt_ocr(
    db: Session,
    submission_id: str,
) -> ReceiptSubmission:
    """Process receipt via Taggun OCR API."""
    submission = db.query(ReceiptSubmission).filter(
        ReceiptSubmission.id == submission_id,
    ).first()
    if not submission:
        raise ValueError("Receipt submission not found")

    submission.status = ReceiptStatus.PROCESSING
    db.commit()

    taggun_api_key = os.getenv("TAGGUN_API_KEY")

    if not taggun_api_key:
        # Mock OCR for development
        logger.warning("TAGGUN_API_KEY not set — using mock OCR")
        submission.ocr_provider = "mock"
        submission.ocr_merchant_name = submission.place_id or "Mock Merchant"
        submission.ocr_total_cents = 1500  # $15.00
        submission.ocr_confidence = 0.95
        submission.ocr_timestamp = datetime.utcnow()
        submission.status = ReceiptStatus.APPROVED
        submission.approved_reward_cents = _calculate_reward(submission)
        submission.reviewed_by = "auto"
        _approve_claim(db, submission)
        db.commit()
        db.refresh(submission)
        return submission

    # Real Taggun OCR
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.taggun.io/api/receipt/v1/verbose/url",
                headers={
                    "apikey": taggun_api_key,
                    "Content-Type": "application/json",
                },
                json={"url": submission.image_url},
            )
            response.raise_for_status()
            ocr_data = response.json()

        submission.ocr_provider = "taggun"
        submission.ocr_raw_response = ocr_data
        submission.ocr_confidence = ocr_data.get("confidenceLevel", 0) / 100.0

        # Extract merchant name
        merchant_name_data = ocr_data.get("merchantName", {})
        submission.ocr_merchant_name = merchant_name_data.get("data", "")

        # Extract total
        total_data = ocr_data.get("totalAmount", {})
        if total_data.get("data"):
            submission.ocr_total_cents = int(float(total_data["data"]) * 100)

        # Extract date
        date_data = ocr_data.get("date", {})
        if date_data.get("data"):
            try:
                submission.ocr_timestamp = datetime.fromisoformat(date_data["data"])
            except (ValueError, TypeError):
                pass

        # Auto-approve logic
        confidence = submission.ocr_confidence or 0
        if confidence >= 0.7 and submission.ocr_total_cents and submission.ocr_total_cents > 0:
            submission.status = ReceiptStatus.APPROVED
            submission.approved_reward_cents = _calculate_reward(submission)
            submission.reviewed_by = "auto"
            _approve_claim(db, submission)
            logger.info(f"Receipt {submission.id} auto-approved: confidence={confidence}, total=${submission.ocr_total_cents/100:.2f}")
        else:
            # Flag for manual review
            submission.status = ReceiptStatus.PENDING
            submission.rejection_reason = f"Low confidence ({confidence:.0%}) or missing total — flagged for review"
            logger.info(f"Receipt {submission.id} flagged for review: confidence={confidence}")

    except Exception as e:
        logger.error(f"OCR processing failed for {submission.id}: {e}", exc_info=True)
        # Don't reject — just leave as pending for manual review
        submission.status = ReceiptStatus.PENDING
        submission.rejection_reason = f"OCR processing error: {str(e)}"

    db.commit()
    db.refresh(submission)
    return submission


def _calculate_reward(submission: ReceiptSubmission) -> int:
    """Calculate reward amount based on receipt total. Default: flat reward from claim."""
    # For now, use the reward_cents from the linked claim
    # Future: calculate cashback % from campaign rules
    return 0  # Will be set from claim.reward_cents in _approve_claim


def _approve_claim(db: Session, submission: ReceiptSubmission):
    """Mark the linked reward claim as approved and punch loyalty cards."""
    claim = db.query(RewardClaim).filter(
        RewardClaim.id == submission.reward_claim_id,
    ).first()
    if claim:
        claim.status = RewardClaimStatus.APPROVED
        claim.completed_at = datetime.utcnow()
        # Set reward from claim's original amount
        if claim.reward_cents:
            submission.approved_reward_cents = claim.reward_cents
        logger.info(f"Claim {claim.id} approved via receipt {submission.id}")

        # Auto-punch loyalty cards for this merchant
        if claim.merchant_id and claim.driver_user_id:
            try:
                from app.services.loyalty_service import increment_visit
                increment_visit(db, claim.driver_user_id, claim.merchant_id)
            except Exception as e:
                logger.debug("Loyalty punch failed (non-fatal): %s", e)


def get_receipt_for_claim(db: Session, reward_claim_id: str) -> Optional[ReceiptSubmission]:
    """Get the latest receipt submission for a claim."""
    return db.query(ReceiptSubmission).filter(
        ReceiptSubmission.reward_claim_id == reward_claim_id,
    ).order_by(ReceiptSubmission.created_at.desc()).first()


# ---------------------------------------------------------------------------
# 4. Merchant Reward State (for enriching merchant detail responses)
# ---------------------------------------------------------------------------

def get_merchant_reward_state(
    db: Session,
    place_id: Optional[str],
    merchant_id: Optional[str],
    driver_user_id: Optional[int],
) -> Dict[str, Any]:
    """
    Get the reward state for a merchant from the driver's perspective.
    Used to enrich the merchant detail response.
    """
    state = {
        "has_active_reward": False,
        "reward_description": None,
        "reward_amount_cents": None,
        "active_claim_id": None,
        "active_claim_status": None,
        "active_claim_expires_at": None,
        "join_request_count": 0,
        "user_has_requested": False,
    }

    if not place_id and not merchant_id:
        return state

    # Check for active perk/exclusive on this merchant (existing system)
    from app.models.while_you_charge import ChargerMerchant, MerchantPerk
    has_perk = False

    if merchant_id:
        perk = db.query(MerchantPerk).filter(
            MerchantPerk.merchant_id == merchant_id,
            MerchantPerk.is_active == True,
        ).first()
        if perk:
            has_perk = True
            state["has_active_reward"] = True
            state["reward_description"] = perk.title

    if not has_perk and merchant_id:
        # Check charger_merchant exclusives
        link = db.query(ChargerMerchant).filter(
            ChargerMerchant.merchant_id == merchant_id,
            ChargerMerchant.exclusive_title.isnot(None),
        ).first()
        if link:
            has_perk = True
            state["has_active_reward"] = True
            state["reward_description"] = link.exclusive_title

    # Check for active reward claim by this driver
    if driver_user_id and (place_id or merchant_id):
        filters = [
            RewardClaim.driver_user_id == driver_user_id,
            RewardClaim.status.in_([
                RewardClaimStatus.CLAIMED,
                RewardClaimStatus.RECEIPT_UPLOADED,
            ]),
            RewardClaim.expires_at > datetime.utcnow(),
        ]
        if place_id:
            filters.append(RewardClaim.place_id == place_id)
        elif merchant_id:
            filters.append(RewardClaim.merchant_id == merchant_id)

        active_claim = db.query(RewardClaim).filter(*filters).first()
        if active_claim:
            state["active_claim_id"] = active_claim.id
            state["active_claim_status"] = active_claim.status.value if hasattr(active_claim.status, 'value') else active_claim.status
            state["active_claim_expires_at"] = active_claim.expires_at.isoformat()

    # Join request count
    if place_id:
        state["join_request_count"] = get_join_request_count(db, place_id)
        if driver_user_id:
            state["user_has_requested"] = user_has_requested(db, driver_user_id, place_id)

    return state
