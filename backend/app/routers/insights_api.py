from fastapi import APIRouter

from app.db import SessionLocal
from app.domains.schemas import InsightsReq
from app.services import merchant_analytics
from app.services.events2 import event_insights

router = APIRouter(prefix="/v1/insights", tags=["insights"])


@router.post("/events")
def events_insights(req: InsightsReq):
    return event_insights(req.event_id, req.from_ts, req.to_ts)


@router.post("/merchants")
def merchants_insights(req: InsightsReq):
    if not req.merchant_id:
        return {"error": "merchant_id required"}
    db = SessionLocal()
    try:
        return merchant_analytics.merchant_summary(db, req.merchant_id, req.from_ts, req.to_ts)
    finally:
        db.close()


