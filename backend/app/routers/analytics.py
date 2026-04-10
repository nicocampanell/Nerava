"""
Analytics and KPI endpoints
"""

from fastapi import APIRouter, HTTPException, Query

from app.analytics.batch_writer import analytics_batch_writer
from app.analytics.kpis import kpi_calculator

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

@router.get("/kpis")
async def get_kpis(days: int = Query(7, ge=1, le=30, description="Number of days to analyze")):
    """Get all KPIs for the specified period"""
    try:
        kpis = await kpi_calculator.get_all_kpis(days)
        return kpis
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating KPIs: {str(e)}")

@router.get("/kpis/conversion-funnel")
async def get_conversion_funnel(days: int = Query(7, ge=1, le=30)):
    """Get conversion funnel metrics"""
    try:
        funnel = await kpi_calculator.get_conversion_funnel(days)
        return funnel
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating conversion funnel: {str(e)}")

@router.get("/kpis/green-hour-utilization")
async def get_green_hour_utilization(days: int = Query(7, ge=1, le=30)):
    """Get Green Hour utilization metrics"""
    try:
        utilization = await kpi_calculator.get_green_hour_utilization(days)
        return utilization
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating Green Hour utilization: {str(e)}")

@router.get("/kpis/streak-metrics")
async def get_streak_metrics(days: int = Query(30, ge=1, le=90)):
    """Get charging streak metrics"""
    try:
        streaks = await kpi_calculator.get_streak_metrics(days)
        return streaks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating streak metrics: {str(e)}")

@router.get("/kpis/revenue-metrics")
async def get_revenue_metrics(days: int = Query(7, ge=1, le=30)):
    """Get revenue metrics"""
    try:
        revenue = await kpi_calculator.get_revenue_metrics(days)
        return revenue
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating revenue metrics: {str(e)}")

@router.get("/stats")
async def get_analytics_stats():
    """Get analytics system statistics"""
    try:
        stats = await analytics_batch_writer.get_analytics_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting analytics stats: {str(e)}")

@router.get("/health")
async def get_analytics_health():
    """Get analytics system health"""
    try:
        stats = await analytics_batch_writer.get_analytics_stats()
        return {
            "status": "healthy" if stats.get("running", False) else "unhealthy",
            "batch_size": stats.get("batch_size", 0),
            "total_events": stats.get("total_events", 0),
            "events_by_type": stats.get("events_by_type", {}),
            "events_by_region": stats.get("events_by_region", {})
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
