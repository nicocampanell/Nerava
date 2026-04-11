"""
Nerava Score — reliability rating for chargers (0-100).

Based on:
- Session completion rate (40%): sessions > 5 min / total sessions
- Recency (30%): sessions in last 7 days (more recent = higher score)
- Diversity (20%): unique drivers (more diverse = more trusted)
- Duration (10%): avg session duration (longer = more reliable)

Returns None if fewer than 5 total sessions (insufficient data).
"""
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.session_event import SessionEvent


def compute_nerava_score(charger_id: str, db: Session):
    # Total sessions
    total = db.query(func.count(SessionEvent.id)).filter(
        SessionEvent.charger_id == charger_id,
        SessionEvent.session_end.isnot(None),
    ).scalar() or 0

    if total < 5:
        return None

    # Completed sessions (> 5 min)
    completed = db.query(func.count(SessionEvent.id)).filter(
        SessionEvent.charger_id == charger_id,
        SessionEvent.session_end.isnot(None),
        SessionEvent.duration_minutes > 5,
    ).scalar() or 0

    # Recent sessions (last 7 days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent = db.query(func.count(SessionEvent.id)).filter(
        SessionEvent.charger_id == charger_id,
        SessionEvent.session_start >= seven_days_ago,
    ).scalar() or 0

    # Unique drivers
    unique_drivers = db.query(func.count(func.distinct(SessionEvent.driver_user_id))).filter(
        SessionEvent.charger_id == charger_id,
        SessionEvent.session_end.isnot(None),
    ).scalar() or 0

    # Avg duration
    avg_duration = db.query(func.avg(SessionEvent.duration_minutes)).filter(
        SessionEvent.charger_id == charger_id,
        SessionEvent.session_end.isnot(None),
        SessionEvent.duration_minutes > 0,
    ).scalar() or 0

    # Completion rate (0-100)
    completion_score = min(100, (completed / total) * 100) if total > 0 else 0

    # Recency score (0-100): 10+ recent sessions = 100
    recency_score = min(100, (recent / 10) * 100)

    # Diversity score (0-100): 20+ unique drivers = 100
    diversity_score = min(100, (unique_drivers / 20) * 100)

    # Duration score (0-100): 30+ min avg = 100
    duration_score = min(100, (float(avg_duration) / 30) * 100)

    # Weighted average
    score = (
        completion_score * 0.40 +
        recency_score * 0.30 +
        diversity_score * 0.20 +
        duration_score * 0.10
    )

    return round(min(100, max(0, score)), 1)
