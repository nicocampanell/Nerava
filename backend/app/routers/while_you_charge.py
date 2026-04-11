"""
While You Charge search endpoint

Test:
curl -X POST http://localhost:8001/v1/while_you_charge/search \
  -H "Content-Type: application/json" \
  -d '{"user_lat": 30.2672, "user_lng": -97.7431, "query": "coffee"}'
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.while_you_charge import (
    find_and_link_merchants,
    find_chargers_near,
    normalize_query_to_category,
    rank_merchants,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/while_you_charge", tags=["while_you_charge"])


class SearchRequest(BaseModel):
    user_lat: float
    user_lng: float
    query: str = "coffee"
    max_drive_minutes: int = 15
    max_walk_minutes: int = 10
    limit_merchants: int = 10


class ChargerResponse(BaseModel):
    id: str
    lat: float
    lng: float
    network_name: Optional[str] = None
    logo_url: Optional[str] = None
    name: str


class RecommendedMerchantResponse(BaseModel):
    id: str
    name: str
    logo_url: Optional[str] = None
    nova_reward: int
    walk_minutes: int


class SearchResponse(BaseModel):
    chargers: List[ChargerResponse]
    recommended_merchants: List[RecommendedMerchantResponse]


@router.post("/search", response_model=SearchResponse)
async def search_while_you_charge(
    request: SearchRequest,
    db: Session = Depends(get_db)
):
    """
    Search for chargers and nearby merchants based on user location and query.
    """
    try:
        logger.info(f"Search request: lat={request.user_lat}, lng={request.user_lng}, query='{request.query}'")
        
        # Normalize query
        category, merchant_name = normalize_query_to_category(request.query)
        logger.info(f"Normalized query: category={category}, merchant_name={merchant_name}")
        
        # Find chargers near user
        chargers = await find_chargers_near(
            db=db,
            user_lat=request.user_lat,
            user_lng=request.user_lng,
            radius_m=request.max_drive_minutes * 1000,  # Rough conversion
            max_drive_minutes=request.max_drive_minutes
        )
        
        if not chargers:
            logger.warning(f"No chargers found near ({request.user_lat}, {request.user_lng})")
            return SearchResponse(chargers=[], recommended_merchants=[])
        
        # Find and link merchants
        merchants = await find_and_link_merchants(
            db=db,
            chargers=chargers,
            category=category,
            merchant_name=merchant_name,
            max_walk_minutes=request.max_walk_minutes
        )
        
        if not merchants:
            logger.warning(
                "No merchants found for %d chargers (category=%s, name=%s). "
                "Check logs for: [PLACES] status and results count, [WhileYouCharge] filtering details",
                len(chargers),
                category,
                merchant_name
            )
            # Return chargers but no merchants
            charger_responses = [
                ChargerResponse(
                    id=c.id,
                    lat=c.lat,
                    lng=c.lng,
                    network_name=c.network_name,
                    logo_url=c.logo_url,
                    name=c.name
                )
                for c in chargers[:10]
            ]
            return SearchResponse(
                chargers=charger_responses,
                recommended_merchants=[]
            )
        
        # Rank merchants
        ranked = rank_merchants(
            db=db,
            merchants=merchants,
            chargers=chargers,
            user_lat=request.user_lat,
            user_lng=request.user_lng
        )
        
        # Get unique chargers that have ranked merchants
        charger_ids_with_merchants = {r["charger"].id for r in ranked if r["charger"]}
        relevant_chargers = [c for c in chargers if c.id in charger_ids_with_merchants]
        
        # Build responses
        charger_responses = [
            ChargerResponse(
                id=c.id,
                lat=c.lat,
                lng=c.lng,
                network_name=c.network_name,
                logo_url=c.logo_url or _get_network_logo_url(c.network_name),
                name=c.name
            )
            for c in relevant_chargers[:20]
        ]
        
        merchant_responses = [
            RecommendedMerchantResponse(
                id=r["merchant"].id,
                name=r["merchant"].name,
                logo_url=r["merchant"].logo_url,
                nova_reward=r["nova_reward"],
                walk_minutes=r["walk_time_min"]
            )
            for r in ranked[:request.limit_merchants]
        ]
        
        logger.info(f"Search complete: {len(charger_responses)} chargers, {len(merchant_responses)} merchants")
        return SearchResponse(
            chargers=charger_responses,
            recommended_merchants=merchant_responses
        )
    
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


def _get_network_logo_url(network_name: Optional[str]) -> Optional[str]:
    """Get logo URL for network"""
    if not network_name:
        return None
    
    # In production, these would be hosted on your CDN
    network_logos = {
        "Tesla": "https://logo.clearbit.com/tesla.com",
        "ChargePoint": "https://logo.clearbit.com/chargepoint.com",
        "EVgo": "https://logo.clearbit.com/evgo.com",
        "Electrify America": "https://logo.clearbit.com/electrifyamerica.com",
        "Blink": "https://logo.clearbit.com/blinkcharging.com",
        "Flo": "https://logo.clearbit.com/flo.com",
        "Volta": "https://logo.clearbit.com/volta.com"
    }
    
    return network_logos.get(network_name)

