"""
PostHog Events Router
Provides API endpoints for manually triggering PostHog events via Swagger.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.analytics import get_analytics_client

router = APIRouter(prefix="/v1/posthog", tags=["PostHog Events"])


class GeofenceChargerEnteredRequest(BaseModel):
    """Request model for charger geofence entry event"""
    distinct_id: str = Field(..., description="User/distinct identifier")
    charger_id: str = Field(..., description="Charger ID")
    charger_name: Optional[str] = Field(None, description="Charger name")
    charger_address: Optional[str] = Field(None, description="Charger address")
    lat: float = Field(..., description="Latitude")
    lng: float = Field(..., description="Longitude")
    accuracy_m: Optional[float] = Field(None, description="Location accuracy in meters")
    radius_m: Optional[int] = Field(400, description="Geofence radius in meters")
    distance_to_charger_m: Optional[float] = Field(None, description="Distance to charger in meters")
    user_id: Optional[str] = Field(None, description="User ID")
    session_id: Optional[str] = Field(None, description="Session ID")
    properties: Optional[Dict[str, Any]] = Field(None, description="Additional event properties")

    class Config:
        json_schema_extra = {
            "example": {
                "distinct_id": "driver:test_driver_123",
                "charger_id": "canyon_ridge_tesla",
                "charger_name": "Tesla Supercharger - Canyon Ridge",
                "charger_address": "500 W Canyon Ridge Dr, Austin, TX 78753",
                "lat": 30.4037865,
                "lng": -97.6730044,
                "accuracy_m": 10.0,
                "radius_m": 400,
                "distance_to_charger_m": 15.0
            }
        }


class GeofenceMerchantEnteredRequest(BaseModel):
    """Request model for merchant geofence entry event"""
    distinct_id: str = Field(..., description="User/distinct identifier")
    merchant_id: str = Field(..., description="Merchant ID")
    merchant_name: Optional[str] = Field(None, description="Merchant name")
    merchant_address: Optional[str] = Field(None, description="Merchant address")
    charger_id: Optional[str] = Field(None, description="Charger ID")
    lat: float = Field(..., description="Latitude")
    lng: float = Field(..., description="Longitude")
    accuracy_m: Optional[float] = Field(None, description="Location accuracy in meters")
    radius_m: Optional[int] = Field(40, description="Geofence radius in meters")
    distance_to_merchant_m: Optional[float] = Field(None, description="Distance to merchant in meters")
    distance_to_charger_m: Optional[float] = Field(None, description="Distance to charger in meters")
    user_id: Optional[str] = Field(None, description="User ID")
    session_id: Optional[str] = Field(None, description="Session ID")
    properties: Optional[Dict[str, Any]] = Field(None, description="Additional event properties")

    class Config:
        json_schema_extra = {
            "example": {
                "distinct_id": "driver:test_driver_123",
                "merchant_id": "asadas_grill_canyon_ridge",
                "merchant_name": "Asadas Grill",
                "merchant_address": "501 W Canyon Ridge Dr, Austin, TX 78753",
                "charger_id": "canyon_ridge_tesla",
                "lat": 30.4028469,
                "lng": -97.6718938,
                "accuracy_m": 8.0,
                "radius_m": 40,
                "distance_to_merchant_m": 5.0,
                "distance_to_charger_m": 149.0
            }
        }


class GeofenceMerchantExitedRequest(BaseModel):
    """Request model for merchant geofence exit event"""
    distinct_id: str = Field(..., description="User/distinct identifier")
    merchant_id: str = Field(..., description="Merchant ID")
    merchant_name: Optional[str] = Field(None, description="Merchant name")
    merchant_address: Optional[str] = Field(None, description="Merchant address")
    charger_id: Optional[str] = Field(None, description="Charger ID")
    lat: float = Field(..., description="Latitude")
    lng: float = Field(..., description="Longitude")
    accuracy_m: Optional[float] = Field(None, description="Location accuracy in meters")
    radius_m: Optional[int] = Field(40, description="Geofence radius in meters")
    distance_to_merchant_m: Optional[float] = Field(None, description="Distance to merchant in meters")
    distance_to_charger_m: Optional[float] = Field(None, description="Distance to charger in meters")
    user_id: Optional[str] = Field(None, description="User ID")
    session_id: Optional[str] = Field(None, description="Session ID")
    properties: Optional[Dict[str, Any]] = Field(None, description="Additional event properties")

    class Config:
        json_schema_extra = {
            "example": {
                "distinct_id": "driver:test_driver_123",
                "merchant_id": "asadas_grill_canyon_ridge",
                "merchant_name": "Asadas Grill",
                "merchant_address": "501 W Canyon Ridge Dr, Austin, TX 78753",
                "charger_id": "canyon_ridge_tesla",
                "lat": 30.4037969,
                "lng": -97.6709438,
                "accuracy_m": 12.0,
                "radius_m": 40,
                "distance_to_merchant_m": 120.0,
                "distance_to_charger_m": 150.0
            }
        }


class PostHogEventResponse(BaseModel):
    """Response model for PostHog event endpoints"""
    ok: bool
    event: str
    distinct_id: str
    message: str
    note: Optional[str] = None


@router.post(
    "/geofence/charger/entered",
    response_model=PostHogEventResponse,
    summary="Trigger charger geofence entry event",
    description="""
    Manually trigger a PostHog event when a user enters a charger geofence radius.
    
    This event is typically fired by the iOS app when the user enters the charger intent zone.
    Includes geo coordinates for location-based analytics.
    """
)
async def trigger_charger_geofence_entered(request: GeofenceChargerEnteredRequest):
    """Trigger PostHog event: ios.geofence.charger.entered"""
    analytics = get_analytics_client()
    
    if not analytics.enabled:
        raise HTTPException(
            status_code=400,
            detail="PostHog not configured (POSTHOG_KEY missing or ANALYTICS_ENABLED=false)"
        )
    
    # Build properties
    properties = {
        "charger_id": request.charger_id,
        "source": "api_manual",
        **(request.properties or {})
    }
    
    if request.charger_name:
        properties["charger_name"] = request.charger_name
    if request.charger_address:
        properties["charger_address"] = request.charger_address
    if request.radius_m:
        properties["radius_m"] = request.radius_m
    if request.distance_to_charger_m is not None:
        properties["distance_to_charger_m"] = request.distance_to_charger_m
    
    # Capture event with geo coordinates
    analytics.capture(
        event="ios.geofence.charger.entered",
        distinct_id=request.distinct_id,
        user_id=request.user_id,
        charger_id=request.charger_id,
        session_id=request.session_id,
        lat=request.lat,
        lng=request.lng,
        accuracy_m=request.accuracy_m,
        properties=properties
    )
    
    return PostHogEventResponse(
        ok=True,
        event="ios.geofence.charger.entered",
        distinct_id=request.distinct_id,
        message="Event sent to PostHog",
        note="Check PostHog dashboard in ~30 seconds"
    )


@router.post(
    "/geofence/merchant/entered",
    response_model=PostHogEventResponse,
    summary="Trigger merchant geofence entry event",
    description="""
    Manually trigger a PostHog event when a user enters a merchant geofence radius.
    
    This event is typically fired by the iOS app when the user enters the merchant unlock zone.
    Includes geo coordinates for location-based analytics.
    """
)
async def trigger_merchant_geofence_entered(request: GeofenceMerchantEnteredRequest):
    """Trigger PostHog event: ios.geofence.merchant.entered"""
    analytics = get_analytics_client()
    
    if not analytics.enabled:
        raise HTTPException(
            status_code=400,
            detail="PostHog not configured (POSTHOG_KEY missing or ANALYTICS_ENABLED=false)"
        )
    
    # Build properties
    properties = {
        "merchant_id": request.merchant_id,
        "source": "api_manual",
        **(request.properties or {})
    }
    
    if request.merchant_name:
        properties["merchant_name"] = request.merchant_name
    if request.merchant_address:
        properties["merchant_address"] = request.merchant_address
    if request.radius_m:
        properties["radius_m"] = request.radius_m
    if request.distance_to_merchant_m is not None:
        properties["distance_to_merchant_m"] = request.distance_to_merchant_m
    if request.distance_to_charger_m is not None:
        properties["distance_to_charger_m"] = request.distance_to_charger_m
    
    # Capture event with geo coordinates
    analytics.capture(
        event="ios.geofence.merchant.entered",
        distinct_id=request.distinct_id,
        user_id=request.user_id,
        merchant_id=request.merchant_id,
        charger_id=request.charger_id,
        session_id=request.session_id,
        lat=request.lat,
        lng=request.lng,
        accuracy_m=request.accuracy_m,
        properties=properties
    )
    
    return PostHogEventResponse(
        ok=True,
        event="ios.geofence.merchant.entered",
        distinct_id=request.distinct_id,
        message="Event sent to PostHog",
        note="Check PostHog dashboard in ~30 seconds"
    )


@router.post(
    "/geofence/merchant/exited",
    response_model=PostHogEventResponse,
    summary="Trigger merchant geofence exit event",
    description="""
    Manually trigger a PostHog event when a user exits a merchant geofence radius.
    
    This event is typically fired by the iOS app when the user leaves the merchant unlock zone.
    Includes geo coordinates for location-based analytics.
    """
)
async def trigger_merchant_geofence_exited(request: GeofenceMerchantExitedRequest):
    """Trigger PostHog event: ios.geofence.merchant.exited"""
    analytics = get_analytics_client()
    
    if not analytics.enabled:
        raise HTTPException(
            status_code=400,
            detail="PostHog not configured (POSTHOG_KEY missing or ANALYTICS_ENABLED=false)"
        )
    
    # Build properties
    properties = {
        "merchant_id": request.merchant_id,
        "source": "api_manual",
        **(request.properties or {})
    }
    
    if request.merchant_name:
        properties["merchant_name"] = request.merchant_name
    if request.merchant_address:
        properties["merchant_address"] = request.merchant_address
    if request.radius_m:
        properties["radius_m"] = request.radius_m
    if request.distance_to_merchant_m is not None:
        properties["distance_to_merchant_m"] = request.distance_to_merchant_m
    if request.distance_to_charger_m is not None:
        properties["distance_to_charger_m"] = request.distance_to_charger_m
    
    # Capture event with geo coordinates
    analytics.capture(
        event="ios.geofence.merchant.exited",
        distinct_id=request.distinct_id,
        user_id=request.user_id,
        merchant_id=request.merchant_id,
        charger_id=request.charger_id,
        session_id=request.session_id,
        lat=request.lat,
        lng=request.lng,
        accuracy_m=request.accuracy_m,
        properties=properties
    )
    
    return PostHogEventResponse(
        ok=True,
        event="ios.geofence.merchant.exited",
        distinct_id=request.distinct_id,
        message="Event sent to PostHog",
        note="Check PostHog dashboard in ~30 seconds"
    )
