"""
Bootstrap endpoint for seeding Charge Party cluster
"""
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email_sender import get_email_sender
from app.db import get_db
from app.models import User
from app.models.domain import DomainMerchant
from app.models.merchant_account import MerchantAccount
from app.models.while_you_charge import (
    Charger,
    ChargerCluster,
    ChargerMerchant,
    Merchant,
)
from app.routers.auth_domain import create_magic_link_token
from app.services.google_places_new import place_details, search_nearby
from app.services.qr_service import create_or_get_merchant_qr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/bootstrap", tags=["bootstrap"])


def verify_bootstrap_key(x_bootstrap_key: Optional[str] = Header(None, alias="X-Bootstrap-Key")) -> str:
    """Verify bootstrap key from header"""
    # Accept BOOTSTRAP_KEY or fall back to JWT_SECRET for environments without BOOTSTRAP_KEY
    bootstrap_key = os.getenv("BOOTSTRAP_KEY") or os.getenv("JWT_SECRET")
    if not bootstrap_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOOTSTRAP_KEY or JWT_SECRET must be configured"
        )

    if not x_bootstrap_key or x_bootstrap_key != bootstrap_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Bootstrap-Key header"
        )

    return x_bootstrap_key


class AsadasPartyRequest(BaseModel):
    charger_address: str
    charger_lat: float
    charger_lng: float
    charger_radius_m: int = 400
    merchant_radius_m: int = 40
    primary_merchant: dict
    seed_limit: int = 25


class AsadasPartyResponse(BaseModel):
    ok: bool
    cluster_id: str
    primary_merchant: dict
    seeded_merchants_count: int
    magic_link_sent: bool


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters"""
    import math
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


@router.post("/asadas_party", response_model=AsadasPartyResponse)
async def bootstrap_asadas_party(
    request: AsadasPartyRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(verify_bootstrap_key)
):
    """
    Bootstrap Asadas party cluster:
    - Creates/updates cluster record
    - Upserts primary merchant (Asadas) with Google Places data
    - Creates/updates merchant admin user (Hector)
    - Links merchant to user
    - Generates QR token
    - Sends magic link email
    - Seeds nearby merchants via Google Places
    """
    try:
        # 1. Upsert cluster record
        cluster = db.query(ChargerCluster).filter(ChargerCluster.name == "asadas_party").first()
        if not cluster:
            cluster = ChargerCluster(
                id=str(uuid.uuid4()),
                name="asadas_party",
                charger_lat=request.charger_lat,
                charger_lng=request.charger_lng,
                charger_radius_m=request.charger_radius_m,
                merchant_radius_m=request.merchant_radius_m
            )
            db.add(cluster)
        else:
            cluster.charger_lat = request.charger_lat
            cluster.charger_lng = request.charger_lng
            cluster.charger_radius_m = request.charger_radius_m
            cluster.merchant_radius_m = request.merchant_radius_m
            cluster.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(cluster)
        
        # 2. Upsert charger (if not exists)
        charger_id = "asadas_party_charger"
        charger = db.query(Charger).filter(Charger.id == charger_id).first()
        if not charger:
            charger = Charger(
                id=charger_id,
                name="Asadas Party Charger",
                lat=request.charger_lat,
                lng=request.charger_lng,
                address=request.charger_address,
                status="available"
            )
            db.add(charger)
            db.commit()
            db.refresh(charger)
        
        cluster.charger_id = charger.id
        db.commit()
        
        # 3. Upsert primary merchant (Asadas) with Google Places lookup
        primary_data = request.primary_merchant
        primary_name = primary_data.get("name", "Asadas Grill")
        primary_address = primary_data.get("address", request.charger_address)
        primary_email = primary_data.get("email")
        primary_phone = primary_data.get("phone")
        
        # Search for Asadas via Google Places
        places_results = await search_nearby(
            lat=request.charger_lat,
            lng=request.charger_lng,
            radius_m=100,  # Small radius for exact match
            max_results=5
        )
        
        # Try to find Asadas by name match
        asadas_place = None
        for place in places_results:
            if "asadas" in place.get("name", "").lower():
                asadas_place = place
                break
        
        # If not found, get place details by searching with name
        if not asadas_place:
            from app.services.google_places_new import search_text
            text_results = await search_text(
                query=f"{primary_name} {primary_address}",
                location_bias={"lat": request.charger_lat, "lng": request.charger_lng},
                max_results=3
            )
            if text_results:
                asadas_place = text_results[0]
        
        # Try to load local place_details.json first (for Asadas)
        place_details_data = None
        # Path: backend/app/routers/bootstrap.py -> backend/app/routers -> backend/app -> backend -> project_root
        place_details_path = Path(__file__).parent.parent.parent.parent / "merchant_photos_asadas_grill" / "place_details.json"
        if place_details_path.exists():
            try:
                with open(place_details_path) as f:
                    place_details_data = json.load(f)
                logger.info(f"Loaded place_details.json from {place_details_path}")
            except Exception as e:
                logger.warning(f"Failed to load place_details.json: {e}")
        
        # Fallback to Google Places API if local file not available
        if not place_details_data and asadas_place and asadas_place.get("place_id"):
            place_details_data = await place_details(asadas_place["place_id"])
        
        # Create or update merchant
        merchant_id = "m_asadas_party"
        merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
        
        if not merchant:
            merchant = Merchant(
                id=merchant_id,
                name=primary_name,
                lat=request.charger_lat,  # Use charger location as fallback
                lng=request.charger_lng,
                address=primary_address,
                phone=primary_phone
            )
            db.add(merchant)
        else:
            merchant.name = primary_name
            merchant.address = primary_address
            merchant.phone = primary_phone
        
        # Enrich with place_details data (from local JSON or Google Places API)
        if place_details_data:
            display_name = place_details_data.get("displayName", {})
            if isinstance(display_name, dict):
                merchant.name = display_name.get("text", merchant.name)
            
            # Handle location (from local JSON or Google Places API format)
            location = place_details_data.get("location", {})
            if location:
                if isinstance(location, dict):
                    merchant.lat = location.get("latitude") or location.get("lat", merchant.lat)
                    merchant.lng = location.get("longitude") or location.get("lng", merchant.lng)
            
            # Set place_id (handle both formats)
            place_id = place_details_data.get("place_id") or place_details_data.get("id", "")
            if place_id:
                merchant.place_id = place_id.replace("places/", "")
                merchant.external_id = merchant.place_id  # Also set external_id for compatibility
            
            # Get photos - use local static files if available, otherwise Google Places API
            photo_urls_list = []
            photos_count = place_details_data.get("photos_count", 0)
            
            # Use local static photos if available
            if photos_count > 0:
                base_url = os.getenv("PUBLIC_WEB_BASE_URL", "http://localhost:8001")
                # Generate photo URLs pointing to static mount
                for i in range(1, min(photos_count + 1, 11)):  # Support up to 10 photos
                    photo_filename = f"asadas_grill_{i:02d}.jpg"
                    photo_url = f"{base_url}/static/merchant_photos_asadas_grill/{photo_filename}"
                    photo_urls_list.append(photo_url)
            
            # Fallback to Google Places API photos if no local photos
            if not photo_urls_list:
                photos = place_details_data.get("photos", [])
                if photos:
                    from app.services.google_places_new import get_photo_url
                    max_photos = min(3, len(photos))
                    photo_maxwidth = int(os.getenv("GOOGLE_PLACES_PHOTO_MAXWIDTH", "800"))
                    
                    for i in range(max_photos):
                        photo = photos[i]
                        photo_ref = photo.get("name", "").replace("places/", "").split("/photos/")[-1]
                        if photo_ref:
                            try:
                                photo_url = await get_photo_url(photo_ref, max_width=photo_maxwidth)
                                if photo_url:
                                    photo_urls_list.append(photo_url)
                            except Exception as e:
                                logger.warning(f"Failed to get photo URL {i+1} for {merchant.name}: {e}")
            
            if photo_urls_list:
                merchant.primary_photo_url = photo_urls_list[0]
                merchant.photo_urls = photo_urls_list
            else:
                merchant.photo_urls = []  # Empty array, not null
            
            # Get description (from local JSON or Google Places API)
            description = place_details_data.get("description")
            if description:
                merchant.description = description
            else:
                editorial_summary = place_details_data.get("editorialSummary")
                if editorial_summary and isinstance(editorial_summary, dict):
                    merchant.description = editorial_summary.get("text")
                elif not merchant.description:
                    # Fallback to types if no editorial summary
                    types = place_details_data.get("types", [])
                    if types:
                        type_descriptions = {
                            "restaurant": "Restaurant",
                            "cafe": "Cafe",
                            "meal_takeaway": "Takeout",
                            "bar": "Bar",
                            "gym": "Gym",
                            "supermarket": "Supermarket",
                            "shopping_mall": "Shopping"
                        }
                        for t in types:
                            if t in type_descriptions:
                                merchant.description = type_descriptions[t]
                                break
            
            # Get rating and other details
            merchant.rating = place_details_data.get("rating")
            merchant.user_rating_count = place_details_data.get("user_rating_count") or place_details_data.get("userRatingCount")
            merchant.price_level = place_details_data.get("price_level") or place_details_data.get("priceLevel")
            
            # Get formatted address
            formatted_address = place_details_data.get("formattedAddress") or place_details_data.get("address")
            if formatted_address:
                merchant.address = formatted_address
        
        db.commit()
        db.refresh(merchant)
        
        # 4. Create/upsert merchant admin user (Hector)
        if not primary_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Primary merchant email is required"
            )
        
        user = db.query(User).filter(User.email == primary_email).first()
        if not user:
            from app.models import User as UserModel
            user = UserModel(
                email=primary_email,
                password_hash="magic-link-user-no-password",  # Placeholder for magic-link only
                is_active=True,
                role_flags="merchant",
                auth_provider="local"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created merchant admin user {user.id} for {primary_email}")
        
        # 5. Link merchant to user via MerchantAccount
        merchant_account = db.query(MerchantAccount).filter(
            MerchantAccount.owner_user_id == user.id
        ).first()
        
        if not merchant_account:
            merchant_account = MerchantAccount(
                id=str(uuid.uuid4()),
                owner_user_id=user.id
            )
            db.add(merchant_account)
            db.commit()
            db.refresh(merchant_account)
        
        # Create location claim for merchant
        if merchant.place_id:
            from app.models.merchant_account import MerchantLocationClaim
            location_claim = db.query(MerchantLocationClaim).filter(
                MerchantLocationClaim.merchant_account_id == merchant_account.id,
                MerchantLocationClaim.place_id == merchant.place_id
            ).first()
            
            if not location_claim:
                location_claim = MerchantLocationClaim(
                    id=str(uuid.uuid4()),
                    merchant_account_id=merchant_account.id,
                    place_id=merchant.place_id,
                    status="CLAIMED"
                )
                db.add(location_claim)
                db.commit()
        
        # 6. Generate QR token (use DomainMerchant for QR token storage)
        # Check if DomainMerchant exists, create if not
        # DomainMerchant.id is UUID, so we need to find by name or create new
        domain_merchant = db.query(DomainMerchant).filter(
            DomainMerchant.name == merchant.name,
            DomainMerchant.zone_slug == "asadas_party"
        ).first()
        
        if not domain_merchant:
            domain_merchant = DomainMerchant(
                id=str(uuid.uuid4()),  # Generate UUID for DomainMerchant
                name=merchant.name,
                lat=merchant.lat,
                lng=merchant.lng,
                addr_line1=merchant.address,
                zone_slug="asadas_party",
                status="active",  # Set status to active on create
                owner_user_id=user.id
            )
            db.add(domain_merchant)
            db.flush()  # Flush to get ID
        else:
            # Update existing domain merchant
            domain_merchant.name = merchant.name
            domain_merchant.lat = merchant.lat
            domain_merchant.lng = merchant.lng
            domain_merchant.addr_line1 = merchant.address
            domain_merchant.owner_user_id = user.id
            domain_merchant.status = "active"  # Ensure status is active on update
            db.flush()
        
        # Generate or get QR token (this will commit if new token is generated)
        qr_result = create_or_get_merchant_qr(db, domain_merchant)
        qr_token = qr_result["token"]
        
        # Ensure status is active and commit everything
        domain_merchant.status = "active"
        db.commit()
        db.refresh(domain_merchant)
        
        # Verify QR token is persisted and status is active
        if not domain_merchant.qr_token or domain_merchant.qr_token != qr_token:
            logger.error(f"QR token mismatch: expected {qr_token}, got {domain_merchant.qr_token}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist QR token"
            )
        if domain_merchant.status != "active":
            logger.error(f"Domain merchant {domain_merchant.id} status is {domain_merchant.status}, expected active")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Domain merchant status is not active"
            )
        
        logger.info(f"QR token generated for domain merchant {domain_merchant.id}: {qr_token[:8]}... (status: {domain_merchant.status})")
        
        # 7. Send magic link email
        magic_token = create_magic_link_token(user.id, primary_email)
        merchant_portal_url = os.getenv("MERCHANT_PORTAL_URL", "http://localhost/merchant/")
        magic_link_url = f"{merchant_portal_url}#/auth/magic?token={magic_token}"
        
        email_sender = get_email_sender()
        email_from = os.getenv("EMAIL_FROM", "hello@nerava.network")
        
        subject = "Welcome to Nerava Merchant Portal"
        body_text = f"""Welcome to Nerava Merchant Portal!

Click this link to sign in:
{magic_link_url}

This link expires in 15 minutes.

If you didn't request this link, you can safely ignore this email.
"""
        body_html = f"""<html>
<body>
<h2>Welcome to Nerava Merchant Portal!</h2>
<p>Click this link to sign in:</p>
<p><a href="{magic_link_url}">{magic_link_url}</a></p>
<p><small>This link expires in 15 minutes.</small></p>
<p><small>If you didn't request this link, you can safely ignore this email.</small></p>
</body>
</html>"""
        
        magic_link_sent = email_sender.send_email(
            to_email=primary_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html
        )
        
        # 8. Create ChargerMerchant link for primary merchant
        charger_merchant = db.query(ChargerMerchant).filter(
            ChargerMerchant.charger_id == charger.id,
            ChargerMerchant.merchant_id == merchant.id
        ).first()
        
        distance_to_charger = haversine_distance(
            request.charger_lat, request.charger_lng,
            merchant.lat, merchant.lng
        )
        
        if not charger_merchant:
            charger_merchant = ChargerMerchant(
                charger_id=charger.id,
                merchant_id=merchant.id,
                distance_m=distance_to_charger,
                walk_duration_s=int(round(distance_to_charger / 80 * 60)),  # Approximate walk time
                is_primary=True,
                exclusive_title="Free Beverage",
                exclusive_description="Show your pass for a free beverage during Happy Hour"
            )
            db.add(charger_merchant)
        else:
            charger_merchant.is_primary = True
            charger_merchant.distance_m = distance_to_charger
            charger_merchant.exclusive_title = "Free Beverage"
            charger_merchant.exclusive_description = "Show your pass for a free beverage during Happy Hour"
        
        db.commit()
        
        # 9. Seed nearby merchants via Google Places (with fallback to static list)
        seeded_count = 0
        min_merchants = 10  # Ensure at least 10 merchants
        
        # Try Google Places first
        nearby_places = []
        if settings.GOOGLE_PLACES_API_KEY:
            try:
                nearby_places = await search_nearby(
                    lat=request.charger_lat,
                    lng=request.charger_lng,
                    radius_m=800,  # Larger radius to get more merchants
                    max_results=request.seed_limit * 3  # Get more to filter
                )
                logger.info(f"Google Places returned {len(nearby_places)} places")
            except Exception as e:
                logger.warning(f"Google Places search failed: {e}, falling back to static list")
                nearby_places = []
        
        # Process Google Places results
        processed_place_ids = set()
        for place in nearby_places:
            if seeded_count >= request.seed_limit:
                break
                
            place_id = place.get("place_id")
            place_name = place.get("name")
            
            # Skip if this is Asadas (already added as primary) or already processed
            if not place_id or place_id in processed_place_ids:
                continue
            if place_id == merchant.place_id or "asadas" in place_name.lower():
                continue
            
            processed_place_ids.add(place_id)
            
            # Get full place details
            try:
                place_details_data = await place_details(place_id) if place_id else None
            except Exception as e:
                logger.warning(f"Failed to get place details for {place_id}: {e}")
                continue
            
            if not place_details_data:
                continue
            
            # Extract location
            location = place_details_data.get("location", {})
            place_lat = location.get("latitude")
            place_lng = location.get("longitude")
            
            if not place_lat or not place_lng:
                continue
            
            # Calculate distance to charger
            distance_to_charger_m = haversine_distance(
                request.charger_lat, request.charger_lng,
                place_lat, place_lng
            )
            
            # Create or update merchant
            seeded_merchant_id = f"m_seeded_{place_id[:20]}"  # Use first 20 chars of place_id
            seeded_merchant = db.query(Merchant).filter(Merchant.id == seeded_merchant_id).first()
            
            display_name = place_details_data.get("displayName", {})
            if isinstance(display_name, dict):
                place_name = display_name.get("text", place_name)
            
            if not seeded_merchant:
                seeded_merchant = Merchant(
                    id=seeded_merchant_id,
                    name=place_name,
                    lat=place_lat,
                    lng=place_lng,
                    place_id=place_id,
                    external_id=place_id
                )
                db.add(seeded_merchant)
            else:
                seeded_merchant.name = place_name
                seeded_merchant.lat = place_lat
                seeded_merchant.lng = place_lng
            
            # Enrich with Google Places data
            formatted_address = place_details_data.get("formattedAddress")
            if formatted_address:
                seeded_merchant.address = formatted_address
            
            # Get description
            editorial_summary = place_details_data.get("editorialSummary")
            if editorial_summary and isinstance(editorial_summary, dict):
                seeded_merchant.description = editorial_summary.get("text")
            
            # Get rating and price level
            seeded_merchant.rating = place_details_data.get("rating")
            seeded_merchant.user_rating_count = place_details_data.get("userRatingCount")
            seeded_merchant.price_level = place_details_data.get("priceLevel")
            
            # Get photos (at least first 3) - REQUIRED: skip merchant if no photos
            photos = place_details_data.get("photos", [])
            photo_urls_list = []
            if photos:
                from app.services.google_places_new import get_photo_url
                photo_maxwidth = int(os.getenv("GOOGLE_PLACES_PHOTO_MAXWIDTH", "800"))
                max_photos = min(3, len(photos))  # Get at least first 3 photos
                
                for i in range(max_photos):
                    photo = photos[i]
                    photo_ref = photo.get("name", "").replace("places/", "").split("/photos/")[-1]
                    if photo_ref:
                        try:
                            photo_url = await get_photo_url(photo_ref, max_width=photo_maxwidth)
                            if photo_url:
                                photo_urls_list.append(photo_url)
                        except Exception as e:
                            logger.warning(f"Failed to get photo URL {i+1} for {place_name}: {e}")
            
            # Skip merchant if no photos (per requirement: "if data or photos does not exist, do not show merchant")
            if not photo_urls_list:
                logger.info(f"Skipping merchant {place_name} (place_id: {place_id}) - no photos available")
                continue
            
            # Set photo_urls
            seeded_merchant.primary_photo_url = photo_urls_list[0] if photo_urls_list else None
            seeded_merchant.photo_urls = photo_urls_list if photo_urls_list else []
            seeded_merchant.price_level = place_details_data.get("priceLevel")
            
            # Get types/category
            types = place_details_data.get("types", [])
            if types:
                seeded_merchant.place_types = types
                # Set primary category
                if "restaurant" in types or "food" in types:
                    seeded_merchant.primary_category = "food"
                elif "cafe" in types:
                    seeded_merchant.primary_category = "coffee"
                else:
                    seeded_merchant.primary_category = "other"
            
            db.commit()
            db.refresh(seeded_merchant)
            
            # Create ChargerMerchant link
            seeded_link = db.query(ChargerMerchant).filter(
                ChargerMerchant.charger_id == charger.id,
                ChargerMerchant.merchant_id == seeded_merchant.id
            ).first()
            
            if not seeded_link:
                seeded_link = ChargerMerchant(
                    charger_id=charger.id,
                    merchant_id=seeded_merchant.id,
                    distance_m=distance_to_charger_m,
                    walk_duration_s=int(round(distance_to_charger_m / 80 * 60)),
                    is_primary=False
                )
                db.add(seeded_link)
                db.commit()
            
            seeded_count += 1
        
        # Fallback: If we don't have enough merchants, use static list
        if seeded_count < min_merchants:
            logger.info(f"Only {seeded_count} merchants from Google Places, adding static fallback merchants")
            static_merchants = [
                {"name": "Taco Bell", "address": "500 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3845, "lng": -97.6895},
                {"name": "Subway", "address": "502 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3847, "lng": -97.6897},
                {"name": "McDonald's", "address": "503 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3849, "lng": -97.6899},
                {"name": "Starbucks", "address": "504 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3851, "lng": -97.6901},
                {"name": "Pizza Hut", "address": "505 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3853, "lng": -97.6903},
                {"name": "Burger King", "address": "506 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3855, "lng": -97.6905},
                {"name": "Wendy's", "address": "507 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3857, "lng": -97.6907},
                {"name": "Dunkin'", "address": "508 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3859, "lng": -97.6909},
                {"name": "KFC", "address": "509 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3861, "lng": -97.6911},
                {"name": "Domino's", "address": "510 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3863, "lng": -97.6913},
                {"name": "Chipotle", "address": "511 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3865, "lng": -97.6915},
                {"name": "Papa John's", "address": "512 W Canyon Ridge Dr, Austin, TX 78753", "lat": 30.3867, "lng": -97.6917},
            ]
            
            for static_merchant in static_merchants:
                if seeded_count >= request.seed_limit:
                    break
                
                # Calculate distance
                distance_to_charger_m = haversine_distance(
                    request.charger_lat, request.charger_lng,
                    static_merchant["lat"], static_merchant["lng"]
                )
                
                # Create merchant ID from name
                merchant_id_slug = static_merchant["name"].lower().replace("'", "").replace(" ", "_")
                seeded_merchant_id = f"m_static_{merchant_id_slug}"
                
                # Check if already exists
                seeded_merchant = db.query(Merchant).filter(Merchant.id == seeded_merchant_id).first()
                
                if not seeded_merchant:
                    seeded_merchant = Merchant(
                        id=seeded_merchant_id,
                        name=static_merchant["name"],
                        lat=static_merchant["lat"],
                        lng=static_merchant["lng"],
                        address=static_merchant["address"],
                        primary_category="food"
                    )
                    db.add(seeded_merchant)
                    db.commit()
                    db.refresh(seeded_merchant)
                else:
                    # Update if exists
                    seeded_merchant.name = static_merchant["name"]
                    seeded_merchant.address = static_merchant["address"]
                    db.commit()
                
                # Create ChargerMerchant link
                seeded_link = db.query(ChargerMerchant).filter(
                    ChargerMerchant.charger_id == charger.id,
                    ChargerMerchant.merchant_id == seeded_merchant.id
                ).first()
                
                if not seeded_link:
                    seeded_link = ChargerMerchant(
                        charger_id=charger.id,
                        merchant_id=seeded_merchant.id,
                        distance_m=distance_to_charger_m,
                        walk_duration_s=int(round(distance_to_charger_m / 80 * 60)),
                        is_primary=False
                    )
                    db.add(seeded_link)
                    db.commit()
                
                seeded_count += 1
        
        db.commit()
        
        logger.info(f"Bootstrap complete: cluster_id={cluster.id}, seeded={seeded_count} merchants")
        
        return AsadasPartyResponse(
            ok=True,
            cluster_id=str(cluster.id),
            primary_merchant={
                "id": merchant.id,
                "qr_token": qr_token
            },
            seeded_merchants_count=seeded_count,
            magic_link_sent=magic_link_sent
        )
        
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bootstrap failed: {str(e)}"
        )


@router.get("/debug/merchant-by-qr/{token}")
async def debug_merchant_by_qr(
    token: str,
    db: Session = Depends(get_db),
    bootstrap_key: str = Depends(verify_bootstrap_key)
):
    """
    Debug endpoint to check if a QR token resolves to a merchant.
    Protected by bootstrap key.
    """
    from app.models_domain import DomainMerchant
    
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.qr_token == token
    ).first()
    
    if not merchant:
        return {
            "found": False,
            "token": token,
            "message": "No merchant found with this QR token"
        }
    
    return {
        "found": True,
        "token": token,
        "merchant_id": str(merchant.id),
        "merchant_name": merchant.name,
        "status": merchant.status,
        "qr_token": merchant.qr_token,
        "qr_created_at": merchant.qr_created_at.isoformat() if merchant.qr_created_at else None,
        "qr_last_used_at": merchant.qr_last_used_at.isoformat() if merchant.qr_last_used_at else None,
        "zone_slug": merchant.zone_slug
    }


@router.get("/schema")
async def get_bootstrap_schema():
    """Return JSON schema/example payload for bootstrap endpoint"""
    return {
        "endpoint": "POST /v1/bootstrap/asadas_party",
        "headers": {
            "X-Bootstrap-Key": "<BOOTSTRAP_KEY from env>",
            "Content-Type": "application/json"
        },
        "example_payload": {
            "charger_address": "501 W Canyon Ridge Dr, Austin, TX 78753",
            "charger_lat": 30.3839,
            "charger_lng": -97.6900,
            "charger_radius_m": 400,
            "merchant_radius_m": 40,
            "primary_merchant": {
                "name": "Asadas Grill",
                "address": "501 W Canyon Ridge Dr, Austin, TX 78753",
                "email": "hector@example.com",
                "phone": "+1-512-555-1234"
            },
            "seed_limit": 25
        },
        "response": {
            "ok": True,
            "cluster_id": "<uuid>",
            "primary_merchant": {
                "id": "<merchant_id>",
                "qr_token": "<qr_token>"
            },
            "seeded_merchants_count": 25,
            "magic_link_sent": True
        }
    }


@router.post("/create-admin")
def bootstrap_create_admin(
    email: Optional[str] = None,
    password: Optional[str] = None,
    db: Session = Depends(get_db),
    _: str = Depends(verify_bootstrap_key),
):
    """
    Create or update admin user. Protected by BOOTSTRAP_KEY.
    Pass email/password as query params, or falls back to env vars.
    """
    from app.core.security import hash_password

    email = email or os.getenv("ADMIN_EMAIL", "james@nerava.network")
    password = password or os.getenv("ADMIN_PASSWORD", "BIGAppleNerava")

    user = db.query(User).filter(User.email == email).first()
    if user:
        user.password_hash = hash_password(password)
        if "admin" not in (user.role_flags or ""):
            user.role_flags = (user.role_flags or "") + ",admin"
        db.commit()
        return {"ok": True, "action": "updated", "email": email, "user_id": user.id}

    user = User(
        email=email,
        password_hash=hash_password(password),
        role_flags="admin",
        auth_provider="email",
    )
    db.add(user)
    db.commit()
    return {"ok": True, "action": "created", "email": email, "user_id": user.id}


@router.post("/trigger-seed")
async def bootstrap_trigger_seed(
    seed_type: str = "chargers",
    states: Optional[str] = None,
    max_cells: Optional[int] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_km: Optional[float] = 15.0,
    db: Session = Depends(get_db),
    _: str = Depends(verify_bootstrap_key),
):
    """
    Trigger charger/merchant seed directly (runs synchronously).
    Protected by BOOTSTRAP_KEY.

    seed_type: 'chargers' or 'merchants'
    states: comma-separated state codes (optional, for chargers)
    max_cells: max grid cells (optional, for merchants)
    lat/lng/radius_km: target a specific area for merchant seeding
    """
    if seed_type == "chargers":
        from scripts.seed_chargers_bulk import seed_chargers
        state_list = states.split(",") if states else None
        result = await seed_chargers(db, states=state_list)
        return {"ok": True, "type": "chargers", "result": result}
    elif seed_type == "merchants":
        from scripts.seed_merchants_free import seed_merchants
        chargers_override = None
        if lat is not None and lng is not None:
            # Target a specific area — only seed chargers near lat/lng
            import math

            from app.models.while_you_charge import Charger
            lat_delta = radius_km / 111.0
            lng_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
            chargers_override = db.query(Charger).filter(
                Charger.lat.between(lat - lat_delta, lat + lat_delta),
                Charger.lng.between(lng - lng_delta, lng + lng_delta),
            ).all()
            logger.info(f"[TriggerSeed] Targeting {len(chargers_override)} chargers near ({lat}, {lng}) r={radius_km}km")
        result = await seed_merchants(db, max_cells=max_cells, chargers_override=chargers_override)
        return {"ok": True, "type": "merchants", "result": result}
    else:
        raise HTTPException(status_code=400, detail="seed_type must be 'chargers' or 'merchants'")

