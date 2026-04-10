import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.while_you_charge import get_domain_hub_view

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hubs"])

@router.get("/recommended")
def recommended():
    # simple static recommendation; front-end still has its own fallback
    return {"id":"fallback_hub","name":"Nerava Hub","lat":30.2672,"lng":-97.7431}

@router.get("/recommend")
def recommend():
    # Alias for /recommended to match frontend expectations
    return {"id":"fallback_hub","name":"Nerava Hub","lat":30.2672,"lng":-97.7431}

@router.get("/nearby")
def hubs_nearby():
    """
    Return multiple stations near the user. Each item includes id, name, lat, lng, network, and optional eta_min.
    """
    return {
        "origin": {"lat": 30.268, "lng": -97.742},  # Austin downtown example
        "stations": [
            {"id": "stn-1", "name": "The Quarters on Campus", "lat": 30.2895, "lng": -97.7420, "network": "EVgo", "eta_min": 9},
            {"id": "stn-2", "name": "West Campus Garage", "lat": 30.2847, "lng": -97.7429, "network": "ChargePoint", "eta_min": 7},
            {"id": "stn-3", "name": "Seaholm District", "lat": 30.2680, "lng": -97.7540, "network": "Tesla", "eta_min": 12},
            {"id": "stn-4", "name": "Rainey Street Hub", "lat": 30.2570, "lng": -97.7380, "network": "Flo", "eta_min": 11},
        ]
    }


@router.get("/domain")
def get_domain_hub(db: Session = Depends(get_db)):
    """
    Get Domain hub view with chargers and recommended merchants.
    
    Returns:
        - hub_id: Domain hub identifier
        - hub_name: Human-readable hub name
        - chargers: List of chargers in the Domain hub
        - merchants: List of recommended merchants near Domain chargers
    
    Example:
        GET /v1/hubs/domain
    """
    try:
        logger.info("[DomainHub] GET /v1/hubs/domain request")
        hub_data = get_domain_hub_view(db)
        logger.info(f"[DomainHub] Returning {len(hub_data['chargers'])} chargers, {len(hub_data['merchants'])} merchants")
        return hub_data
    except Exception as e:
        logger.error(f"[DomainHub] Error fetching Domain hub: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch Domain hub: {str(e)}")