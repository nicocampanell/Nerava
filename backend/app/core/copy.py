"""
Central Copy Constants Module

Single source of truth for all user-facing copy used in API responses.
DO NOT use "verification", "verified", or "verify" terminology.
Use: "intent", "charging moment", "confidence", "trust reinforcement".
"""

# Location education copy
LOCATION_EDUCATION_COPY = {
    "title": "Find nearby places while you charge",
    "description": "We'll show you walkable merchants near your charging location.",
    "accuracy_required": "Please enable location services for accurate results.",
}

# Tier C fallback copy
TIER_C_FALLBACK_COPY = (
    "We don't see a public charger nearby. "
    "This activates when you're charging at a public station."
)

# Vehicle onboarding explanation copy
VEHICLE_ONBOARDING_EXPLANATION = {
    "title": "Complete your vehicle setup",
    "description": (
        "To help ensure trust and prevent abuse, we need a few photos "
        "of your vehicle at a charging station. This is a one-time process."
    ),
    "instructions": (
        "Take 3-5 photos of your EV plugged in at a charger. "
        "Make sure the charger and your vehicle are clearly visible."
    ),
    "submitted": "Your photos have been submitted and are under review.",
    "approved": "Your vehicle setup is complete!",
    "rejected": "Your photos were not approved. Please try again with clearer photos.",
}

# Perk unlock copy
PERK_UNLOCK_COPY = {
    "success": "Perk unlocked successfully!",
    "already_unlocked": "This perk is already unlocked.",
    "session_limit": "You've reached the maximum number of perk unlocks for this charging moment.",
    "cooldown": "Please wait before unlocking another perk from this merchant.",
    "tier_required": "Perks are only available when you're at a charging location (confidence tier A or B).",
}

# Vehicle onboarding status messages
VEHICLE_ONBOARDING_STATUS_COPY = {
    "not_required": "Vehicle setup is not required at this time.",
    "required": "Vehicle setup is required to continue.",
    "submitted": "Your vehicle setup is pending review.",
    "approved": "Your vehicle setup is complete.",
    "rejected": "Your vehicle setup was rejected. Please try again.",
}



