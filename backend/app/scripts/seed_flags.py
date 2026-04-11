#!/usr/bin/env python3
"""
Seed script to insert all 20 feature flags as disabled by default.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_db
from app.models_extra import FeatureFlag


def seed_feature_flags():
    """Insert all 20 feature flags with default disabled state."""
    
    # All 20 feature flags
    flags = [
        "feature_merchant_intel",
        "feature_behavior_cloud", 
        "feature_autonomous_reward_routing",
        "feature_city_marketplace",
        "feature_multimodal",
        "feature_merchant_credits",
        "feature_charge_verify_api",
        "feature_energy_wallet_ext",
        "feature_merchant_utility_coops",
        "feature_whitelabel_sdk",
        "feature_energy_rep",
        "feature_carbon_micro_offsets",
        "feature_fleet_workplace",
        "feature_smart_home_iot",
        "feature_contextual_commerce",
        "feature_energy_events",
        "feature_uap_partnerships",
        "feature_ai_reward_opt",
        "feature_esg_finance_gateway",
        "feature_ai_growth_automation"
    ]
    
    db = next(get_db())
    
    try:
        for flag_key in flags:
            # Check if flag already exists
            existing = db.query(FeatureFlag).filter(FeatureFlag.key == flag_key).first()
            if not existing:
                flag = FeatureFlag(
                    key=flag_key,
                    enabled=False,
                    env="prod"
                )
                db.add(flag)
                print(f"Added flag: {flag_key}")
            else:
                print(f"Flag already exists: {flag_key}")
        
        db.commit()
        print(f"Successfully seeded {len(flags)} feature flags")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding flags: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_feature_flags()
