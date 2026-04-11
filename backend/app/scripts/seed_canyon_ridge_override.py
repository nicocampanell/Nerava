"""
Seed script for Canyon Ridge Tesla Supercharger with Asadas Grill primary override.

Creates:
- Canyon Ridge charger (500 W Canyon Ridge Dr, Austin, TX 78753)
- Asadas Grill merchant (enriched from Google Places)
- Primary merchant override link
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import logging

from app.db import SessionLocal
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.google_places_new import place_details, search_text
from app.services.merchant_enrichment import enrich_from_google_places

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def seed_canyon_ridge_override():
    """Seed Canyon Ridge charger with Asadas Grill primary override"""
    db = SessionLocal()
    
    try:
        # Canyon Ridge charger coordinates (verified from production database)
        charger_lat = 30.3979
        charger_lng = -97.7044
        charger_address = "500 W Canyon Ridge Dr, Austin, TX 78753"
        charger_id = "canyon_ridge_tesla"
        
        # 1. Find or create Canyon Ridge charger
        charger = db.query(Charger).filter(Charger.id == charger_id).first()
        if not charger:
            charger = Charger(
                id=charger_id,
                name="Tesla Supercharger - Canyon Ridge",
                network_name="Tesla",
                lat=charger_lat,
                lng=charger_lng,
                address=charger_address,
                city="Austin",
                state="TX",
                zip_code="78753",
                status="available",
                is_public=True,
            )
            db.add(charger)
            db.commit()
            db.refresh(charger)
            logger.info(f"✅ Created charger: {charger_id}")
        else:
            logger.info(f"✅ Charger already exists: {charger_id}")
        
        # 2. Search for Asadas Grill using Google Places
        logger.info("Searching for Asadas Grill on Google Places...")
        search_results = await search_text(
            query="Asadas Grill",
            location_bias={"lat": charger_lat, "lng": charger_lng},
            max_results=5
        )
        
        asadas_place_id = None
        asadas_data = None
        
        # Find the best match (should be near the charger)
        for result in search_results:
            if "asadas" in result.get("name", "").lower() and "grill" in result.get("name", "").lower():
                asadas_place_id = result.get("place_id")
                # Verify it's close to charger (within 500m)
                distance = result.get("distance_m")
                if distance and distance < 500:
                    asadas_data = result
                    logger.info(f"✅ Found Asadas Grill: {result.get('name')} (distance: {distance}m)")
                    break
        
        if not asadas_place_id:
            logger.warning("⚠️  Could not find Asadas Grill on Google Places. Creating merchant without place_id.")
            # Create merchant with approximate location
            merchant_id = "asadas_grill_canyon_ridge"
            merchant = Merchant(
                id=merchant_id,
                name="Asadas Grill",
                lat=30.2680,  # Approximate location near charger
                lng=-97.7435,
                address="501 W Canyon Ridge Dr, Austin, TX 78753",
                city="Austin",
                state="TX",
                zip_code="78753",
                category="restaurant",
                primary_category="food",
            )
            db.add(merchant)
            db.commit()
            db.refresh(merchant)
        else:
            # Fetch full place details
            logger.info(f"Fetching full details for place_id: {asadas_place_id}")
            place_data = await place_details(asadas_place_id)
            
            if not place_data:
                logger.error("Failed to fetch place details")
                return
            
            # Extract merchant data
            display_name = place_data.get("displayName", {})
            name = display_name.get("text", "") if isinstance(display_name, dict) else str(display_name)
            location = place_data.get("location", {})
            
            merchant_id = f"asadas_grill_{asadas_place_id[:20]}"  # Use place_id in merchant ID
            
            # Check if merchant already exists
            merchant = db.query(Merchant).filter(Merchant.place_id == asadas_place_id).first()
            if not merchant:
                merchant = Merchant(
                    id=merchant_id,
                    place_id=asadas_place_id,
                    name=name,
                    lat=location.get("latitude", charger_lat),
                    lng=location.get("longitude", charger_lng),
                    address=place_data.get("formattedAddress"),
                    phone=place_data.get("nationalPhoneNumber"),
                    website=place_data.get("websiteUri"),
                    category="restaurant",
                    primary_category="food",
                    rating=place_data.get("rating"),
                    user_rating_count=place_data.get("userRatingCount"),
                    price_level=place_data.get("priceLevel"),
                    business_status=place_data.get("businessStatus"),
                    place_types=place_data.get("types", []),
                )
                db.add(merchant)
                db.commit()
                db.refresh(merchant)
                logger.info(f"✅ Created merchant: {merchant_id}")
            else:
                merchant_id = merchant.id
                logger.info(f"✅ Merchant already exists: {merchant_id}")
            
            # Enrich merchant with full Google Places data (photos, hours, etc.)
            logger.info("Enriching merchant with Google Places data...")
            success = await enrich_from_google_places(db, merchant, asadas_place_id, force_refresh=True)
            if success:
                logger.info("✅ Merchant enriched successfully")
            else:
                logger.warning("⚠️  Merchant enrichment had issues, but continuing...")
        
        # 3. Calculate distance and walk time (approximate)
        from app.services.google_places_new import _haversine_distance
        distance_m = _haversine_distance(charger.lat, charger.lng, merchant.lat, merchant.lng)
        # Approximate walk time: 80m/min walking speed
        walk_duration_s = int((distance_m / 80) * 60)
        
        # 4. Create or update primary merchant override
        override = db.query(ChargerMerchant).filter(
            ChargerMerchant.charger_id == charger_id,
            ChargerMerchant.merchant_id == merchant_id
        ).first()
        
        if override:
            # Update existing link to be primary
            override.is_primary = True
            override.override_mode = "PRE_CHARGE_ONLY"
            override.suppress_others = True
            override.exclusive_title = "Free Margarita"
            override.exclusive_description = "Free Margarita (Charging Exclusive)"
            override.distance_m = distance_m
            override.walk_duration_s = walk_duration_s
            logger.info("✅ Updated existing ChargerMerchant link to primary")
        else:
            # Create new primary override link
            override = ChargerMerchant(
                charger_id=charger_id,
                merchant_id=merchant_id,
                distance_m=distance_m,
                walk_duration_s=walk_duration_s,
                is_primary=True,
                override_mode="PRE_CHARGE_ONLY",
                suppress_others=True,
                exclusive_title="Free Margarita",
                exclusive_description="Free Margarita (Charging Exclusive)",
            )
            db.add(override)
            logger.info("✅ Created primary merchant override")
        
        db.commit()
        
        logger.info("\n" + "="*60)
        logger.info("✅ Canyon Ridge override seeded successfully!")
        logger.info(f"   Charger ID: {charger_id}")
        logger.info(f"   Merchant ID: {merchant_id}")
        logger.info(f"   Place ID: {merchant.place_id or 'N/A'}")
        logger.info(f"   Distance: {distance_m:.0f}m")
        logger.info(f"   Walk time: {walk_duration_s // 60} min")
        logger.info("="*60 + "\n")
        
    except Exception as e:
        logger.error(f"❌ Error seeding Canyon Ridge override: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(seed_canyon_ridge_override())






