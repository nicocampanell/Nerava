from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_extra import RewardEvent

router = APIRouter(prefix="/v1/grid", tags=["grid"])

# Grid intelligence coefficients
KWH_PER_CENT = 0.1  # 0.1 kWh per cent reward (example)
CO2_PER_KWH = 0.4  # 0.4 kg CO2 per kWh (example)

@router.get("/metrics/current")
async def get_current_metrics(db: Session = Depends(get_db)):
    """Get current grid intelligence metrics."""
    try:
        # Get current period (this month)
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Aggregate current period data
        current_data = db.query(
            func.sum(RewardEvent.gross_cents).label('total_cents'),
            func.count(RewardEvent.id).label('total_events'),
            func.avg(RewardEvent.gross_cents).label('avg_cents')
        ).filter(
            RewardEvent.created_at >= period_start
        ).first()
        
        # Calculate derived metrics
        total_cents = current_data.total_cents or 0
        total_kwh = total_cents * KWH_PER_CENT
        total_co2_kg = total_kwh * CO2_PER_KWH
        
        # Get top sources
        top_sources = db.query(
            RewardEvent.source,
            func.sum(RewardEvent.gross_cents).label('total_cents'),
            func.count(RewardEvent.id).label('event_count')
        ).filter(
            RewardEvent.created_at >= period_start
        ).group_by(RewardEvent.source).order_by(desc('total_cents')).limit(5).all()
        
        return {
            "period": now.strftime("%Y-%m"),
            "total_rewards_cents": total_cents,
            "total_kwh": round(total_kwh, 2),
            "total_co2_kg": round(total_co2_kg, 2),
            "total_events": current_data.total_events or 0,
            "avg_reward_cents": round(current_data.avg_cents or 0, 2),
            "top_sources": [
                {
                    "source": source,
                    "total_cents": total_cents,
                    "event_count": event_count
                }
                for source, total_cents, event_count in top_sources
            ],
            "coefficients": {
                "kwh_per_cent": KWH_PER_CENT,
                "co2_per_kwh": CO2_PER_KWH
            }
        }
        
    except Exception as e:
        return {"error": f"Failed to get current metrics: {str(e)}"}

@router.get("/metrics/time-series")
async def get_time_series_metrics(
    days: int = Query(30, description="Number of days to look back"),
    db: Session = Depends(get_db)
):
    """Get time series grid intelligence metrics."""
    try:
        # Calculate date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)
        
        # Get daily aggregates
        daily_data = db.query(
            func.date(RewardEvent.created_at).label('date'),
            func.sum(RewardEvent.gross_cents).label('total_cents'),
            func.count(RewardEvent.id).label('event_count')
        ).filter(
            RewardEvent.created_at >= start_date,
            RewardEvent.created_at <= end_date
        ).group_by(func.date(RewardEvent.created_at)).order_by('date').all()
        
        # Format time series data
        time_series = []
        for date, total_cents, event_count in daily_data:
            total_kwh = total_cents * KWH_PER_CENT
            total_co2_kg = total_kwh * CO2_PER_KWH
            
            time_series.append({
                "date": date.isoformat(),
                "total_rewards_cents": total_cents,
                "total_kwh": round(total_kwh, 2),
                "total_co2_kg": round(total_co2_kg, 2),
                "event_count": event_count
            })
        
        # Calculate summary stats
        total_cents = sum(ts["total_rewards_cents"] for ts in time_series)
        total_kwh = sum(ts["total_kwh"] for ts in time_series)
        total_co2_kg = sum(ts["total_co2_kg"] for ts in time_series)
        total_events = sum(ts["event_count"] for ts in time_series)
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days
            },
            "summary": {
                "total_rewards_cents": total_cents,
                "total_kwh": round(total_kwh, 2),
                "total_co2_kg": round(total_co2_kg, 2),
                "total_events": total_events,
                "avg_daily_kwh": round(total_kwh / days, 2),
                "avg_daily_co2_kg": round(total_co2_kg / days, 2)
            },
            "time_series": time_series,
            "coefficients": {
                "kwh_per_cent": KWH_PER_CENT,
                "co2_per_kwh": CO2_PER_KWH
            }
        }
        
    except Exception as e:
        return {"error": f"Failed to get time series metrics: {str(e)}"}

@router.get("/impact/summary")
async def get_impact_summary(db: Session = Depends(get_db)):
    """Get high-level impact summary for utilities/partners."""
    try:
        # Get all-time totals
        all_time = db.query(
            func.sum(RewardEvent.gross_cents).label('total_cents'),
            func.count(RewardEvent.id).label('total_events')
        ).first()
        
        # Get this year totals
        year_start = datetime.utcnow().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        this_year = db.query(
            func.sum(RewardEvent.gross_cents).label('total_cents'),
            func.count(RewardEvent.id).label('total_events')
        ).filter(RewardEvent.created_at >= year_start).first()
        
        # Calculate impact metrics
        all_time_cents = all_time.total_cents or 0
        all_time_kwh = all_time_cents * KWH_PER_CENT
        all_time_co2 = all_time_kwh * CO2_PER_KWH
        
        this_year_cents = this_year.total_cents or 0
        this_year_kwh = this_year_cents * KWH_PER_CENT
        this_year_co2 = this_year_kwh * CO2_PER_KWH
        
        return {
            "all_time": {
                "total_rewards_cents": all_time_cents,
                "total_kwh": round(all_time_kwh, 2),
                "total_co2_kg": round(all_time_co2, 2),
                "total_events": all_time.total_events or 0
            },
            "this_year": {
                "total_rewards_cents": this_year_cents,
                "total_kwh": round(this_year_kwh, 2),
                "total_co2_kg": round(this_year_co2, 2),
                "total_events": this_year.total_events or 0
            },
            "impact_metrics": {
                "kwh_per_cent": KWH_PER_CENT,
                "co2_per_kwh": CO2_PER_KWH,
                "estimated_co2_offset_kg": round(all_time_co2, 2),
                "estimated_energy_savings_kwh": round(all_time_kwh, 2)
            }
        }
        
    except Exception as e:
        return {"error": f"Failed to get impact summary: {str(e)}"}
