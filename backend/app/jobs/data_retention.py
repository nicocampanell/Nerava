"""
Data Retention Job

Deletes old records and anonymizes old exclusive session locations per GDPR requirements.

Run command:
    python -m app.jobs.data_retention

Or via script:
    cd backend && python -m app.jobs.data_retention
"""
import os
import sys
from datetime import datetime, timedelta, timezone

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import logging

from app.db import SessionLocal
from app.models import ExclusiveSession, IntentSession
from app.models.claim_session import ClaimSession
from app.models.merchant_cache import MerchantCache
from app.models.otp_challenge import OTPChallenge
from app.models.vehicle_onboarding import VehicleOnboarding
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def run_data_retention(db: Session):
    """
    Run data retention cleanup:
    - Delete intent_sessions older than 90 days
    - Delete otp_challenges older than 7 days
    - Delete claim_sessions older than 30 days
    - Delete vehicle_onboarding older than 1 year
    - Delete merchant_cache older than 30 days
    - Anonymize old exclusive_sessions locations (set activation_lat, activation_lng to NULL) older than 1 year
    """
    now = datetime.now(timezone.utc)
    
    # 1. Delete intent_sessions older than 90 days
    cutoff_intent = now - timedelta(days=90)
    deleted_intent = db.query(IntentSession).filter(
        IntentSession.created_at < cutoff_intent
    ).delete(synchronize_session=False)
    logger.info(f"Deleted {deleted_intent} intent_sessions older than 90 days")
    
    # 2. Delete otp_challenges older than 7 days
    cutoff_otp = now - timedelta(days=7)
    deleted_otp = db.query(OTPChallenge).filter(
        OTPChallenge.created_at < cutoff_otp
    ).delete(synchronize_session=False)
    logger.info(f"Deleted {deleted_otp} otp_challenges older than 7 days")
    
    # 3. Delete claim_sessions older than 30 days
    cutoff_claim = now - timedelta(days=30)
    deleted_claim = db.query(ClaimSession).filter(
        ClaimSession.created_at < cutoff_claim
    ).delete(synchronize_session=False)
    logger.info(f"Deleted {deleted_claim} claim_sessions older than 30 days")
    
    # 4. Delete vehicle_onboarding older than 1 year
    cutoff_vehicle = now - timedelta(days=365)
    deleted_vehicle = db.query(VehicleOnboarding).filter(
        VehicleOnboarding.created_at < cutoff_vehicle
    ).delete(synchronize_session=False)
    logger.info(f"Deleted {deleted_vehicle} vehicle_onboarding records older than 1 year")
    
    # 5. Delete merchant_cache older than 30 days
    cutoff_cache = now - timedelta(days=30)
    deleted_cache = db.query(MerchantCache).filter(
        MerchantCache.created_at < cutoff_cache
    ).delete(synchronize_session=False)
    logger.info(f"Deleted {deleted_cache} merchant_cache records older than 30 days")
    
    # 6. Anonymize old exclusive_sessions locations (older than 1 year)
    cutoff_exclusive = now - timedelta(days=365)
    anonymized_exclusive = db.query(ExclusiveSession).filter(
        ExclusiveSession.created_at < cutoff_exclusive,
        ExclusiveSession.activation_lat.isnot(None)  # Only update if location exists
    ).update(
        {
            "activation_lat": None,
            "activation_lng": None,
            "activation_accuracy_m": None,
        },
        synchronize_session=False
    )
    logger.info(f"Anonymized locations for {anonymized_exclusive} exclusive_sessions older than 1 year")
    
    db.commit()
    
    return {
        "deleted_intent_sessions": deleted_intent,
        "deleted_otp_challenges": deleted_otp,
        "deleted_claim_sessions": deleted_claim,
        "deleted_vehicle_onboarding": deleted_vehicle,
        "deleted_merchant_cache": deleted_cache,
        "anonymized_exclusive_sessions": anonymized_exclusive,
    }


def main():
    """Main entry point for data retention job"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    db = SessionLocal()
    try:
        logger.info("Starting data retention job...")
        results = run_data_retention(db)
        logger.info(f"Data retention job completed: {results}")
    except Exception as e:
        logger.error(f"Data retention job failed: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
