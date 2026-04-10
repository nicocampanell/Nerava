from datetime import datetime, time, timedelta
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo


def _parse_t(s: str) -> time:
    hh, mm, *rest = s.split(":")
    return time(int(hh), int(mm or 0))

def is_off_peak(now: datetime, window: List[str]) -> bool:
    start = _parse_t(window[0])
    end   = _parse_t(window[1])
    t = now.time()
    # Handles windows crossing midnight (e.g., 22:00–06:00)
    return (t >= start) or (t < end) if start > end else (start <= t < end)

def calc_award_cents(now: datetime, rules: List[Dict]) -> int:
    total = 0
    for r in rules:
        if not r.get("active", True):
            continue
        if r.get("code") == "OFF_PEAK_BASE":
            win = r.get("params", {}).get("window", ["22:00","06:00"])
            cents = int(r.get("params", {}).get("cents", 25))
            if is_off_peak(now, win):
                total += cents
    return total

def get_offpeak_state(now: datetime, tz: ZoneInfo) -> Tuple[bool, int]:
    """
    Calculate off-peak state and seconds until window ends.
    
    Off-peak window: 22:00 → 06:00 (crosses midnight)
    
    Args:
        now: Current datetime (should be timezone-aware)
        tz: Timezone to use for calculation
    
    Returns:
        Tuple of (offpeak_active: bool, window_ends_in_seconds: int)
        window_ends_in_seconds is always >= 0
    """
    # Convert now to the specified timezone if needed
    if now.tzinfo is None:
        # If naive, assume it's UTC and convert to target timezone
        now = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    else:
        now = now.astimezone(tz)
    
    # Parse window times
    window_start = time(22, 0)  # 22:00
    window_end = time(6, 0)      # 06:00
    
    current_time = now.time()
    
    # Check if currently in off-peak window (22:00-06:00)
    offpeak_active = (current_time >= window_start) or (current_time < window_end)
    
    # Calculate seconds until window ends/starts
    if offpeak_active:
        # Currently in off-peak, window ends at next 06:00
        if current_time < window_end:
            # Between midnight and 06:00, window ends at 06:00 today
            window_end_dt = now.replace(hour=6, minute=0, second=0, microsecond=0)
        else:
            # Between 22:00 and midnight, window ends at 06:00 tomorrow
            window_end_dt = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        # Currently in peak hours (06:00-22:00)
        # Show time until off-peak window STARTS (22:00 today), not when it ends
        window_end_dt = now.replace(hour=22, minute=0, second=0, microsecond=0)
    
    # Calculate seconds difference
    delta = window_end_dt - now
    window_ends_in_seconds = max(0, int(delta.total_seconds()))
    
    return (offpeak_active, window_ends_in_seconds)
