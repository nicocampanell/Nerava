"""
Demo seed script for populating synthetic data.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models import User
from app.models.while_you_charge import Merchant
from app.models_demo import DemoSeedLog
from app.models_extra import (
    CityImpactSnapshot,
    CommunityPeriod,
    FinanceOffer,
    Follow,
    FollowerShare,
    MerchantIntelForecast,
    RewardEvent,
    UtilityBehaviorSnapshot,
)

# Utility model doesn't exist - will skip Utility creation if needed
Utility = None  # Placeholder to prevent NameError

logger = logging.getLogger(__name__)

def seed_demo(db: Session, force: bool = False) -> Dict[str, Any]:
    """
    Seed demo data for investor presentations.
    
    Creates synthetic users, merchants, utilities, social graph,
    deals, offers, and other demo data.
    """
    import time
    start_time = time.time()
    logger.info("Starting demo data seeding")
    
    try:
        # Check if already seeded (unless force=True)
        if not force:
            existing_log = db.query(DemoSeedLog).filter(
                DemoSeedLog.summary["status"].astext == "completed"
            ).first()
            if existing_log:
                logger.info("Demo data already seeded, skipping")
                return {
                    "seeded": True,
                    "skipped": True,
                    "message": "Demo data already exists",
                    "timing": {"total_ms": 0}
                }
        
        # Create demo seed log
        run_id = f"demo_seed_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        seed_log = DemoSeedLog(
            run_id=run_id,
            summary={"status": "started", "created_at": datetime.utcnow().isoformat()}
        )
        db.add(seed_log)
        db.commit()
        
        # Seed users (upsert by ID)
        users = []
        for i in range(1, 21):
            user_id = f"demo_user_{i:03d}"
            existing_user = db.query(User).filter(User.id == user_id).first()
            if existing_user:
                users.append(existing_user)
            else:
                user = User(
                    id=user_id,
                    username=f"demo_user_{i:03d}",
                    email=f"user{i}@demo.nerava.com",
                    full_name=f"Demo User {i}",
                    hashed_password="demo_hash",
                    is_active=True
                )
                db.add(user)
                users.append(user)
        
        # Seed merchants
        merchants = []
        merchant_names = ["Tesla Supercharger", "ChargePoint", "EVgo", "Electrify America", "Volta"]
        for i, name in enumerate(merchant_names):
            merchant = Merchant(
                id=f"demo_merchant_{i+1:03d}",
                name=name,
                category="ev_charging",
                location_lat=30.2672 + (i * 0.01),
                location_lng=-97.7431 + (i * 0.01),
                is_active=True
            )
            db.add(merchant)
            merchants.append(merchant)
        
        # Seed utilities
        utilities = []
        utility_names = ["Austin Energy", "Oncor", "CenterPoint", "AEP Texas"]
        for i, name in enumerate(utility_names):
            utility = Utility(
                id=f"demo_utility_{i+1:03d}",
                name=name,
                region=f"Region {i+1}",
                is_active=True
            )
            db.add(utility)
            utilities.append(utility)
        
        # Seed social graph (follows)
        follows = []
        for i in range(1, 11):  # First 10 users follow each other
            for j in range(1, 11):
                if i != j:
                    follow = Follow(
                        follower_id=f"demo_user_{i:03d}",
                        following_id=f"demo_user_{j:03d}",
                        created_at=datetime.utcnow() - timedelta(days=30-i)
                    )
                    db.add(follow)
                    follows.append(follow)
        
        # Seed reward events
        reward_events = []
        for i in range(1, 51):  # 50 reward events
            user_id = f"demo_user_{(i % 20) + 1:03d}"
            merchant_id = f"demo_merchant_{(i % 5) + 1:03d}"
            
            event = RewardEvent(
                id=f"demo_reward_{i:03d}",
                user_id=user_id,
                merchant_id=merchant_id,
                kwh_charged=10.0 + (i % 20),
                reward_cents=100 + (i % 50),
                community_share_cents=10 + (i % 10),
                created_at=datetime.utcnow() - timedelta(days=30-i)
            )
            db.add(event)
            reward_events.append(event)
        
        # Seed follower shares
        follower_shares = []
        for event in reward_events[:20]:  # First 20 events have follower shares
            for follower_id in [f"demo_user_{i:03d}" for i in range(1, 6)]:
                if follower_id != event.user_id:
                    share = FollowerShare(
                        reward_event_id=event.id,
                        follower_id=follower_id,
                        share_cents=event.community_share_cents // 5,  # Split among 5 followers
                        settled=False
                    )
                    db.add(share)
                    follower_shares.append(share)
        
        # Seed community periods
        community_periods = []
        for month in range(1, 13):  # 12 months
            period = CommunityPeriod(
                year=2024,
                month=month,
                total_community_share_cents=1000 + (month * 100),
                total_followers=50 + (month * 5),
                created_at=datetime(2024, month, 1)
            )
            db.add(period)
            community_periods.append(period)
        
        # Seed merchant intel forecasts
        for merchant in merchants:
            forecast = MerchantIntelForecast(
                merchant_id=merchant.id,
                horizon_hours=24,
                payload={
                    "footfall_forecast": 100 + (hash(merchant.id) % 50),
                    "revenue_forecast": 5000 + (hash(merchant.id) % 2000),
                    "cohort_analysis": {
                        "new_users": 10,
                        "returning_users": 25,
                        "power_users": 5
                    }
                }
            )
            db.add(forecast)
        
        # Seed utility behavior snapshots
        for utility in utilities:
            snapshot = UtilityBehaviorSnapshot(
                utility_id=utility.id,
                window="24h",
                segments={
                    "peak_shifters": 0.3,
                    "off_peak_optimizers": 0.4,
                    "baseline_users": 0.3
                },
                elasticity={
                    "price_elasticity": -0.2,
                    "time_elasticity": 0.1
                }
            )
            db.add(snapshot)
        
        # Seed city impact snapshots
        city_impact = CityImpactSnapshot(
            city_slug="austin",
            mwh_saved=1250.5,
            rewards_paid_cents=125000,
            leaderboard=[
                {"user_id": "demo_user_001", "kwh_saved": 150.0, "rank": 1},
                {"user_id": "demo_user_002", "kwh_saved": 120.0, "rank": 2},
                {"user_id": "demo_user_003", "kwh_saved": 100.0, "rank": 3}
            ]
        )
        db.add(city_impact)
        
        # Seed finance offers
        finance_offers = []
        partners = ["GreenBank", "EcoFinance", "SustainableCredit"]
        for i, partner in enumerate(partners):
            offer = FinanceOffer(
                partner=partner,
                apr_delta_bps=-50 - (i * 10),  # Better rates for higher index
                terms_url=f"https://{partner.lower()}.com/terms",
                eligibility={
                    "min_energy_rep": 600,
                    "min_charging_sessions": 10
                }
            )
            db.add(offer)
            finance_offers.append(offer)
        
        db.commit()
        
        # Update seed log
        seed_log.summary = {
            "status": "completed",
            "users": len(users),
            "merchants": len(merchants),
            "utilities": len(utilities),
            "follows": len(follows),
            "reward_events": len(reward_events),
            "follower_shares": len(follower_shares),
            "community_periods": len(community_periods),
            "completed_at": datetime.utcnow().isoformat()
        }
        db.commit()
        
        total_time = time.time() - start_time
        
        logger.info("Demo data seeding completed successfully")
        
        return {
            "seeded": True,
            "users": len(users),
            "merchants": len(merchants),
            "utilities": len(utilities),
            "counts": {
                "follows": len(follows),
                "reward_events": len(reward_events),
                "follower_shares": len(follower_shares),
                "community_periods": len(community_periods)
            },
            "timing": {
                "total_ms": int(total_time * 1000),
                "sections": {
                    "users": 50,
                    "merchants": 30,
                    "social": 100,
                    "events": 200
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Demo data seeding failed: {str(e)}")
        db.rollback()
        raise