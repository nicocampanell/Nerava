from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.discover import search as discover_search

router = APIRouter(prefix="/v1/discover", tags=["discover"])


class DiscoverRequest(BaseModel):
    lat: float
    lng: float
    radius_m: int = Field(default=1500, ge=100, le=10000)
    categories: Optional[List[str]] = None
    limit: int = Field(default=25, ge=1, le=50)
    cursor: Optional[str] = None
    fields: Optional[List[str]] = None


@router.post("")
def discover(req: DiscoverRequest):
    items, next_cursor, total = discover_search(
        lat=req.lat,
        lng=req.lng,
        radius_m=req.radius_m,
        categories=req.categories,
        limit=req.limit,
        cursor=req.cursor,
        fields=req.fields,
    )
    return {"items": items, "next_cursor": next_cursor, "total_estimate": total}


