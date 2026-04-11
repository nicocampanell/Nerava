"""
Service layer for "While You Charge" search and ranking
"""

import logging
import uuid
from math import cos, radians
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.integrations.google_distance_matrix_client import get_walk_times
from app.integrations.google_places_client import (
    get_place_details,
    normalize_category_to_google_type,
    search_places_near,
)
from app.integrations.nrel_client import fetch_chargers_in_bbox
from app.models_while_you_charge import Charger, ChargerMerchant, Merchant, MerchantPerk
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)


def normalize_query_to_category(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize query to canonical category or treat as merchant name.

    Returns:
        (category, merchant_name) - one will be None
    """
    query_lower = query.lower().strip()

    category_map = {
        "coffee": "coffee",
        "cafe": "coffee",
        "food": "food",
        "restaurant": "food",
        "dining": "food",
        "groceries": "groceries",
        "grocery": "groceries",
        "supermarket": "groceries",
        "gym": "gym",
        "fitness": "gym",
        "workout": "gym",
    }

    for key, category in category_map.items():
        if key in query_lower:
            return (category, None)

    # Not a category, treat as merchant name
    return (None, query)


async def find_chargers_near(
    db: Session,
    user_lat: float,
    user_lng: float,
    radius_m: float = 10000,
    max_drive_minutes: int = 15,
) -> List[Charger]:
    """
    Find chargers near user location.
    If none found in DB, fetch from API and seed.
    """
    logger.info(
        f"[WhileYouCharge] Finding chargers near ({user_lat}, {user_lng}) within {radius_m}m"
    )

    # Rough bounding box (not perfect circle, but good enough)
    # 1 degree lat ≈ 111km, 1 degree lng ≈ 111km * cos(lat)
    lat_deg = radius_m / 111000
    lng_deg = radius_m / (111000 * abs(cos(radians(user_lat))))

    min_lat = user_lat - lat_deg
    max_lat = user_lat + lat_deg
    min_lng = user_lng - lng_deg
    max_lng = user_lng + lng_deg

    # Query DB
    chargers = (
        db.query(Charger)
        .filter(
            and_(
                Charger.lat >= min_lat,
                Charger.lat <= max_lat,
                Charger.lng >= min_lng,
                Charger.lng <= max_lng,
                Charger.is_public == True,
            )
        )
        .limit(50)
        .all()
    )

    logger.info(f"[WhileYouCharge] Found {len(chargers)} chargers in DB")

    # If no chargers in DB, fetch from API
    if not chargers:
        logger.info("[WhileYouCharge] No chargers in DB, fetching from NREL API...")
        bbox = (min_lat, min_lng, max_lat, max_lng)
        charger_data_list = await fetch_chargers_in_bbox(bbox, limit=50)
        logger.info(f"[WhileYouCharge] Fetched {len(charger_data_list)} chargers from NREL API")

        # Save to DB
        for charger_data in charger_data_list:
            charger = Charger(
                id=f"ch_{uuid.uuid4().hex[:12]}",
                external_id=charger_data.external_id,
                name=charger_data.name,
                network_name=charger_data.network_name,
                lat=charger_data.lat,
                lng=charger_data.lng,
                address=charger_data.address,
                city=charger_data.city,
                state=charger_data.state,
                zip_code=charger_data.zip_code,
                connector_types=charger_data.connector_types,
                power_kw=charger_data.power_kw,
                is_public=charger_data.is_public,
                access_code=charger_data.access_code,
                status=charger_data.status,
            )
            db.add(charger)

        db.commit()
        logger.info(f"[WhileYouCharge] Saved {len(charger_data_list)} new chargers to DB")

        # Requery
        chargers = (
            db.query(Charger)
            .filter(
                and_(
                    Charger.lat >= min_lat,
                    Charger.lat <= max_lat,
                    Charger.lng >= min_lng,
                    Charger.lng <= max_lng,
                    Charger.is_public == True,
                )
            )
            .limit(50)
            .all()
        )

    # Filter by drive time (rough estimate: 60 km/h average)
    # This is approximate - in production you might use routing API
    filtered = []
    for charger in chargers:
        distance_m = haversine_m(user_lat, user_lng, charger.lat, charger.lng)
        drive_time_min = (distance_m / 1000) / (60 / 60)  # km / (km/min) = minutes
        if drive_time_min <= max_drive_minutes:
            filtered.append(charger)

    logger.info(
        f"[WhileYouCharge] Filtered to {len(filtered)} chargers within {max_drive_minutes} min drive time"
    )
    return filtered


async def find_and_link_merchants(
    db: Session,
    chargers: List[Charger],
    category: Optional[str],
    merchant_name: Optional[str],
    max_walk_minutes: int = 10,
) -> List[Merchant]:
    """
    Find merchants linked to chargers, or fetch new ones from Google Places.
    """
    logger.info(
        f"[WhileYouCharge] Finding merchants for {len(chargers)} chargers, category={category}, name={merchant_name}"
    )

    charger_ids = [c.id for c in chargers]

    # Query existing linked merchants
    query = (
        db.query(Merchant)
        .join(ChargerMerchant)
        .filter(
            ChargerMerchant.charger_id.in_(charger_ids),
            ChargerMerchant.walk_duration_s <= max_walk_minutes * 60,
        )
    )

    if category:
        query = query.filter(Merchant.category == category)
    elif merchant_name:
        query = query.filter(Merchant.name.ilike(f"%{merchant_name}%"))

    # Note: Merchant.external_id is used for deduplication and linking with Google Places
    # It's backed by the merchants.external_id column (added in migration 013, ensured in 021)
    existing_merchants = query.distinct().all()
    logger.info(f"[WhileYouCharge] Found {len(existing_merchants)} existing merchants in DB")

    # If we have enough merchants, return them
    if len(existing_merchants) >= 5:
        logger.info(
            f"[WhileYouCharge] Returning {len(existing_merchants)} existing merchants (sufficient)"
        )
        return existing_merchants

    # Otherwise, fetch from Google Places
    # Search around each charger
    logger.info(
        f"[WhileYouCharge] Not enough merchants in DB ({len(existing_merchants)} < 5), fetching from Google Places..."
    )
    all_new_merchants = []

    for charger in chargers[:10]:  # Limit to avoid too many API calls
        logger.debug(f"Searching merchants near charger {charger.id} ({charger.name})")
        # Determine Google Places types / keyword
        if category:
            place_types, keyword = normalize_category_to_google_type(category)
        else:
            place_types = []
            keyword = merchant_name.lower() if merchant_name else None

        # Search for places
        places = await search_places_near(
            lat=charger.lat,
            lng=charger.lng,
            query=merchant_name,
            types=place_types if place_types else None,
            radius_m=800,  # 800m radius - reasonable walking distance
            limit=20,  # Get more results to filter down by walk time
            keyword=keyword,
        )

        logger.warning(
            "[WhileYouCharge] Charger %s (%s): Got %d places from Google, types=%s, keyword=%s, location=(%s,%s)",
            charger.id,
            charger.name,
            len(places),
            place_types,
            keyword,
            charger.lat,
            charger.lng,
        )

        if not places:
            logger.error(
                "[WhileYouCharge] ⚠️ No places returned for charger %s (%s) at (%s,%s) - check [PLACES] logs above",
                charger.id,
                charger.name,
                charger.lat,
                charger.lng,
            )
            continue

        # Get walk times for all places
        origins = [(charger.lat, charger.lng)]
        destinations = [(p.lat, p.lng) for p in places]

        if destinations:
            logger.error(
                "[WhileYouCharge] 📍 Getting walk times for %d places from charger %s at (%s,%s)",
                len(destinations),
                charger.id,
                charger.lat,
                charger.lng,
            )
            walk_times = await get_walk_times(origins, destinations)
            logger.error(
                "[WhileYouCharge] 📍 Walk times received: %d/%d places have walk info",
                len([k for k in walk_times.keys() if walk_times[k].get("status") == "OK"]),
                len(destinations),
            )

            places_filtered_by_walk = 0
            places_filtered_by_straight_distance = 0
            for place in places:
                # First, check straight-line distance (cheap check before walk time API call)
                straight_distance_m = haversine_m(charger.lat, charger.lng, place.lat, place.lng)
                # Filter out places more than 1.5km straight-line (walk distance will be longer)
                if straight_distance_m > 1500:
                    logger.error(
                        "[WhileYouCharge] ❌ Dropping place '%s': too far (straight-line distance=%dm, max=1500m)",
                        place.name,
                        int(straight_distance_m),
                    )
                    places_filtered_by_straight_distance += 1
                    continue

                dest = (place.lat, place.lng)
                walk_info = walk_times.get((origins[0], dest))

                if not walk_info:
                    logger.error(
                        "[WhileYouCharge] ❌ Dropping place '%s': no walk info from Distance Matrix. Place location=(%s,%s), charger=(%s,%s), straight_distance=%dm",
                        place.name,
                        place.lat,
                        place.lng,
                        charger.lat,
                        charger.lng,
                        int(straight_distance_m),
                    )
                    places_filtered_by_walk += 1
                    continue

                walk_seconds = walk_info["duration_s"]
                if walk_seconds > max_walk_minutes * 60:
                    logger.error(
                        "[WhileYouCharge] ❌ Dropping place '%s': walk_time=%ds (max=%ds). Walk distance: %dm, straight distance: %dm",
                        place.name,
                        walk_seconds,
                        max_walk_minutes * 60,
                        walk_info.get("distance_m", 0),
                        int(straight_distance_m),
                    )
                    places_filtered_by_walk += 1
                    continue

                logger.error(
                    "[WhileYouCharge] ✅ Keeping place '%s': walk_time=%ds, distance=%dm, location=(%s,%s)",
                    place.name,
                    walk_seconds,
                    walk_info.get("distance_m", 0),
                    place.lat,
                    place.lng,
                )

                # Check if merchant already exists
                existing = db.query(Merchant).filter(Merchant.external_id == place.place_id).first()

                if existing:
                    merchant = existing
                else:
                    # Get place details for more info (optional, may fail)
                    details = None
                    try:
                        details = await get_place_details(place.place_id)
                    except Exception as e:
                        logger.debug(f"Could not get place details for {place.place_id}: {e}")

                    # Create new merchant
                    merchant = Merchant(
                        id=f"m_{uuid.uuid4().hex[:12]}",
                        external_id=place.place_id,
                        name=place.name,
                        category=category or "other",
                        lat=place.lat,
                        lng=place.lng,
                        address=place.address
                        or (details.get("formatted_address") if details else ""),
                        rating=place.rating or (details.get("rating") if details else None),
                        price_level=place.price_level
                        or (details.get("price_level") if details else None),
                        place_types=place.types,
                        logo_url=place.icon,
                        photo_url=(
                            details.get("photos", [{}])[0].get("photo_reference")
                            if details
                            and details.get("photos")
                            and len(details.get("photos", [])) > 0
                            else None
                        ),
                        phone=details.get("formatted_phone_number") if details else None,
                        website=details.get("website") if details else None,
                    )
                    db.add(merchant)
                    db.flush()

                # Create or update charger-merchant link
                link = (
                    db.query(ChargerMerchant)
                    .filter(
                        and_(
                            ChargerMerchant.charger_id == charger.id,
                            ChargerMerchant.merchant_id == merchant.id,
                        )
                    )
                    .first()
                )

                if not link:
                    link = ChargerMerchant(
                        charger_id=charger.id,
                        merchant_id=merchant.id,
                        distance_m=haversine_m(
                            charger.lat, charger.lng, merchant.lat, merchant.lng
                        ),
                        walk_duration_s=walk_info["duration_s"],
                        walk_distance_m=walk_info.get("distance_m"),
                    )
                    db.add(link)

                all_new_merchants.append(merchant)
                logger.error(
                    "[WhileYouCharge] ✅ Added merchant '%s' (id=%s) for charger %s",
                    merchant.name,
                    merchant.id,
                    charger.id,
                )

    logger.error(
        "[WhileYouCharge] 📊 Before commit: %d merchants in all_new_merchants list",
        len(all_new_merchants),
    )
    db.commit()

    # Combine existing and new
    all_merchants = list(existing_merchants) + all_new_merchants
    # Deduplicate by ID
    seen = set()
    unique_merchants = []
    for m in all_merchants:
        if m.id not in seen:
            seen.add(m.id)
            unique_merchants.append(m)

    logger.warning(
        "[WhileYouCharge] SUMMARY: %d unique merchants total (%d existing from DB, %d newly created/linked). "
        "Check [PLACES] logs above for Google API status and [WhileYouCharge] logs for filtering details.",
        len(unique_merchants),
        len(existing_merchants),
        len(all_new_merchants),
    )
    return unique_merchants


def rank_merchants(
    db: Session,
    merchants: List[Merchant],
    chargers: List[Charger],
    user_lat: float,
    user_lng: float,
) -> List[Dict]:
    """
    Rank merchants by drive time, walk time, rating, and active perks.

    Returns list of dicts with merchant info and scores.
    """
    logger.info(f"[WhileYouCharge] Ranking {len(merchants)} merchants for {len(chargers)} chargers")

    charger_ids = [c.id for c in chargers]
    merchant_ids = [m.id for m in merchants]

    # Get all charger-merchant links
    links = (
        db.query(ChargerMerchant)
        .filter(
            ChargerMerchant.merchant_id.in_(merchant_ids),
            ChargerMerchant.charger_id.in_(charger_ids),
        )
        .all()
    )
    logger.debug(f"[WhileYouCharge] Found {len(links)} charger-merchant links")

    # Get active perks
    perks = (
        db.query(MerchantPerk)
        .filter(MerchantPerk.merchant_id.in_(merchant_ids), MerchantPerk.is_active == True)
        .all()
    )
    perks_by_merchant = {p.merchant_id: p for p in perks}
    logger.debug(f"[WhileYouCharge] Found {len(perks)} active perks")

    # Build merchant data with scores
    merchant_scores = []
    skipped_no_link = 0

    for merchant in merchants:
        # Find best charger link (shortest walk time)
        best_link = None
        best_walk_time = float("inf")
        linked_charger = None

        for link in links:
            if link.merchant_id == merchant.id:
                if link.walk_duration_s < best_walk_time:
                    best_walk_time = link.walk_duration_s
                    best_link = link
                    linked_charger = next((c for c in chargers if c.id == link.charger_id), None)

        if not best_link:
            skipped_no_link += 1
            continue  # Skip merchants without charger links

        # Calculate drive time to charger (rough estimate)
        if linked_charger:
            drive_distance_m = haversine_m(
                user_lat, user_lng, linked_charger.lat, linked_charger.lng
            )
            drive_time_min = (drive_distance_m / 1000) / (60 / 60)  # Approximate
        else:
            drive_time_min = 999

        # Get perk
        perk = perks_by_merchant.get(merchant.id)
        nova_reward = perk.nova_reward if perk else 10  # Default 10 Nova

        # Calculate score (lower is better)
        # Weight: drive time (40%), walk time (30%), rating (20%), perk bonus (10%)
        drive_score = drive_time_min * 0.4
        walk_score = (best_walk_time / 60) * 0.3
        rating_score = (5 - (merchant.rating or 3.5)) * 0.2  # Lower rating = higher score
        perk_bonus = (20 - nova_reward) * 0.1  # Higher reward = lower score

        total_score = drive_score + walk_score + rating_score + perk_bonus

        merchant_scores.append(
            {
                "merchant": merchant,
                "charger": linked_charger,
                "walk_time_s": best_walk_time,
                "walk_time_min": int(best_walk_time / 60),
                "drive_time_min": int(drive_time_min),
                "nova_reward": nova_reward,
                "perk": perk,
                "score": total_score,
            }
        )

    # Sort by score (ascending)
    merchant_scores.sort(key=lambda x: x["score"])

    logger.info(
        f"[WhileYouCharge] Ranked {len(merchant_scores)} merchants (skipped {skipped_no_link} without charger links)"
    )
    return merchant_scores


def get_domain_hub_view(db: Session) -> Dict:
    """
    Get Domain hub view with chargers and recommended merchants (sync version).

    NOTE: This sync version does NOT auto-fetch merchants (only reads from DB).
    Use get_domain_hub_view_async() in async contexts to enable auto-fetch.

    Returns:
        Dict with hub_id, hub_name, chargers, and merchants
    """
    # Always use sync-only version - bootstrap endpoint should be fast
    # Merchants will be fetched when while_you_charge endpoint is called (async)
    return _get_domain_hub_view_sync_only(db)


async def get_domain_hub_view_async(db: Session) -> Dict:
    """
    Async version of get_domain_hub_view that properly handles async merchant fetching.

    Uses Domain hub configuration to fetch chargers and find linked merchants.
    Automatically fetches merchants from Google Places if none exist.

    Returns:
        Dict with hub_id, hub_name, chargers, and merchants
    """
    from app.domains.domain_hub import DOMAIN_CHARGERS, HUB_ID, HUB_NAME

    logger.info("[DomainHub] Fetching Domain hub view (async)")

    # Get charger IDs from config
    charger_ids = [ch["id"] for ch in DOMAIN_CHARGERS]

    # Fetch chargers from DB
    chargers = db.query(Charger).filter(Charger.id.in_(charger_ids)).all()

    # Create a map of charger ID -> charger object
    chargers_by_id = {c.id: c for c in chargers}

    # Build charger list in config order (fallback to config if not in DB)
    charger_list = []
    for charger_config in DOMAIN_CHARGERS:
        charger_id = charger_config["id"]
        charger = chargers_by_id.get(charger_id)

        if charger:
            # Use DB charger data
            charger_list.append(
                {
                    "id": charger.id,
                    "name": charger.name,
                    "lat": charger.lat,
                    "lng": charger.lng,
                    "network_name": charger.network_name,
                    "logo_url": charger.logo_url,
                    "address": charger.address,
                    "radius_m": charger_config.get("radius_m", 1000),
                }
            )
        else:
            # Fallback to config data (charger not yet seeded)
            logger.warning(f"[DomainHub] Charger {charger_id} not found in DB, using config data")
            charger_list.append(
                {
                    "id": charger_config["id"],
                    "name": charger_config["name"],
                    "lat": charger_config["lat"],
                    "lng": charger_config["lng"],
                    "network_name": charger_config["network_name"],
                    "logo_url": None,
                    "address": charger_config.get("address"),
                    "radius_m": charger_config.get("radius_m", 1000),
                }
            )

    # Find merchants linked to Domain chargers
    merchant_ids = []
    if chargers:
        charger_ids_in_db = [c.id for c in chargers]
        print(
            f"[DomainHub] Looking for merchants linked to chargers: {charger_ids_in_db}", flush=True
        )
        links = (
            db.query(ChargerMerchant)
            .filter(ChargerMerchant.charger_id.in_(charger_ids_in_db))
            .all()
        )

        merchant_ids = list(set([link.merchant_id for link in links]))
        print(
            f"[DomainHub] Found {len(merchant_ids)} existing merchants in DB (from {len(links)} links)",
            flush=True,
        )
        logger.info(
            f"[DomainHub] Found {len(merchant_ids)} existing merchants in DB (from {len(links)} links)"
        )

    # If chargers aren't in DB, we need to create Charger objects from config for merchant fetching
    # OR create them in DB first (better long-term)
    chargers_for_fetching = list(chargers)  # Start with DB chargers
    if not chargers_for_fetching:
        logger.info(
            "[DomainHub] No chargers in DB, creating Charger objects from config for merchant fetching..."
        )
        for charger_config in DOMAIN_CHARGERS:
            # Try to get or create charger in DB
            existing = db.query(Charger).filter(Charger.id == charger_config["id"]).first()
            if existing:
                chargers_for_fetching.append(existing)
            else:
                # Create charger from config
                new_charger = Charger(
                    id=charger_config["id"],
                    external_id=charger_config.get("external_id"),
                    name=charger_config["name"],
                    network_name=charger_config["network_name"],
                    lat=charger_config["lat"],
                    lng=charger_config["lng"],
                    address=charger_config.get("address"),
                    city=charger_config.get("city"),
                    state=charger_config.get("state"),
                    zip_code=charger_config.get("zip_code"),
                    connector_types=charger_config.get("connector_types", []),
                    power_kw=charger_config.get("power_kw"),
                    is_public=charger_config.get("is_public", True),
                )
                db.add(new_charger)
                db.flush()  # Flush to get the object without committing yet
                chargers_for_fetching.append(new_charger)
                logger.info(f"[DomainHub] Created charger {new_charger.id} from config")
        try:
            db.commit()
            logger.info(f"[DomainHub] Committed {len(chargers_for_fetching)} chargers to DB")
        except Exception as e:
            logger.error(f"[DomainHub] Failed to commit chargers: {e}", exc_info=True)
            db.rollback()

    # If no merchants found, automatically fetch and link them (ASYNC)
    if not merchant_ids and chargers_for_fetching:
        print(
            "[DomainHub] 🔍🔍🔍 NO MERCHANTS FOUND! Starting auto-fetch from Google Places...",
            flush=True,
        )
        print(
            f"[DomainHub] 🔍 Have {len(chargers_for_fetching)} chargers to search around: {[c.id for c in chargers_for_fetching]}",
            flush=True,
        )
        logger.info(
            "[DomainHub] 🔍 No merchants found in DB, starting auto-fetch from Google Places..."
        )
        logger.info(f"[DomainHub] 🔍 Have {len(chargers_for_fetching)} chargers to search around")
        try:
            # Fetch merchants with multiple categories to get variety
            all_fetched_merchants = []
            categories_to_try = ["coffee", "food", "restaurant"]  # Start with 3 categories

            for category in categories_to_try:
                logger.info(
                    f"[DomainHub] 🔍 Fetching {category} merchants for {len(chargers_for_fetching)} chargers..."
                )
                try:
                    fetched_merchants = await find_and_link_merchants(
                        db=db,
                        chargers=chargers_for_fetching[
                            :2
                        ],  # Limit to first 2 chargers to avoid rate limits
                        category=category,
                        merchant_name=None,
                        max_walk_minutes=10,
                    )
                    logger.info(
                        f"[DomainHub] ✅ Got {len(fetched_merchants)} {category} merchants from find_and_link_merchants"
                    )
                    all_fetched_merchants.extend(fetched_merchants)

                    # Commit after each category to save progress
                    try:
                        db.commit()
                        logger.info(f"[DomainHub] ✅ Committed {category} merchants to DB")
                    except Exception as commit_err:
                        logger.error(
                            f"[DomainHub] ❌ Failed to commit {category} merchants: {commit_err}",
                            exc_info=True,
                        )
                        db.rollback()
                except Exception as fetch_err:
                    logger.error(
                        f"[DomainHub] ❌ Error fetching {category} merchants: {fetch_err}",
                        exc_info=True,
                    )
                    continue

            # Refresh merchant_ids from newly created links
            logger.info("[DomainHub] 🔍 Refreshing merchant links from DB...")
            # Expire all objects to force fresh DB query after commits
            db.expire_all()
            charger_ids_for_lookup = [c.id for c in chargers_for_fetching]
            links = (
                db.query(ChargerMerchant)
                .filter(ChargerMerchant.charger_id.in_(charger_ids_for_lookup))
                .all()
            )
            merchant_ids = list(set([link.merchant_id for link in links]))
            logger.info(f"[DomainHub] ✅ Total merchants after auto-fetch: {len(merchant_ids)}")

            # Verify merchants exist in DB
            if merchant_ids:
                merchant_count = db.query(Merchant).filter(Merchant.id.in_(merchant_ids)).count()
                logger.info(
                    f"[DomainHub] ✅ Verified {merchant_count} merchants exist in DB (expected {len(merchant_ids)})"
                )
        except Exception as e:
            logger.error(
                f"[DomainHub] ❌ Failed to fetch merchants automatically: {e}", exc_info=True
            )
            import traceback

            logger.error(f"[DomainHub] ❌ Traceback: {traceback.format_exc()}")
            db.rollback()
    elif not chargers:
        logger.warning("[DomainHub] ⚠️ No chargers available, cannot fetch merchants")

    # Fetch merchants - expire session to ensure we see newly committed data
    db.expire_all()
    merchants = []
    if merchant_ids:
        logger.info(f"[DomainHub] 🔍 Querying {len(merchant_ids)} merchants from DB...")
        merchants_query = db.query(Merchant).filter(Merchant.id.in_(merchant_ids)).all()
        logger.info(f"[DomainHub] ✅ Found {len(merchants_query)} merchants in DB query")

        # Get charger-merchant links for walk times
        charger_ids_for_lookup = (
            [c.id for c in chargers_for_fetching] if chargers_for_fetching else []
        )
        links = (
            db.query(ChargerMerchant)
            .filter(
                ChargerMerchant.merchant_id.in_(merchant_ids),
                ChargerMerchant.charger_id.in_(charger_ids_for_lookup),
            )
            .all()
        )
        logger.info(f"[DomainHub] ✅ Found {len(links)} charger-merchant links")

        # Create maps:
        # 1. merchant_id -> best link (shortest walk time) for overall merchant data
        # 2. charger_id -> list of links for that charger (to attach merchants to chargers)
        links_by_merchant = {}
        links_by_charger = {}
        for link in links:
            merchant_id = link.merchant_id
            charger_id = link.charger_id

            # Track best link per merchant (shortest walk time)
            if (
                merchant_id not in links_by_merchant
                or link.walk_duration_s < links_by_merchant[merchant_id].walk_duration_s
            ):
                links_by_merchant[merchant_id] = link

            # Group links by charger
            if charger_id not in links_by_charger:
                links_by_charger[charger_id] = []
            links_by_charger[charger_id].append(link)

        # Get active perks (or create default perks if none exist)
        perks = (
            db.query(MerchantPerk)
            .filter(MerchantPerk.merchant_id.in_(merchant_ids), MerchantPerk.is_active == True)
            .all()
        )
        perks_by_merchant = {p.merchant_id: p for p in perks}

        # Build merchant list with walk times and perks
        for merchant in merchants_query:
            link = links_by_merchant.get(merchant.id)
            perk = perks_by_merchant.get(merchant.id)

            # Create default perk if none exists (so merchants always have a reward)
            if not perk:
                logger.info(
                    f"[DomainHub] No perk for merchant {merchant.id} ({merchant.name}), creating default..."
                )
                # MerchantPerk is imported at module level (line 11)
                default_perk = MerchantPerk(
                    merchant_id=merchant.id, title="EV Rewards", nova_reward=10, is_active=True
                )
                db.add(default_perk)
                perk = default_perk
                perks_by_merchant[merchant.id] = perk

            merchant_data = {
                "id": merchant.id,
                "name": merchant.name,
                "lat": merchant.lat,
                "lng": merchant.lng,
                "category": merchant.category,
                "logo_url": merchant.logo_url,
                "photo_url": merchant.photo_url,  # Include photo_url so shape_merchant can convert Google Places photo references
                "address": merchant.address,
                "nova_reward": perk.nova_reward if perk else 10,
                "walk_minutes": int(link.walk_duration_s / 60) if link else None,
                "walk_distance_m": link.walk_distance_m if link else None,
                "distance_m": link.distance_m if link else None,
            }
            merchants.append(merchant_data)

        # Commit default perks if we created any
        if len(perks) < len(merchants):
            try:
                db.commit()
                logger.info(f"[DomainHub] ✅ Committed {len(merchants) - len(perks)} default perks")
            except Exception as e:
                logger.warning(f"[DomainHub] Failed to commit default perks: {e}")
                db.rollback()

        # Sort merchants by walk time (ascending)
        merchants.sort(key=lambda m: m.get("walk_minutes") or 999)
        logger.info(f"[DomainHub] ✅ Built {len(merchants)} merchant data objects")

        # Attach merchants to each charger in charger_list
        merchants_by_id = {m["id"]: m for m in merchants}
        print(f"[DomainHub] Attaching merchants to {len(charger_list)} chargers...", flush=True)

        for charger_data in charger_list:
            charger_id = charger_data.get("id")
            charger_links = links_by_charger.get(charger_id, [])
            print(f"[DomainHub] Charger {charger_id} has {len(charger_links)} links", flush=True)

            charger_merchants = []
            for link in charger_links:
                merchant_id = link.merchant_id
                if merchant_id in merchants_by_id:
                    merchant = dict(merchants_by_id[merchant_id])  # Copy to avoid mutation
                    # Add charger-specific walk time
                    merchant["walk_time_seconds"] = link.walk_duration_s
                    merchant["walk_minutes"] = int(link.walk_duration_s / 60)
                    merchant["walk_distance_meters"] = link.walk_distance_m or link.distance_m
                    charger_merchants.append(merchant)
                else:
                    print(
                        f"[DomainHub] ⚠️ Merchant {merchant_id} not found in merchants_by_id (has {len(merchants_by_id)} merchants)",
                        flush=True,
                    )

            charger_data["merchants"] = charger_merchants
            print(
                f"[DomainHub] ✅ Attached {len(charger_merchants)} merchants to charger {charger_id}",
                flush=True,
            )
            logger.info(
                f"[DomainHub] ✅ Attached {len(charger_merchants)} merchants to charger {charger_id}"
            )
    else:
        logger.warning("[DomainHub] ⚠️ No merchants available (merchant_ids is empty)")
        # Ensure all chargers have empty merchants array
        for charger_data in charger_list:
            charger_data["merchants"] = []

    print(
        f"[DomainHub] ✅✅✅ FINAL: {len(charger_list)} chargers, {len(merchants)} merchants",
        flush=True,
    )
    logger.info(f"[DomainHub] ✅ Final: {len(charger_list)} chargers, {len(merchants)} merchants")

    # Log each charger's merchant count
    for ch in charger_list:
        merchant_count = len(ch.get("merchants", []))
        print(
            f"[DomainHub] Charger {ch.get('id')} has {merchant_count} merchants in response",
            flush=True,
        )

    return {
        "hub_id": HUB_ID,
        "hub_name": HUB_NAME,
        "chargers": charger_list,
        "merchants": merchants,
    }


def build_recommended_merchants_from_chargers(chargers: List[Dict], limit: int = 20) -> List[Dict]:
    """
    Given the PWA-shaped chargers list (each with .merchants),
    aggregate a unique list of merchants, apply simple pilot-time heuristics,
    and return a sorted list for `recommended_merchants`.

    Args:
        chargers: List of charger dicts, each potentially having a "merchants" array
        limit: Maximum number of merchants to return

    Returns:
        Deduplicated and sorted list of recommended merchants
    """
    print(f"[WhileYouCharge][Agg] Starting aggregation for {len(chargers)} chargers", flush=True)
    all_merchants = []
    for ch in chargers:
        charger_id = ch.get("id") or ch.get("charger_id")
        merchants = ch.get("merchants") or []
        print(
            f"[WhileYouCharge][Agg] Charger {charger_id} has {len(merchants)} merchants", flush=True
        )
        logger.info(
            "[WhileYouCharge][Agg] Charger %s has %d merchants",
            charger_id,
            len(merchants),
        )
        for m in merchants:
            # Make sure merchant_id exists
            mid = m.get("merchant_id") or m.get("id")
            if not mid:
                logger.warning(
                    "[WhileYouCharge][Agg] Merchant missing ID, skipping: %s",
                    m.get("name", "unknown"),
                )
                continue
            # Copy to avoid mutating original
            m_copy = dict(m)
            m_copy["merchant_id"] = mid

            # Ensure nova_reward exists; for pilot default to 200 if missing
            if "nova_reward" not in m_copy or m_copy["nova_reward"] is None:
                m_copy["nova_reward"] = 200

            # Ensure we have walk_time and distance defaults
            # Try walk_time_s first, then walk_time_seconds, then walk_minutes * 60
            if "walk_time_seconds" not in m_copy:
                if "walk_time_s" in m_copy:
                    m_copy["walk_time_seconds"] = m_copy["walk_time_s"]
                elif "walk_minutes" in m_copy:
                    m_copy["walk_time_seconds"] = (m_copy.get("walk_minutes") or 0) * 60
                else:
                    m_copy["walk_time_seconds"] = m_copy.get("walk_time", 0) or 0
            if "walk_distance_meters" not in m_copy:
                m_copy["walk_distance_meters"] = (
                    m_copy.get("walk_distance_m") or m_copy.get("distance_meters", 0) or 0
                )

            all_merchants.append(m_copy)

    print(
        f"[WhileYouCharge][Agg] Collected {len(all_merchants)} raw merchants from {len(chargers)} chargers",
        flush=True,
    )
    logger.info(
        "[WhileYouCharge][Agg] Collected %d raw merchants from %d chargers",
        len(all_merchants),
        len(chargers),
    )

    # Deduplicate by merchant_id, prefer higher nova_reward then lower walk_time
    dedup = {}
    for m in all_merchants:
        mid = m["merchant_id"]
        existing = dedup.get(mid)
        if existing is None:
            dedup[mid] = m
        else:
            # Keep the one with higher nova_reward, then lower walk_time
            if m.get("nova_reward", 0) > existing.get("nova_reward", 0) or (
                m.get("nova_reward", 0) == existing.get("nova_reward", 0)
                and m.get("walk_time_seconds", 9999) < existing.get("walk_time_seconds", 9999)
            ):
                dedup[mid] = m

    merchants = list(dedup.values())
    print(f"[WhileYouCharge][Agg] After dedupe: {len(merchants)} unique merchants", flush=True)
    logger.info("[WhileYouCharge][Agg] After dedupe: %d unique merchants", len(merchants))

    # Sort by highest nova_reward, then shortest walk_time
    merchants.sort(
        key=lambda m: (
            -m.get("nova_reward", 0),
            m.get("walk_time_seconds", 9999),
        )
    )

    # Limit to something reasonable for PWA
    result = merchants[:limit]
    print(
        f"[WhileYouCharge][Agg] Returning {len(result)} merchants for recommended_merchants (limit={limit})",
        flush=True,
    )
    logger.info(
        "[WhileYouCharge][Agg] Returning %d merchants for recommended_merchants",
        len(result),
    )

    if len(result) == 0:
        print(
            "[WhileYouCharge][Agg] ⚠️⚠️⚠️ WARNING: Returning 0 merchants! Check if chargers have merchants attached.",
            flush=True,
        )

    return result


def build_recommended_merchants(merchants: List[Dict], limit: int = 20) -> List[Dict]:
    """
    Build recommended merchants list by deduplicating and sorting.
    DEPRECATED: Use build_recommended_merchants_from_chargers instead.

    Args:
        merchants: List of merchant dicts (may contain duplicates)
        limit: Maximum number of merchants to return

    Returns:
        Deduplicated and sorted list of recommended merchants
    """
    if not merchants:
        logger.warning("[RecommendedMerchants] No merchants provided")
        return []

    # Deduplicate by merchant ID, keeping the one with higher nova_reward
    seen = {}
    for merchant in merchants:
        mid = merchant.get("id")
        if not mid:
            continue

        if mid not in seen:
            seen[mid] = merchant
        else:
            # Keep the "better" one (higher nova_reward, or same reward but shorter walk time)
            current_reward = seen[mid].get("nova_reward", 0)
            new_reward = merchant.get("nova_reward", 0)
            current_walk = seen[mid].get("walk_minutes") or 9999
            new_walk = merchant.get("walk_minutes") or 9999

            if new_reward > current_reward or (
                new_reward == current_reward and new_walk < current_walk
            ):
                seen[mid] = merchant

    # Convert to list and sort
    merchant_list = list(seen.values())

    # Sort by: 1) highest nova_reward (descending), 2) lowest walk_time (ascending)
    merchant_list.sort(
        key=lambda m: (
            -m.get("nova_reward", 0),  # Negative for descending
            m.get("walk_minutes") or 9999,  # Ascending
        )
    )

    # Take top N
    recommended = merchant_list[:limit]
    logger.info(
        f"[RecommendedMerchants] Built {len(recommended)} recommended merchants from {len(merchants)} total (deduplicated from {len(seen)} unique)"
    )

    return recommended


def _get_domain_hub_view_sync_only(db: Session) -> Dict:
    """Sync-only version without auto-fetch (used when called from async context)."""
    from app.domains.domain_hub import DOMAIN_CHARGERS, HUB_ID, HUB_NAME

    logger.info("[DomainHub] Fetching Domain hub view (sync-only, no auto-fetch)")

    # Get charger IDs from config
    charger_ids = [ch["id"] for ch in DOMAIN_CHARGERS]

    # Fetch chargers from DB
    chargers = db.query(Charger).filter(Charger.id.in_(charger_ids)).all()

    # Build charger list
    chargers_by_id = {c.id: c for c in chargers}
    charger_list = []
    for charger_config in DOMAIN_CHARGERS:
        charger_id = charger_config["id"]
        charger = chargers_by_id.get(charger_id)

        if charger:
            charger_list.append(
                {
                    "id": charger.id,
                    "name": charger.name,
                    "lat": charger.lat,
                    "lng": charger.lng,
                    "network_name": charger.network_name,
                    "logo_url": charger.logo_url,
                    "address": charger.address,
                    "radius_m": charger_config.get("radius_m", 1000),
                }
            )
        else:
            charger_list.append(
                {
                    "id": charger_config["id"],
                    "name": charger_config["name"],
                    "lat": charger_config["lat"],
                    "lng": charger_config["lng"],
                    "network_name": charger_config["network_name"],
                    "logo_url": None,
                    "address": charger_config.get("address"),
                    "radius_m": charger_config.get("radius_m", 1000),
                }
            )

    # Find existing merchants only (no auto-fetch)
    merchant_ids = []
    if chargers:
        charger_ids_in_db = [c.id for c in chargers]
        links = (
            db.query(ChargerMerchant)
            .filter(ChargerMerchant.charger_id.in_(charger_ids_in_db))
            .all()
        )
        merchant_ids = list(set([link.merchant_id for link in links]))

    # Fetch and shape merchants
    merchants = []
    if merchant_ids:
        merchants_query = db.query(Merchant).filter(Merchant.id.in_(merchant_ids)).all()
        charger_ids_in_db = [c.id for c in chargers] if chargers else []
        links = (
            db.query(ChargerMerchant)
            .filter(
                ChargerMerchant.merchant_id.in_(merchant_ids),
                ChargerMerchant.charger_id.in_(charger_ids_in_db),
            )
            .all()
        )

        links_by_merchant = {}
        for link in links:
            merchant_id = link.merchant_id
            if (
                merchant_id not in links_by_merchant
                or link.walk_duration_s < links_by_merchant[merchant_id].walk_duration_s
            ):
                links_by_merchant[merchant_id] = link

        perks = (
            db.query(MerchantPerk)
            .filter(MerchantPerk.merchant_id.in_(merchant_ids), MerchantPerk.is_active == True)
            .all()
        )
        perks_by_merchant = {p.merchant_id: p for p in perks}

        for merchant in merchants_query:
            link = links_by_merchant.get(merchant.id)
            perk = perks_by_merchant.get(merchant.id)

            merchant_data = {
                "id": merchant.id,
                "name": merchant.name,
                "lat": merchant.lat,
                "lng": merchant.lng,
                "category": merchant.category,
                "logo_url": merchant.logo_url,
                "photo_url": merchant.photo_url,  # Include photo_url so shape_merchant can convert Google Places photo references
                "address": merchant.address,
                "nova_reward": perk.nova_reward if perk else 10,
                "walk_minutes": int(link.walk_duration_s / 60) if link else None,
                "walk_distance_m": link.walk_distance_m if link else None,
                "distance_m": link.distance_m if link else None,
            }
            merchants.append(merchant_data)

        merchants.sort(key=lambda m: m.get("walk_minutes") or 999)

    return {
        "hub_id": HUB_ID,
        "hub_name": HUB_NAME,
        "chargers": charger_list,
        "merchants": merchants,
    }
