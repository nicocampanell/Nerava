"""
Merchant enrichment service for syncing Google Places API data to Merchant model.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.while_you_charge import Merchant
from app.services.google_places_new import get_open_status, get_photo_url, place_details

logger = logging.getLogger(__name__)


async def enrich_from_google_places(
    db: Session,
    merchant: Merchant,
    place_id: str,
    force_refresh: bool = False
) -> bool:
    """
    Enrich merchant from Google Places API data.
    
    Args:
        db: Database session
        merchant: Merchant model instance
        place_id: Google Places ID
        force_refresh: If True, bypass cache and refresh all data
    
    Returns:
        True if enrichment succeeded, False otherwise
    """
    if not place_id:
        logger.warning(f"[MerchantEnrichment] No place_id provided for merchant {merchant.id}")
        return False
    
    try:
        # Check if we need to refresh (24h TTL for place details)
        needs_refresh = force_refresh
        if not needs_refresh and merchant.google_places_updated_at:
            age = datetime.utcnow() - merchant.google_places_updated_at
            if age > timedelta(hours=24):
                needs_refresh = True
                logger.info(f"[MerchantEnrichment] Merchant {merchant.id} data is stale ({age}), refreshing")
        
        # Fetch place details if needed
        if needs_refresh or not merchant.google_places_updated_at:
            place_data = await place_details(place_id)
            if not place_data:
                logger.error(f"[MerchantEnrichment] Failed to fetch place details for {place_id}")
                return False
            
            # Update merchant fields from place data
            display_name = place_data.get("displayName", {})
            if isinstance(display_name, dict):
                merchant.name = display_name.get("text", merchant.name)
            elif display_name:
                merchant.name = str(display_name)
            
            # Update location
            location = place_data.get("location", {})
            if location:
                merchant.lat = location.get("latitude", merchant.lat)
                merchant.lng = location.get("longitude", merchant.lng)
            
            # Update address
            formatted_address = place_data.get("formattedAddress")
            if formatted_address:
                merchant.address = formatted_address
            
            # Update phone and website
            phone = place_data.get("nationalPhoneNumber")
            if phone:
                merchant.phone = phone
            
            website = place_data.get("websiteUri")
            if website:
                merchant.website = website
            
            # Update rating and review count
            rating = place_data.get("rating")
            if rating is not None:
                merchant.rating = float(rating)
            
            user_rating_count = place_data.get("userRatingCount")
            if user_rating_count is not None:
                merchant.user_rating_count = int(user_rating_count)
            
            # Update price level (Google Places API New returns string enum)
            price_level = place_data.get("priceLevel")
            if price_level is not None:
                price_map = {
                    "PRICE_LEVEL_FREE": 0,
                    "PRICE_LEVEL_INEXPENSIVE": 1,
                    "PRICE_LEVEL_MODERATE": 2,
                    "PRICE_LEVEL_EXPENSIVE": 3,
                    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
                }
                if isinstance(price_level, str):
                    merchant.price_level = price_map.get(price_level)
                else:
                    merchant.price_level = int(price_level)
            
            # Update business status
            business_status = place_data.get("businessStatus")
            if business_status:
                merchant.business_status = business_status
            
            # Update types
            types = place_data.get("types", [])
            if types:
                merchant.place_types = types
            
            # Handle photos (truncate URLs to fit varchar(255) columns)
            photos = place_data.get("photos", [])
            if photos:
                photo_urls = []
                primary_photo_url = None

                # Process up to 5 photos
                for i, photo in enumerate(photos[:5]):
                    photo_name = photo.get("name", "")
                    if photo_name:
                        # Extract photo reference
                        photo_ref = photo_name.replace("places/", "").split("/photos/")[-1]
                        if photo_ref:
                            # Get photo URL (this will use cache)
                            photo_url = await get_photo_url(photo_ref, max_width=800)
                            if photo_url:
                                photo_urls.append(photo_url)
                                # First photo is primary (must fit varchar(255))
                                if i == 0:
                                    primary_photo_url = photo_url
                                    if len(photo_url) <= 255:
                                        merchant.primary_photo_url = photo_url

                merchant.photo_urls = photo_urls
                # Also update legacy photo_url field for backward compatibility
                if primary_photo_url:
                    if len(primary_photo_url) <= 255:
                        merchant.photo_url = primary_photo_url
            
            # Store opening hours
            opening_hours = place_data.get("regularOpeningHours")
            if opening_hours:
                merchant.hours_json = opening_hours
            
            # Update timestamp
            merchant.google_places_updated_at = datetime.utcnow()
            merchant.place_id = place_id
        
        # Refresh open status (shorter TTL, 5-10 min)
        await refresh_open_status(db, merchant, force_refresh=force_refresh)
        
        # Commit changes
        db.commit()
        db.refresh(merchant)
        
        logger.info(f"[MerchantEnrichment] Successfully enriched merchant {merchant.id} from place_id {place_id}")
        return True
        
    except Exception as e:
        logger.error(f"[MerchantEnrichment] Error enriching merchant {merchant.id}: {e}", exc_info=True)
        db.rollback()
        return False


async def refresh_open_status(
    db: Session,
    merchant: Merchant,
    force_refresh: bool = False
) -> bool:
    """
    Refresh open/closed status for a merchant (lightweight check).
    
    Args:
        db: Database session
        merchant: Merchant model instance
        force_refresh: If True, bypass cache
    
    Returns:
        True if status was updated, False otherwise
    """
    if not merchant.place_id:
        return False
    
    try:
        # Check if we need to refresh (5-10 min TTL)
        needs_refresh = force_refresh
        if not needs_refresh and merchant.last_status_check:
            age = datetime.utcnow() - merchant.last_status_check
            if age > timedelta(minutes=5):
                needs_refresh = True
        
        if needs_refresh or not merchant.last_status_check:
            status_data = await get_open_status(merchant.place_id)
            if status_data:
                merchant.open_now = status_data.get("open_now")
                merchant.last_status_check = datetime.utcnow()
                
                # Store open_until in hours_json if available
                open_until = status_data.get("open_until")
                if open_until and merchant.hours_json:
                    if isinstance(merchant.hours_json, dict):
                        merchant.hours_json["open_until"] = open_until
                
                db.commit()
                logger.debug(f"[MerchantEnrichment] Updated open status for merchant {merchant.id}: {merchant.open_now}")
                return True
        
        return False
        
    except Exception as e:
        logger.warning(f"[MerchantEnrichment] Error refreshing open status for merchant {merchant.id}: {e}")
        return False


def derive_open_now_from_hours(hours_json: Optional[Dict]) -> Optional[bool]:
    """
    Derive open_now status from hours_json.
    
    Args:
        hours_json: Opening hours JSON from Google Places
    
    Returns:
        True if open, False if closed, None if cannot determine
    """
    if not hours_json:
        return None
    
    try:
        from datetime import datetime
        now = datetime.now()
        weekday = now.weekday()  # 0 = Monday, 6 = Sunday
        
        # Google uses 0 = Sunday, 6 = Saturday
        google_weekday = (weekday + 1) % 7
        
        periods = hours_json.get("periods", [])
        for period in periods:
            if period.get("open", {}).get("day") == google_weekday:
                open_time = period.get("open", {}).get("hours", 0) * 60 + period.get("open", {}).get("minutes", 0)
                close_time = period.get("close", {}).get("hours", 0) * 60 + period.get("close", {}).get("minutes", 0)
                current_time = now.hour * 60 + now.minute
                
                if open_time <= current_time < close_time:
                    return True
        
        return False
        
    except Exception as e:
        logger.warning(f"[MerchantEnrichment] Error deriving open_now from hours: {e}")
        return None


def format_open_until(hours_json: Optional[Dict]) -> Optional[str]:
    """
    Format "Open until X" string from hours_json.
    
    Args:
        hours_json: Opening hours JSON from Google Places
    
    Returns:
        Formatted string like "Open until 10:00 PM" or None
    """
    if not hours_json:
        return None
    
    try:
        from datetime import datetime
        now = datetime.now()
        weekday = now.weekday()
        google_weekday = (weekday + 1) % 7
        
        periods = hours_json.get("periods", [])
        for period in periods:
            if period.get("open", {}).get("day") == google_weekday:
                close_hour = period.get("close", {}).get("hours", 0)
                close_min = period.get("close", {}).get("minutes", 0)
                
                if close_hour is not None and close_min is not None:
                    period_str = f"{close_hour % 12 or 12}:{close_min:02d} {'PM' if close_hour >= 12 else 'AM'}"
                    return f"Open until {period_str}"
        
        return None
        
    except Exception as e:
        logger.warning(f"[MerchantEnrichment] Error formatting open_until: {e}")
        return None






