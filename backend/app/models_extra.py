# LEGACY: This file has been moved to app/models/extra.py
# Import from new location for backward compatibility
from .models.extra import (
    Challenge,
    CommunityPeriod,
    CreditLedger,
    DualZoneSession,
    FeatureFlag,
    Follow,
    FollowerShare,
    IncentiveRule,
    Participation,
    RewardEvent,
    UtilityEvent,
)

__all__ = [
    "CreditLedger",
    "IncentiveRule",
    "UtilityEvent",
    "Follow",
    "RewardEvent",
    "FollowerShare",
    "CommunityPeriod",
    "Challenge",
    "Participation",
    "FeatureFlag",
    "DualZoneSession",
]
