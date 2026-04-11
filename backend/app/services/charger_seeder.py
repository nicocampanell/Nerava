"""
Charger Seeder Service

Seeds chargers and nearby merchants from Google Places API.
Designed to be called from admin endpoints in production.
"""
import math
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.google_places_new import _haversine_distance, place_details, search_nearby
from app.utils.log import get_logger

logger = get_logger(__name__)

# Austin charger data
CHARGERS = [
    {
        "id": "canyon_ridge_tesla",
        "name": "Canyon Ridge Supercharger",
        "place_id": "ChIJK-gKfYnLRIYRQKQmx_DvQko",
        "address": "501 W Canyon Ridge Dr, Austin, TX 78753",
        "lat": 30.4027,
        "lng": -97.6719,
        "network": "Tesla",
        "stalls": 8,
        "kw": 150,
        "primary_merchant_place_id": "ChIJA4UGPT_LRIYRjQC0TnNUWRg"  # Asadas Grill
    },
    {
        "id": "charger_mopac",
        "name": "Tesla Supercharger - Mopac",
        "place_id": "ChIJ51fvhIfLRIYRf3XcWjepmrA",
        "address": "10515 N Mopac Expy, Austin, TX 78759",
        "lat": 30.390456,
        "lng": -97.733056,
        "network": "Tesla",
        "stalls": 12,
        "kw": 250,
        "primary_merchant_place_id": None
    },
    {
        "id": "charger_westlake",
        "name": "Tesla Supercharger - Westlake",
        "place_id": "ChIJJ6_0bN1LW4YRg8l9RLePwz8",
        "address": "701 S Capital of Texas Hwy, West Lake Hills, TX 78746",
        "lat": 30.2898,
        "lng": -97.827474,
        "network": "Tesla",
        "stalls": 16,
        "kw": 250,
        "primary_merchant_place_id": None
    },
    {
        "id": "charger_ben_white",
        "name": "Tesla Supercharger - Ben White",
        "place_id": "ChIJcz30IE9LW4YRYVS3g5VSz9Y",
        "address": "2300 W Ben White Blvd, Austin, TX 78704",
        "lat": 30.2334001,
        "lng": -97.7914251,
        "network": "Tesla",
        "stalls": 10,
        "kw": 150,
        "primary_merchant_place_id": None
    },
    {
        "id": "charger_sunset_valley",
        "name": "Tesla Supercharger - Sunset Valley",
        "place_id": "ChIJ2Um53XdLW4YRFBnBkfJKFJA",
        "address": "5601 Brodie Ln, Austin, TX 78745",
        "lat": 30.2261013,
        "lng": -97.8219238,
        "network": "Tesla",
        "stalls": 8,
        "kw": 150,
        "primary_merchant_place_id": None
    }
]


def map_types_to_category(types: List[str]) -> Tuple[str, str]:
    """Map Google Places types to category and primary_category."""
    type_set = set(t.lower() for t in types)

    if any(t in type_set for t in ["restaurant", "meal_takeaway"]):
        category = "Restaurant"
        primary_category = "food"
    elif any(t in type_set for t in ["cafe", "coffee_shop"]):
        category = "Coffee Shop"
        primary_category = "food"
    elif "convenience_store" in type_set:
        category = "Convenience Store"
        primary_category = "other"
    elif any(t in type_set for t in ["gym", "fitness_center"]):
        category = "Gym"
        primary_category = "other"
    elif any(t in type_set for t in ["pharmacy", "drugstore"]):
        category = "Pharmacy"
        primary_category = "other"
    else:
        category = types[0].replace("_", " ").title() if types else "Business"
        primary_category = "other"

    return category, primary_category


class ChargerSeederService:
    """Service for seeding chargers and merchants."""

    def __init__(self, db: Session):
        self.db = db
        self.results = {
            "chargers_created": 0,
            "chargers_updated": 0,
            "merchants_created": 0,
            "merchants_updated": 0,
            "links_created": 0,
            "errors": []
        }

    async def seed_all_chargers(self, charger_ids: Optional[List[str]] = None) -> Dict:
        """
        Seed all chargers or specific ones if charger_ids provided.

        Args:
            charger_ids: Optional list of charger IDs to seed. If None, seeds all.

        Returns:
            Dict with seeding results
        """
        chargers_to_seed = CHARGERS
        if charger_ids:
            chargers_to_seed = [c for c in CHARGERS if c["id"] in charger_ids]

        for charger_data in chargers_to_seed:
            try:
                await self._seed_charger(charger_data)
            except Exception as e:
                error_msg = f"Error seeding {charger_data['id']}: {str(e)}"
                logger.error(error_msg)
                self.results["errors"].append(error_msg)

        return self.results

    async def _seed_charger(self, charger_data: Dict):
        """Seed a single charger with merchants."""
        charger_id = charger_data["id"]
        logger.info(f"Seeding charger: {charger_data['name']}")

        # 1. Upsert charger record
        charger = self.db.query(Charger).filter(Charger.id == charger_id).first()
        if charger:
            charger.name = charger_data["name"]
            charger.network_name = charger_data["network"]
            charger.lat = charger_data["lat"]
            charger.lng = charger_data["lng"]
            charger.address = charger_data["address"]
            charger.power_kw = charger_data["kw"]
            charger.connector_types = ["Tesla"]
            self.results["chargers_updated"] += 1
            logger.info(f"Updated charger: {charger.name}")
        else:
            charger = Charger(
                id=charger_id,
                name=charger_data["name"],
                network_name=charger_data["network"],
                lat=charger_data["lat"],
                lng=charger_data["lng"],
                address=charger_data["address"],
                power_kw=charger_data["kw"],
                connector_types=["Tesla"],
                status="available"
            )
            self.db.add(charger)
            self.results["chargers_created"] += 1
            logger.info(f"Created charger: {charger.name}")

        self.db.commit()

        # 2. Fetch nearby merchants from Google Places
        logger.info(f"Fetching nearby merchants for {charger_data['name']}...")
        try:
            nearby_places = await search_nearby(
                lat=charger_data["lat"],
                lng=charger_data["lng"],
                radius_m=500,
                included_types=["restaurant", "cafe", "coffee_shop", "convenience_store"],
                max_results=15
            )
        except Exception as e:
            logger.error(f"Error fetching nearby places: {e}")
            self.results["errors"].append(f"Google Places error for {charger_id}: {str(e)}")
            return

        # Filter out charger itself
        nearby_places = [p for p in nearby_places if p.get("place_id") != charger_data["place_id"]]
        nearby_places = nearby_places[:12]
        logger.info(f"Found {len(nearby_places)} merchants")

        # 3. Process each merchant
        merchants_processed = []
        for place in nearby_places:
            place_id = place.get("place_id")
            if not place_id:
                continue

            try:
                details = await place_details(place_id)
                if not details:
                    continue

                merchant_result = await self._process_merchant(
                    charger_id, charger_data, place_id, details
                )
                if merchant_result:
                    merchants_processed.append(merchant_result)

            except Exception as e:
                logger.warning(f"Error processing merchant {place_id}: {e}")
                continue

        # 4. Set primary merchant
        await self._set_primary_merchant(charger_id, charger_data, merchants_processed)

        logger.info(f"Completed seeding charger: {charger_data['name']}")

    async def _process_merchant(
        self,
        charger_id: str,
        charger_data: Dict,
        place_id: str,
        details: Dict
    ) -> Optional[Dict]:
        """Process and upsert a single merchant."""
        # Extract merchant data
        name = details.get("displayName", {}).get("text", "") if isinstance(details.get("displayName"), dict) else str(details.get("displayName", ""))
        location = details.get("location", {})
        merchant_lat = location.get("latitude", 0)
        merchant_lng = location.get("longitude", 0)
        types = details.get("types", [])
        rating = details.get("rating")
        user_rating_count = details.get("userRatingCount")

        # Calculate distance and walk time
        distance_m = _haversine_distance(
            charger_data["lat"], charger_data["lng"],
            merchant_lat, merchant_lng
        )
        walk_duration_s = int(math.ceil(distance_m / 1.33))  # 80 m/min

        # Map types to category
        category, primary_category = map_types_to_category(types)

        # Upsert merchant
        merchant = self.db.query(Merchant).filter(Merchant.place_id == place_id).first()
        merchant_id = f"google_{place_id[:20]}"

        if merchant:
            merchant.name = name
            merchant.lat = merchant_lat
            merchant.lng = merchant_lng
            merchant.category = category
            merchant.primary_category = primary_category
            merchant.rating = rating
            merchant.user_rating_count = user_rating_count
            self.results["merchants_updated"] += 1
        else:
            merchant = Merchant(
                id=merchant_id,
                place_id=place_id,
                name=name,
                category=category,
                primary_category=primary_category,
                lat=merchant_lat,
                lng=merchant_lng,
                rating=rating,
                user_rating_count=user_rating_count,
                place_types=types
            )
            self.db.add(merchant)
            self.results["merchants_created"] += 1

        self.db.commit()

        # Upsert ChargerMerchant link
        link = self.db.query(ChargerMerchant).filter(
            ChargerMerchant.charger_id == charger_id,
            ChargerMerchant.merchant_id == merchant.id
        ).first()

        if link:
            link.distance_m = distance_m
            link.walk_duration_s = walk_duration_s
        else:
            link = ChargerMerchant(
                charger_id=charger_id,
                merchant_id=merchant.id,
                distance_m=distance_m,
                walk_duration_s=walk_duration_s,
                is_primary=False
            )
            self.db.add(link)
            self.results["links_created"] += 1

        self.db.commit()

        logger.info(f"  Processed merchant: {name} ({distance_m:.0f}m)")

        return {
            "merchant": merchant,
            "link": link,
            "distance_m": distance_m
        }

    async def _set_primary_merchant(
        self,
        charger_id: str,
        charger_data: Dict,
        merchants_processed: List[Dict]
    ):
        """Set the primary merchant for a charger."""
        if charger_data.get("primary_merchant_place_id"):
            # Use specified primary merchant
            primary_merchant = self.db.query(Merchant).filter(
                Merchant.place_id == charger_data["primary_merchant_place_id"]
            ).first()

            if primary_merchant:
                primary_link = self.db.query(ChargerMerchant).filter(
                    ChargerMerchant.charger_id == charger_id,
                    ChargerMerchant.merchant_id == primary_merchant.id
                ).first()

                if primary_link:
                    self.db.query(ChargerMerchant).filter(
                        ChargerMerchant.charger_id == charger_id
                    ).update({"is_primary": False})
                    primary_link.is_primary = True
                    self.db.commit()
                    logger.info(f"Set primary merchant: {primary_merchant.name}")
        else:
            # Pick closest restaurant/cafe
            restaurant_links = [
                m for m in merchants_processed
                if m["merchant"].primary_category == "food"
            ]
            if restaurant_links:
                restaurant_links.sort(key=lambda x: x["distance_m"])
                closest = restaurant_links[0]

                self.db.query(ChargerMerchant).filter(
                    ChargerMerchant.charger_id == charger_id
                ).update({"is_primary": False})
                closest["link"].is_primary = True
                self.db.commit()
                logger.info(f"Set primary merchant: {closest['merchant'].name}")

    def get_charger_list(self) -> List[Dict]:
        """Return list of available chargers to seed."""
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "address": c["address"],
                "lat": c["lat"],
                "lng": c["lng"]
            }
            for c in CHARGERS
        ]
