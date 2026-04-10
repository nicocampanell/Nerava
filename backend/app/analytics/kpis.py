"""
Key Performance Indicators (KPIs) for business metrics
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from app.config import settings
from app.db import get_db
from sqlalchemy import text

logger = logging.getLogger(__name__)

class KPICalculator:
    """Calculator for business KPIs"""
    
    def __init__(self):
        self.region = settings.region
    
    async def get_conversion_funnel(self, days: int = 7) -> Dict[str, Any]:
        """Get conversion funnel metrics"""
        try:
            db = next(get_db())
            
            # Get funnel data
            funnel_result = db.execute(text("""
                SELECT 
                    COUNT(DISTINCT CASE WHEN event_type = 'analytics_charge_started' THEN aggregate_id END) as sessions_started,
                    COUNT(DISTINCT CASE WHEN event_type = 'analytics_charge_stopped' THEN aggregate_id END) as sessions_completed,
                    COUNT(DISTINCT CASE WHEN event_type = 'analytics_wallet_credited' THEN aggregate_id END) as sessions_credited
                FROM analytics_events
                WHERE timestamp >= :start_date
            """), {
                "start_date": (datetime.utcnow() - timedelta(days=days)).isoformat()
            })
            
            row = funnel_result.fetchone()
            if not row:
                return {"error": "No data available"}
            
            sessions_started = row.sessions_started or 0
            sessions_completed = row.sessions_completed or 0
            sessions_credited = row.sessions_credited or 0
            
            # Calculate conversion rates
            completion_rate = (sessions_completed / sessions_started * 100) if sessions_started > 0 else 0
            credit_rate = (sessions_credited / sessions_completed * 100) if sessions_completed > 0 else 0
            
            return {
                "sessions_started": sessions_started,
                "sessions_completed": sessions_completed,
                "sessions_credited": sessions_credited,
                "completion_rate": round(completion_rate, 2),
                "credit_rate": round(credit_rate, 2),
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error calculating conversion funnel: {e}")
            return {"error": str(e)}
    
    async def get_green_hour_utilization(self, days: int = 7) -> Dict[str, Any]:
        """Get Green Hour utilization metrics"""
        try:
            db = next(get_db())
            
            # Get Green Hour utilization
            utilization_result = db.execute(text("""
                SELECT 
                    COUNT(DISTINCT CASE WHEN properties->>'has_active_window' = 'true' THEN aggregate_id END) as active_window_sessions,
                    COUNT(DISTINCT aggregate_id) as total_sessions,
                    AVG(CAST(properties->>'total_reward_usd' AS FLOAT)) as avg_reward
                FROM analytics_events
                WHERE event_type = 'analytics_charge_stopped'
                AND timestamp >= :start_date
            """), {
                "start_date": (datetime.utcnow() - timedelta(days=days)).isoformat()
            })
            
            row = utilization_result.fetchone()
            if not row:
                return {"error": "No data available"}
            
            active_window_sessions = row.active_window_sessions or 0
            total_sessions = row.total_sessions or 0
            avg_reward = row.avg_reward or 0.0
            
            utilization_rate = (active_window_sessions / total_sessions * 100) if total_sessions > 0 else 0
            
            return {
                "active_window_sessions": active_window_sessions,
                "total_sessions": total_sessions,
                "utilization_rate": round(utilization_rate, 2),
                "avg_reward_usd": round(avg_reward, 2),
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error calculating Green Hour utilization: {e}")
            return {"error": str(e)}
    
    async def get_streak_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get charging streak metrics"""
        try:
            db = next(get_db())
            
            # Get streak data
            streak_result = db.execute(text("""
                SELECT 
                    COUNT(DISTINCT aggregate_id) as unique_users,
                    COUNT(*) as total_sessions,
                    AVG(CAST(properties->>'total_reward_usd' AS FLOAT)) as avg_reward
                FROM analytics_events
                WHERE event_type = 'analytics_charge_stopped'
                AND timestamp >= :start_date
            """), {
                "start_date": (datetime.utcnow() - timedelta(days=days)).isoformat()
            })
            
            row = streak_result.fetchone()
            if not row:
                return {"error": "No data available"}
            
            unique_users = row.unique_users or 0
            total_sessions = row.total_sessions or 0
            avg_reward = row.avg_reward or 0.0
            
            # Calculate sessions per user
            sessions_per_user = (total_sessions / unique_users) if unique_users > 0 else 0
            
            return {
                "unique_users": unique_users,
                "total_sessions": total_sessions,
                "sessions_per_user": round(sessions_per_user, 2),
                "avg_reward_usd": round(avg_reward, 2),
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error calculating streak metrics: {e}")
            return {"error": str(e)}
    
    async def get_revenue_metrics(self, days: int = 7) -> Dict[str, Any]:
        """Get revenue metrics"""
        try:
            db = next(get_db())
            
            # Get revenue data
            revenue_result = db.execute(text("""
                SELECT 
                    SUM(CAST(properties->>'total_reward_usd' AS FLOAT)) as total_rewards,
                    COUNT(*) as total_sessions,
                    AVG(CAST(properties->>'total_reward_usd' AS FLOAT)) as avg_reward
                FROM analytics_events
                WHERE event_type = 'analytics_charge_stopped'
                AND timestamp >= :start_date
            """), {
                "start_date": (datetime.utcnow() - timedelta(days=days)).isoformat()
            })
            
            row = revenue_result.fetchone()
            if not row:
                return {"error": "No data available"}
            
            total_rewards = row.total_rewards or 0.0
            total_sessions = row.total_sessions or 0
            avg_reward = row.avg_reward or 0.0
            
            return {
                "total_rewards_usd": round(total_rewards, 2),
                "total_sessions": total_sessions,
                "avg_reward_usd": round(avg_reward, 2),
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error calculating revenue metrics: {e}")
            return {"error": str(e)}
    
    async def get_all_kpis(self, days: int = 7) -> Dict[str, Any]:
        """Get all KPIs"""
        try:
            conversion_funnel = await self.get_conversion_funnel(days)
            green_hour_utilization = await self.get_green_hour_utilization(days)
            streak_metrics = await self.get_streak_metrics(days)
            revenue_metrics = await self.get_revenue_metrics(days)
            
            return {
                "conversion_funnel": conversion_funnel,
                "green_hour_utilization": green_hour_utilization,
                "streak_metrics": streak_metrics,
                "revenue_metrics": revenue_metrics,
                "generated_at": datetime.utcnow().isoformat(),
                "region": self.region
            }
            
        except Exception as e:
            logger.error(f"Error getting all KPIs: {e}")
            return {"error": str(e)}

# Global KPI calculator
kpi_calculator = KPICalculator()
