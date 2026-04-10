"""
Management command to seed a city with chargers and merchants

Usage:
    python -m app.jobs.seed_city --city="Austin" --bbox="30.0,-98.0,30.5,-97.5"

Requires:
    NREL_API_KEY - Get free key at https://developer.nrel.gov/signup/
    GOOGLE_PLACES_API_KEY (or GOOGLE_MAPS_API_KEY or GOOGLE_API_KEY)

Example:
    python -m app.jobs.seed_city --city="Austin" --bbox="30.0,-98.0,30.5,-97.5"
    python -m app.jobs.seed_city --city="Austin" --bbox="30.0,-98.0,30.5,-97.5" --categories coffee food
"""
import argparse
import asyncio
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler

# ------------------------------------------------------
# Logging Setup (writes to file + prints to console)
# ------------------------------------------------------
LOG_FILE_PATH = "logs/seed_city.log"

# Ensure the logs directory exists
os.makedirs("logs", exist_ok=True)

# Create rotating file log (5 MB max, keep 3 backups)
file_handler = RotatingFileHandler(
    LOG_FILE_PATH,
    maxBytes=5_000_000,
    backupCount=3,
    encoding="utf-8"
)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)

file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Root logger
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger("seed_city")

from app.db import SessionLocal
from app.integrations.google_distance_matrix_client import get_walk_times
from app.integrations.google_places_client import (
    get_place_details,
    normalize_category_to_google_type,
    search_places_near,
)
from app.integrations.nrel_client import fetch_chargers_in_bbox
from app.models_while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.while_you_charge import haversine_distance


async def seed_city(city: str, bbox_str: str, categories: list = None):
    """
    Seed a city with chargers and merchants.
    
    Args:
        city: City name (e.g., "Austin")
        bbox_str: Bounding box as "min_lat,min_lng,max_lat,max_lng"
        categories: List of categories to seed (default: ["coffee", "food", "groceries", "gym"])
    """
    if categories is None:
        categories = ["coffee", "food", "groceries", "gym"]
    
    # Parse bbox
    bbox_parts = [float(x.strip()) for x in bbox_str.split(",")]
    if len(bbox_parts) != 4:
        raise ValueError("bbox must be 'min_lat,min_lng,max_lat,max_lng'")
    bbox = tuple(bbox_parts)
    
    db = SessionLocal()
    
    try:
        logger.info(f"Starting seed for {city} with bbox {bbox}")
        print(f"🌱 Seeding {city} with bbox {bbox}...")
        
        # Step 1: Fetch chargers
        logger.info("Fetching chargers from NREL API...")
        print("📡 Fetching chargers from NREL API...")
        charger_data_list = await fetch_chargers_in_bbox(bbox, limit=100)
        logger.info(f"Found {len(charger_data_list)} chargers from NREL")
        print(f"   Found {len(charger_data_list)} chargers")
        
        # Step 2: Save chargers to DB
        charger_objects = []
        for charger_data in charger_data_list:
            # Check if exists
            existing = db.query(Charger).filter(
                Charger.external_id == charger_data.external_id
            ).first()
            
            if existing:
                charger_objects.append(existing)
                continue
            
            charger = Charger(
                id=f"ch_{uuid.uuid4().hex[:12]}",
                external_id=charger_data.external_id,
                name=charger_data.name,
                network_name=charger_data.network_name,
                lat=charger_data.lat,
                lng=charger_data.lng,
                address=charger_data.address,
                city=charger_data.city or city,
                state=charger_data.state,
                zip_code=charger_data.zip_code,
                connector_types=charger_data.connector_types,
                power_kw=charger_data.power_kw,
                is_public=charger_data.is_public,
                access_code=charger_data.access_code,
                status=charger_data.status
            )
            db.add(charger)
            charger_objects.append(charger)
        
        db.commit()
        logger.info(f"Saved {len(charger_objects)} chargers to DB")
        print(f"   Saved {len(charger_objects)} chargers to DB")
        
        # Step 3: For each charger, find nearby merchants for each category
        total_merchants = 0
        total_links = 0
        logger.info(f"Processing {len(charger_objects)} chargers for merchant discovery")
        
        for charger in charger_objects[:20]:  # Limit to avoid too many API calls
            print(f"   Processing charger: {charger.name}")
            charger_links_before = total_links
            
            for category in categories:
                try:
                    # Get Google Places types + keyword
                    place_types, keyword = normalize_category_to_google_type(category)
                    logger.info(f"[SeedCity] Charger {charger.id}: Searching category '{category}' -> types {place_types} keyword='{keyword}'")
                    
                    # Search for places
                    places = await search_places_near(
                        lat=charger.lat,
                        lng=charger.lng,
                        query=None,
                        types=place_types if place_types else None,
                        radius_m=1000,  # 1km
                        limit=10,
                        keyword=keyword
                    )
                    
                    logger.info(f"[SeedCity] Charger {charger.id} category '{category}': Found {len(places)} places")
                    
                    if not places:
                        logger.debug(f"[SeedCity] No places found for charger {charger.id} category {category}")
                        continue
                    
                    # Get walk times
                    origins = [(charger.lat, charger.lng)]
                    destinations = [(p.lat, p.lng) for p in places]
                    
                    try:
                        walk_times = await get_walk_times(origins, destinations)
                    except Exception as e:
                        logger.error(f"Error getting walk times: {e}", exc_info=True)
                        continue
                    
                    # Process each place
                    for place in places:
                        dest = (place.lat, place.lng)
                        walk_info = walk_times.get((origins[0], dest))
                        
                        if not walk_info or walk_info["duration_s"] > 600:  # Max 10 min walk
                            continue
                        
                        # Check if merchant exists
                        merchant = db.query(Merchant).filter(
                            Merchant.external_id == place.place_id
                        ).first()
                        
                        if not merchant:
                            # Get place details for more info (optional)
                            details = None
                            try:
                                details = await get_place_details(place.place_id)
                            except Exception as e:
                                logger.debug(f"Could not get place details for {place.place_id}: {e}")
                            
                            merchant = Merchant(
                                id=f"m_{uuid.uuid4().hex[:12]}",
                                external_id=place.place_id,
                                name=place.name,
                                category=category,
                                lat=place.lat,
                                lng=place.lng,
                                address=place.address or (details.get("formatted_address") if details else ""),
                                rating=place.rating or (details.get("rating") if details else None),
                                price_level=place.price_level or (details.get("price_level") if details else None),
                                place_types=place.types,
                                logo_url=place.icon,
                                photo_url=details.get("photos", [{}])[0].get("photo_reference") if details and details.get("photos") else None,
                                phone=details.get("formatted_phone_number") if details else None,
                                website=details.get("website") if details else None
                            )
                            db.add(merchant)
                            db.flush()
                            total_merchants += 1
                        
                        # Create or update charger-merchant link (idempotent)
                        existing_link = db.query(ChargerMerchant).filter(
                            ChargerMerchant.charger_id == charger.id,
                            ChargerMerchant.merchant_id == merchant.id
                        ).first()
                        
                        if not existing_link:
                            link = ChargerMerchant(
                                charger_id=charger.id,
                                merchant_id=merchant.id,
                                distance_m=haversine_distance(
                                    charger.lat, charger.lng, merchant.lat, merchant.lng
                                ),
                                walk_duration_s=walk_info["duration_s"],
                                walk_distance_m=walk_info.get("distance_m")
                            )
                            db.add(link)
                            total_links += 1
                        else:
                            # Update walk time if it changed
                            existing_link.walk_duration_s = walk_info["duration_s"]
                            existing_link.walk_distance_m = walk_info.get("distance_m")
                    
                    # Small delay to avoid rate limits
                    await asyncio.sleep(0.1)
                
                except Exception as e:
                    logger.error(f"Error processing category {category} for charger {charger.id}: {e}", exc_info=True)
                    print(f"   ⚠️  Error with category {category}: {e}")
                    continue

            if total_links == charger_links_before:
                logger.warning(f"No merchants found for charger {charger.name}")
        
        db.commit()
        logger.info(f"Seed complete: {total_merchants} merchants, {total_links} charger-merchant links")
        print(f"✅ Seeded {total_merchants} merchants and {total_links} charger-merchant links")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Seed failed: {e}", exc_info=True)
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Seed a city with chargers and merchants")
    parser.add_argument("--city", required=True, help="City name (e.g., Austin)")
    parser.add_argument("--bbox", required=True, help="Bounding box: min_lat,min_lng,max_lat,max_lng")
    parser.add_argument("--categories", nargs="+", default=["coffee", "food", "groceries", "gym"],
                       help="Categories to seed")
    
    args = parser.parse_args()
    
    asyncio.run(seed_city(args.city, args.bbox, args.categories))


if __name__ == "__main__":
    main()

