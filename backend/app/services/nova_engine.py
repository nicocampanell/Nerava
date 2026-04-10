"""
Nova Engine - Pure reward calculation logic
Extracts reward calculation logic from energy_rep_cron, reward_routing_runner, and other reward logic.
This file should be pure business logic; no FastAPI, no DB session.
"""
from datetime import datetime, time
from typing import Dict, List, Optional


def _parse_t(s: str) -> time:
    """Parse time string like '22:00' or '06:00' into time object."""
    hh, mm, *rest = s.split(":")
    return time(int(hh), int(mm or 0))


def is_off_peak(now: datetime, window: List[str]) -> bool:
    """
    Check if current time is within off-peak window.
    
    Args:
        now: Current datetime
        window: List of two time strings like ["22:00", "06:00"]
        
    Returns:
        True if current time is within off-peak window
    """
    start = _parse_t(window[0])
    end = _parse_t(window[1])
    t = now.time()
    # Handles windows crossing midnight (e.g., 22:00–06:00)
    return (t >= start) or (t < end) if start > end else (start <= t < end)


def calculate_nova_for_session(
    kwh: Optional[float],
    duration_minutes: Optional[int],
    session_time: datetime,
    rules: List[Dict],
) -> int:
    """
    Calculate Nova amount for a charging session.
    
    Pure business logic function - no DB side effects.
    
    Args:
        kwh: Energy charged in kWh (None if unknown)
        duration_minutes: Duration in minutes (None if unknown)
        session_time: When the session occurred
        rules: List of incentive rules (from IncentiveRule model or equivalent)
        
    Returns:
        Nova amount (in smallest unit, e.g., cents or points)
        Returns 0 if missing required data or not in off-peak window
    """
    # Edge case: missing kWh or duration should not crash; return zero
    if kwh is None or duration_minutes is None:
        return 0
    
    # Check if session was during off-peak hours
    total_nova = 0
    for rule in rules:
        if not rule.get("active", True):
            continue
        if rule.get("code") == "OFF_PEAK_BASE":
            window = rule.get("params", {}).get("window", ["22:00", "06:00"])
            base_cents = int(rule.get("params", {}).get("cents", 25))
            
            if is_off_peak(session_time, window):
                # Apply base reward for off-peak session
                # Could scale by kWh or duration in the future
                total_nova += base_cents
            # Peak sessions get zero Nova
    
    return total_nova


def calc_award_cents(now: datetime, rules: List[Dict]) -> int:
    """
    Calculate award in cents based on current time and rules.
    
    This is a simpler version that doesn't require session data.
    Used for simple time-based awards.
    
    Args:
        now: Current datetime
        rules: List of incentive rules
        
    Returns:
        Award amount in cents
    """
    total = 0
    for r in rules:
        if not r.get("active", True):
            continue
        if r.get("code") == "OFF_PEAK_BASE":
            win = r.get("params", {}).get("window", ["22:00", "06:00"])
            cents = int(r.get("params", {}).get("cents", 25))
            if is_off_peak(now, win):
                total += cents
    return total


