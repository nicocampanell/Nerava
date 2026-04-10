from fastapi import APIRouter

from app.domains.schemas import AffiliateNotifyReq, TrackClickReq
from app.services.affiliate import build_click, ingest_conversion

router = APIRouter(prefix="/v1/affiliate", tags=["affiliate"])


@router.post("/track_click")
def track_click(req: TrackClickReq):
    return build_click(req.user_id, req.merchant_id, req.offer_id)


@router.post("/notify")
def notify(payload: AffiliateNotifyReq):
    return ingest_conversion(payload)


