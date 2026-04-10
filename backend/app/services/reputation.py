"""
Energy Reputation Tier Computation Service.

Single source of truth for tier definitions and reputation calculations.
"""
from typing import Any, Dict

# Tier definitions (single source of truth)
# Using min points only - avoids boundary bugs with max comparisons
TIERS = [
    {"name": "Bronze",   "min": 0,   "next_min": 100, "color": "#78716c"},
    {"name": "Silver",   "min": 100, "next_min": 300, "color": "#64748b"},
    {"name": "Gold",     "min": 300, "next_min": 700, "color": "#eab308"},
    {"name": "Platinum", "min": 700, "next_min": None, "color": "#06b6d4"},
]


def compute_reputation(points: int) -> Dict[str, Any]:
    """
    Compute tier, progress, and next-tier info from reputation points.
    
    Uses min-based tier determination to avoid boundary bugs.
    
    Args:
        points: Energy reputation score (clamped to >= 0)
    
    Returns:
        Dict with:
        - points: int (clamped >= 0)
        - tier: str (current tier name)
        - tier_color: str (hex color for UI)
        - next_tier: str | None (next tier name, None if Platinum)
        - points_to_next: int | None (points needed for next tier, None if Platinum)
        - progress_to_next: float (0-1, progress within current tier, 1.0 if Platinum)
    """
    # Clamp points to >= 0
    p = max(0, int(points or 0))
    
    # Determine tier using min-based comparisons (avoids boundary bugs)
    # Check from highest to lowest tier
    if p >= 700:
        current_tier = TIERS[3]  # Platinum
        next_tier = None
    elif p >= 300:
        current_tier = TIERS[2]  # Gold
        next_tier = TIERS[3]  # Platinum
    elif p >= 100:
        current_tier = TIERS[1]  # Silver
        next_tier = TIERS[2]  # Gold
    else:
        current_tier = TIERS[0]  # Bronze
        next_tier = TIERS[1]  # Silver
    
    # If Platinum (no next tier), return maxed out values
    if next_tier is None:
        return {
            "points": p,
            "tier": current_tier["name"],
            "tier_color": current_tier["color"],
            "next_tier": None,
            "points_to_next": None,
            "progress_to_next": 1.0,
        }
    
    # Calculate points to next tier
    points_to_next = max(0, next_tier["min"] - p)
    
    # Calculate progress within current tier (0-1)
    # Progress = (points - current_tier_min) / (next_tier_min - current_tier_min)
    tier_span = next_tier["min"] - current_tier["min"]
    if tier_span > 0:
        progress = (p - current_tier["min"]) / tier_span
        # Clamp progress to 0-1 (shouldn't exceed 1.0 with min-based logic, but defensive)
        progress = max(0.0, min(1.0, progress))
    else:
        progress = 0.0
    
    return {
        "points": p,
        "tier": current_tier["name"],
        "tier_color": current_tier["color"],
        "next_tier": next_tier["name"],
        "points_to_next": points_to_next,
        "progress_to_next": progress,  # Do NOT round - let frontend format
    }

