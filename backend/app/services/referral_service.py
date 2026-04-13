"""Referral system service."""

from __future__ import annotations

import logging
import re
import secrets
import string
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.referral import ReferralCode, ReferralRedemption

logger = logging.getLogger(__name__)

REFERRAL_REWARD_CENTS = 250  # $2.50


def _generate_code() -> str:
    """Generate a unique 8-char alphanumeric referral code."""
    chars = string.ascii_uppercase + string.digits
    # Remove ambiguous characters
    chars = (
        chars.replace("O", "").replace("0", "").replace("I", "").replace("1", "").replace("L", "")
    )
    return "NERAVA-" + "".join(secrets.choice(chars) for _ in range(6))


def _code_from_name(name: str) -> str:
    """Generate a referral code prefix from a display name."""
    slug = re.sub(r"[^A-Z0-9]", "", name.upper())[:8]
    if not slug:
        return _generate_code()
    return f"NERAVA-{slug}"


def get_or_create_code(
    db: Session, user_id: int, display_name: Optional[str] = None
) -> ReferralCode:
    """Get existing or create new referral code for user."""
    existing = db.query(ReferralCode).filter(ReferralCode.user_id == user_id).first()
    if existing:
        return existing

    def _next_unique_random_code() -> str:
        for _ in range(10):
            candidate = _generate_code()
            if not db.query(ReferralCode).filter(ReferralCode.code == candidate).first():
                return candidate
        raise ValueError("Failed to generate unique referral code")

    if display_name:
        # Try name-based code, then with numeric suffix
        base_code = _code_from_name(display_name)
        code = base_code
        for suffix in ["", "2", "3", "4", "5", "6", "7", "8", "9"]:
            candidate = base_code + suffix
            if not db.query(ReferralCode).filter(ReferralCode.code == candidate).first():
                code = candidate
                break
        else:
            # All name-based variants taken, fall back to random with retry
            code = _next_unique_random_code()
    else:
        code = _next_unique_random_code()

    ref_code = ReferralCode(user_id=user_id, code=code)
    try:
        db.add(ref_code)
        db.commit()
        db.refresh(ref_code)
    except Exception:
        db.rollback()
        logger.error("Failed to create referral code for user %s", user_id, exc_info=True)
        raise
    return ref_code


def redeem_referral(db: Session, code: str, new_user_id: int) -> Optional[ReferralRedemption]:
    """Redeem a referral code for a new user. Returns None if invalid."""
    ref_code = db.query(ReferralCode).filter(ReferralCode.code == code).first()
    if not ref_code:
        return None

    # Can't self-refer
    if ref_code.user_id == new_user_id:
        return None

    # Check if already redeemed
    existing = (
        db.query(ReferralRedemption)
        .filter(ReferralRedemption.referred_user_id == new_user_id)
        .first()
    )
    if existing:
        return existing  # Idempotent

    redemption = ReferralRedemption(
        referral_code_id=ref_code.id,
        referred_user_id=new_user_id,
        reward_granted=False,
    )
    db.add(redemption)
    db.commit()
    db.refresh(redemption)
    logger.info(
        f"Referral redeemed: code={code}, referrer={ref_code.user_id}, referred={new_user_id}"
    )
    return redemption


def grant_referral_rewards(db: Session, user_id: int) -> bool:
    """Grant referral rewards after first completed session. Returns True if rewards granted."""
    # Find pending redemption for this user
    redemption = (
        db.query(ReferralRedemption)
        .filter(
            ReferralRedemption.referred_user_id == user_id,
            ReferralRedemption.reward_granted == False,
        )
        .first()
    )
    if not redemption:
        return False

    ref_code = db.query(ReferralCode).filter(ReferralCode.id == redemption.referral_code_id).first()
    if not ref_code:
        return False

    # Credit both wallets
    try:
        from app.services.payout_service import credit_wallet

        credit_wallet(
            db, ref_code.user_id, REFERRAL_REWARD_CENTS, "Referral reward - friend joined"
        )
        credit_wallet(db, user_id, REFERRAL_REWARD_CENTS, "Referral welcome bonus")
        redemption.reward_granted = True
        db.commit()
        logger.info(
            f"Referral rewards granted: referrer={ref_code.user_id}, referred={user_id}, amount={REFERRAL_REWARD_CENTS}"
        )
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to grant referral rewards: {e}")
        return False


def get_referral_stats(db: Session, user_id: int) -> dict:
    """Get referral stats for a user."""
    ref_code = db.query(ReferralCode).filter(ReferralCode.user_id == user_id).first()
    if not ref_code:
        return {"total_referrals": 0, "total_earned_cents": 0, "pending_count": 0}

    total = (
        db.query(func.count(ReferralRedemption.id))
        .filter(ReferralRedemption.referral_code_id == ref_code.id)
        .scalar()
        or 0
    )

    granted = (
        db.query(func.count(ReferralRedemption.id))
        .filter(
            ReferralRedemption.referral_code_id == ref_code.id,
            ReferralRedemption.reward_granted == True,
        )
        .scalar()
        or 0
    )

    pending = total - granted

    return {
        "total_referrals": total,
        "total_earned_cents": granted * REFERRAL_REWARD_CENTS,
        "pending_count": pending,
    }
