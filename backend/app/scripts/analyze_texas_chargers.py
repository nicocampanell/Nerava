"""
Texas EV Charger Ranking Analysis

Identifies and ranks the Top 25 EV charger locations across Texas
(Dallas, Austin, San Antonio, Houston + intercity towns)
optimized for Nerava merchant monetization.

Usage:
    python -m app.scripts.analyze_texas_chargers
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os

from app.core.config import settings as core_settings
from app.scripts.data.texas_metro_bounds import get_all_search_locations
from app.scripts.franchise_exclusions import is_franchise
from app.services.geo import haversine_m
from app.services.google_places_new import place_details, search_nearby, search_text

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Use API key from environment — never hardcode secrets
HARDCODED_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")


# Charger search queries
CHARGER_QUERIES = [
    "Tesla Supercharger",
    "EVgo",
    "ChargePoint",
    "Electrify America",
]

# Merchant types to include (for SearchNearby)
MERCHANT_TYPES = [
    "restaurant",
    "cafe",
    "bar",
    "bakery",
    "dessert_shop",
    "gym",
    "coffee_shop",
]


def estimate_stall_count(charger_name: str, network: str) -> int:
    """
    Estimate stall count based on charger name and network.

    Args:
        charger_name: Name of the charger
        network: Network name (Tesla, EVgo, ChargePoint, etc.)

    Returns:
        Estimated stall count
    """
    name_lower = charger_name.lower()

    # Tesla Superchargers typically have 8-20+ stalls
    if "tesla" in network.lower() or "supercharger" in name_lower:
        # Some Tesla locations have stall counts in name or can infer from size
        if "v3" in name_lower or "v4" in name_lower:
            return 16  # V3/V4 sites typically larger
        return 12  # Default Tesla Supercharger

    # Other networks vary more, but we only include if >= 6
    if "evgo" in network.lower():
        return 8  # EVgo typically 4-10
    if "chargepoint" in network.lower():
        return 8  # ChargePoint varies
    if "electrify" in network.lower():
        return 10  # Electrify America typically 4-10

    return 6  # Conservative default for unknown networks


def infer_network(charger_name: str) -> str:
    """
    Infer network from charger name.

    Args:
        charger_name: Name of the charger

    Returns:
        Network name (Tesla, EVgo, ChargePoint, etc.)
    """
    name_lower = charger_name.lower()

    if "tesla" in name_lower or "supercharger" in name_lower:
        return "Tesla"
    if "evgo" in name_lower:
        return "EVgo"
    if "chargepoint" in name_lower or "charge point" in name_lower:
        return "ChargePoint"
    if "electrify" in name_lower:
        return "Electrify America"

    return "Unknown"


async def fetch_chargers_for_location(location: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch chargers for a specific location using Google Places API.

    Args:
        location: Location dict with lat, lng, radius_km, city, state

    Returns:
        List of charger dicts
    """
    chargers = []
    location_bias = {"lat": location["lat"], "lng": location["lng"]}

    logger.info(
        f"Searching chargers for {location.get('name', location.get('city', 'Unknown'))}..."
    )

    for query in CHARGER_QUERIES:
        try:
            results = await search_text(query=query, location_bias=location_bias, max_results=20)

            for result in results:
                charger_name = result.get("name", "")
                place_id = result.get("place_id", "")
                lat = result.get("lat", 0)
                lng = result.get("lng", 0)

                # Skip if no valid location
                if not lat or not lng:
                    continue

                # Infer network
                network = infer_network(charger_name)

                # Estimate stall count
                stall_count = estimate_stall_count(charger_name, network)

                # Filter: Tesla Superchargers include all, others only if >= 6 stalls
                if network != "Tesla" and stall_count < 6:
                    continue

                # Get place details for address
                address = "Address unavailable"
                try:
                    details = await place_details(place_id)
                    if details:
                        address = details.get("formattedAddress", address)
                except Exception as e:
                    logger.debug(f"Could not fetch details for {place_id}: {e}")

                charger = {
                    "name": charger_name,
                    "place_id": place_id,
                    "lat": lat,
                    "lng": lng,
                    "address": address,
                    "network": network,
                    "estimated_stall_count": stall_count,
                    "city": location.get("city", "Unknown"),
                    "state": location.get("state", "TX"),
                    "location_name": location.get("name", ""),
                }

                chargers.append(charger)

        except Exception as e:
            logger.error(f"Error searching for {query} in {location.get('city', 'Unknown')}: {e}")
            continue

    logger.info(
        f"Found {len(chargers)} chargers for {location.get('name', location.get('city', 'Unknown'))}"
    )
    return chargers


async def enrich_merchants_for_charger(charger: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Enrich merchants for a charger location within 400m radius.

    Args:
        charger: Charger dict with lat, lng

    Returns:
        List of merchant dicts filtered and enriched
    """
    merchants = []
    charger_lat = charger["lat"]
    charger_lng = charger["lng"]

    logger.info(f"Enriching merchants for {charger['name']} at ({charger_lat}, {charger_lng})...")

    try:
        # Search nearby merchants (400m radius)
        nearby_merchants = await search_nearby(
            lat=charger_lat,
            lng=charger_lng,
            radius_m=400,
            included_types=MERCHANT_TYPES,
            max_results=20,
        )

        # Enrich each merchant with place details
        for merchant_data in nearby_merchants:
            place_id = merchant_data.get("place_id", "")
            if not place_id:
                continue

            try:
                # Get place details for full information
                details = await place_details(place_id)
                if not details:
                    continue

                # Check business status
                business_status = details.get("businessStatus", "")
                if business_status != "OPERATIONAL":
                    continue

                # Extract merchant information
                display_name = details.get("displayName", {})
                merchant_name = (
                    display_name.get("text", "")
                    if isinstance(display_name, dict)
                    else str(display_name)
                )

                # Check if franchise
                if is_franchise(merchant_name, details):
                    continue

                # Get location
                location = details.get("location", {})
                merchant_lat = location.get("latitude", 0)
                merchant_lng = location.get("longitude", 0)

                # Calculate distance to charger
                distance_m = haversine_m(charger_lat, charger_lng, merchant_lat, merchant_lng)

                # Only include merchants within 400m (accounting for search radius variance)
                if distance_m > 400:
                    continue

                # Extract merchant data
                merchant = {
                    "name": merchant_name,
                    "place_id": place_id,
                    "lat": merchant_lat,
                    "lng": merchant_lng,
                    "distance_m": round(distance_m),
                    "rating": details.get("rating"),
                    "user_rating_count": details.get("userRatingCount", 0),
                    "price_level": details.get("priceLevel"),
                    "website": details.get("websiteUri"),
                    "phone": details.get("nationalPhoneNumber"),
                    "types": details.get("types", []),
                    "business_status": business_status,
                    "address": details.get("formattedAddress", ""),
                }

                merchants.append(merchant)

            except Exception as e:
                logger.debug(f"Error enriching merchant {place_id}: {e}")
                continue

        # Sort by distance
        merchants.sort(key=lambda x: x["distance_m"])

        logger.info(f"Found {len(merchants)} independent merchants for {charger['name']}")

    except Exception as e:
        logger.error(f"Error enriching merchants for {charger['name']}: {e}")

    return merchants


def calculate_charger_score(stall_count: int, network: str) -> float:
    """
    Calculate charger score (0-10) based on stall count and network.

    Args:
        stall_count: Estimated stall count
        network: Network name

    Returns:
        Score from 0-10
    """
    if network == "Tesla":
        if stall_count >= 24:
            return 10.0
        elif stall_count >= 16:
            return 8.0
        elif stall_count >= 8:
            return 6.0
        else:
            return 4.0
    else:
        # Non-Tesla networks (already filtered to >= 6)
        if stall_count >= 16:
            return 8.0
        elif stall_count >= 12:
            return 7.0
        elif stall_count >= 8:
            return 6.0
        else:
            return 5.0


def calculate_merchant_density_score(merchants: List[Dict[str, Any]]) -> float:
    """
    Calculate merchant density score (0-10) based on merchant count and proximity.

    Args:
        merchants: List of merchant dicts with distance_m

    Returns:
        Score from 0-10
    """
    if not merchants:
        return 0.0

    independent_count = len(merchants)
    within_200m = len([m for m in merchants if m["distance_m"] <= 200])
    within_400m = independent_count

    # Base score: min(10, count / 3)
    base_score = min(10.0, independent_count / 3.0)

    # Bonus: +2 if >= 5 merchants within 200m
    if within_200m >= 5:
        base_score += 2.0

    # Bonus: +1 if >= 10 merchants within 400m
    if within_400m >= 10:
        base_score += 1.0

    return min(10.0, base_score)


def calculate_merchant_quality_score(merchants: List[Dict[str, Any]]) -> float:
    """
    Calculate merchant quality score (0-10) based on ratings, price level, contact info.

    Args:
        merchants: List of merchant dicts

    Returns:
        Score from 0-10
    """
    if not merchants:
        return 0.0

    # Calculate average rating
    ratings = [m["rating"] for m in merchants if m.get("rating") is not None]
    avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

    # Count merchants with $$ or $$$ price level
    price_level_count = len([m for m in merchants if m.get("price_level") in [2, 3]])
    price_level_score = (price_level_count / len(merchants)) * 3.0

    # Count merchants with website
    website_count = len([m for m in merchants if m.get("website")])
    website_score = (website_count / len(merchants)) * 2.0

    # Count merchants with phone
    phone_count = len([m for m in merchants if m.get("phone")])
    phone_score = (phone_count / len(merchants)) * 1.0

    # Rating score: +4 if avg >= 4.2
    rating_score = 4.0 if avg_rating >= 4.2 else (avg_rating / 4.2) * 4.0

    total_score = rating_score + price_level_score + website_score + phone_score
    return min(10.0, total_score)


def calculate_monetization_likelihood_score(merchants: List[Dict[str, Any]]) -> float:
    """
    Calculate monetization likelihood score (0-10) based on contact info and category alignment.

    Args:
        merchants: List of merchant dicts

    Returns:
        Score from 0-10
    """
    if not merchants:
        return 0.0

    total = len(merchants)

    # % merchants with phone + website
    contact_count = len([m for m in merchants if m.get("phone") and m.get("website")])
    contact_score = (contact_count / total) * 10.0 if total > 0 else 0.0

    # Categories aligned with exclusives (food/drink/fitness)
    aligned_types = ["restaurant", "cafe", "bar", "bakery", "dessert_shop", "coffee_shop", "gym"]
    aligned_count = len(
        [m for m in merchants if any(t in aligned_types for t in m.get("types", []))]
    )
    aligned_score = (aligned_count / total) * 3.0 if total > 0 else 0.0

    # Non-franchise concentration (all are already filtered, so this is just normalization)
    independent_score = 7.0  # All merchants are already independent (franchises filtered out)

    total_score = contact_score + aligned_score + independent_score
    return min(10.0, total_score)


def calculate_scores(charger: Dict[str, Any], merchants: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Calculate all scores for a charger location.

    Args:
        charger: Charger dict
        merchants: List of merchant dicts

    Returns:
        Dict with all scores and final score
    """
    charger_score = calculate_charger_score(charger["estimated_stall_count"], charger["network"])

    density_score = calculate_merchant_density_score(merchants)
    quality_score = calculate_merchant_quality_score(merchants)
    monetization_score = calculate_monetization_likelihood_score(merchants)

    # Final weighted score (favoring merchant quality + density)
    final_score = (
        charger_score * 0.15
        + density_score * 0.30
        + quality_score * 0.30
        + monetization_score * 0.25
    )

    return {
        "charger": round(charger_score, 2),
        "merchant_density": round(density_score, 2),
        "merchant_quality": round(quality_score, 2),
        "monetization_likelihood": round(monetization_score, 2),
        "final": round(final_score, 2),
    }


def recommend_anchor_merchant_type(merchants: List[Dict[str, Any]]) -> str:
    """
    Recommend first anchor merchant type based on top merchants.

    Args:
        merchants: List of merchant dicts (sorted by distance/rating)

    Returns:
        Recommended merchant type string
    """
    if not merchants:
        return "Unknown"

    # Get top 5 merchants by rating + distance
    top_merchants = sorted(
        merchants, key=lambda m: (-(m.get("rating", 0) or 0), m.get("distance_m", 999))
    )[:5]

    # Map Google Places types to merchant categories
    type_to_category = {
        "restaurant": "Restaurant",
        "mexican_restaurant": "Mexican restaurant",
        "italian_restaurant": "Italian restaurant",
        "american_restaurant": "American restaurant",
        "asian_restaurant": "Asian restaurant",
        "chinese_restaurant": "Chinese restaurant",
        "japanese_restaurant": "Japanese restaurant",
        "indian_restaurant": "Indian restaurant",
        "cafe": "Coffee shop",
        "coffee_shop": "Coffee shop",
        "bakery": "Bakery",
        "bar": "Bar",
        "gym": "Gym",
        "fitness_center": "Gym",
        "dessert_shop": "Dessert shop",
    }

    # Count category frequencies
    category_counts = {}
    for merchant in top_merchants:
        types = merchant.get("types", [])
        for place_type in types:
            category = type_to_category.get(place_type)
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1

    # Find most common category
    if category_counts:
        most_common = max(category_counts.items(), key=lambda x: x[1])
        return most_common[0]

    # Fallback: Use highest-rated merchant's primary type
    if top_merchants:
        top_merchant = top_merchants[0]
        types = top_merchant.get("types", [])
        for place_type in types:
            category = type_to_category.get(place_type)
            if category:
                return category

    return "Restaurant"  # Default fallback


async def main():
    """Main analysis function."""
    logger.info("Starting Texas EV Charger Ranking Analysis...")

    # Use hardcoded API key if environment variable is not set
    if not core_settings.GOOGLE_PLACES_API_KEY:
        logger.info("GOOGLE_PLACES_API_KEY not set in environment, using hardcoded key")
        # Set environment variable for future imports
        os.environ["GOOGLE_PLACES_API_KEY"] = HARDCODED_API_KEY
        # Update the core_settings object directly
        core_settings.GOOGLE_PLACES_API_KEY = HARDCODED_API_KEY
        # Also update the google_places_new module's settings
        from app.services import google_places_new

        google_places_new.core_settings.GOOGLE_PLACES_API_KEY = HARDCODED_API_KEY

    logger.info(f"Using Google Places API key: {core_settings.GOOGLE_PLACES_API_KEY[:20]}...")

    # Get all search locations
    locations = get_all_search_locations()
    logger.info(f"Analyzing {len(locations)} locations...")

    # Phase 1: Fetch all chargers
    all_chargers = []
    for location in locations:
        chargers = await fetch_chargers_for_location(location)
        all_chargers.extend(chargers)
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)

    logger.info(f"Found {len(all_chargers)} total chargers")

    # Deduplicate chargers by place_id
    seen_place_ids = set()
    unique_chargers = []
    for charger in all_chargers:
        place_id = charger.get("place_id")
        if place_id and place_id not in seen_place_ids:
            seen_place_ids.add(place_id)
            unique_chargers.append(charger)

    logger.info(f"After deduplication: {len(unique_chargers)} unique chargers")

    # Phase 2: Enrich merchants for each charger
    chargers_with_merchants = []
    for i, charger in enumerate(unique_chargers, 1):
        logger.info(f"Processing charger {i}/{len(unique_chargers)}: {charger['name']}")
        merchants = await enrich_merchants_for_charger(charger)

        charger_data = {
            "charger": charger,
            "merchants": merchants,
            "merchant_count_200m": len([m for m in merchants if m["distance_m"] <= 200]),
            "merchant_count_400m": len(merchants),
        }

        chargers_with_merchants.append(charger_data)

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)

    # Phase 3: Calculate scores
    chargers_with_scores = []
    for data in chargers_with_merchants:
        scores = calculate_scores(data["charger"], data["merchants"])
        anchor_type = recommend_anchor_merchant_type(data["merchants"])

        result = {
            "charger": data["charger"],
            "merchants": {
                "total_count": data["merchant_count_400m"],
                "within_200m": data["merchant_count_200m"],
                "within_400m": data["merchant_count_400m"],
                "top_5": data["merchants"][:5],  # Top 5 by distance
            },
            "scores": scores,
            "recommended_first_anchor_merchant_type": anchor_type,
        }

        chargers_with_scores.append(result)

    # Phase 4: Rank by final score
    chargers_with_scores.sort(key=lambda x: x["scores"]["final"], reverse=True)

    # Get Top 25 and Next Tier 26-35
    top_25 = chargers_with_scores[:25]
    next_tier = chargers_with_scores[25:35]

    logger.info(f"Ranked {len(chargers_with_scores)} chargers")
    logger.info(
        f"Top 25 final scores range: {top_25[0]['scores']['final']} - {top_25[-1]['scores']['final']}"
    )

    # Phase 5: Generate output
    output_data = {
        "top_25": [{**item, "rank": i + 1} for i, item in enumerate(top_25)],
        "next_tier_26_35": [{**item, "rank": i + 26} for i, item in enumerate(next_tier)],
        "metadata": {
            "analysis_date": datetime.now().isoformat(),
            "total_chargers_analyzed": len(unique_chargers),
            "total_merchants_enriched": sum(len(d["merchants"]) for d in chargers_with_merchants),
        },
    }

    # Save JSON output
    output_path = Path(__file__).parent.parent.parent.parent / "texas_charger_rankings.json"
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"Saved JSON output to {output_path}")

    # Generate Markdown table
    markdown_path = Path(__file__).parent.parent.parent.parent / "texas_charger_rankings.md"
    generate_markdown_table(output_data, markdown_path)

    logger.info(f"Saved Markdown output to {markdown_path}")
    logger.info("Analysis complete!")


def generate_markdown_table(output_data: Dict[str, Any], output_path: Path):
    """Generate human-readable Markdown table output."""
    lines = [
        "# Texas EV Charger Rankings - Top 25",
        "",
        f"**Analysis Date:** {output_data['metadata']['analysis_date']}",
        f"**Total Chargers Analyzed:** {output_data['metadata']['total_chargers_analyzed']}",
        f"**Total Merchants Enriched:** {output_data['metadata']['total_merchants_enriched']}",
        "",
        "## Top 25 Locations",
        "",
        "| Rank | City | Charger Name | Network | Stalls | Merchants (400m) | Final Score | Recommended Anchor Type |",
        "|------|------|--------------|---------|--------|------------------|-------------|------------------------|",
    ]

    for item in output_data["top_25"]:
        charger = item["charger"]
        rank = item["rank"]
        city = charger.get("city", "Unknown")
        name = charger.get("name", "Unknown")
        network = charger.get("network", "Unknown")
        stalls = charger.get("estimated_stall_count", 0)
        merchant_count = item["merchants"]["within_400m"]
        final_score = item["scores"]["final"]
        anchor_type = item["recommended_first_anchor_merchant_type"]

        lines.append(
            f"| {rank} | {city} | {name[:40]} | {network} | {stalls} | {merchant_count} | {final_score} | {anchor_type} |"
        )

    lines.extend(
        [
            "",
            "## Next Tier (26-35)",
            "",
            "| Rank | City | Charger Name | Network | Stalls | Merchants (400m) | Final Score | Recommended Anchor Type |",
            "|------|------|--------------|---------|--------|------------------|-------------|------------------------|",
        ]
    )

    for item in output_data["next_tier_26_35"]:
        charger = item["charger"]
        rank = item["rank"]
        city = charger.get("city", "Unknown")
        name = charger.get("name", "Unknown")
        network = charger.get("network", "Unknown")
        stalls = charger.get("estimated_stall_count", 0)
        merchant_count = item["merchants"]["within_400m"]
        final_score = item["scores"]["final"]
        anchor_type = item["recommended_first_anchor_merchant_type"]

        lines.append(
            f"| {rank} | {city} | {name[:40]} | {network} | {stalls} | {merchant_count} | {final_score} | {anchor_type} |"
        )

    # Add top 5 standout notes
    lines.extend(
        [
            "",
            "## Top 5 Standout Locations",
            "",
        ]
    )

    for i, item in enumerate(output_data["top_25"][:5], 1):
        charger = item["charger"]
        scores = item["scores"]
        merchants = item["merchants"]
        top_merchants = item["merchants"]["top_5"]

        lines.append(f"### {i}. {charger['name']} ({charger['city']})")
        lines.append(f"- **Final Score:** {scores['final']}")
        lines.append(
            f"- **Charger Score:** {scores['charger']} ({charger['estimated_stall_count']} stalls, {charger['network']})"
        )
        lines.append(
            f"- **Merchant Density:** {scores['merchant_density']} ({merchants['within_400m']} merchants within 400m, {merchants['within_200m']} within 200m)"
        )
        lines.append(f"- **Merchant Quality:** {scores['merchant_quality']}")
        lines.append(f"- **Monetization Likelihood:** {scores['monetization_likelihood']}")
        lines.append(
            f"- **Recommended Anchor Type:** {item['recommended_first_anchor_merchant_type']}"
        )
        lines.append("- **Top Merchants:**")
        for merchant in top_merchants[:3]:
            lines.append(
                f"  - {merchant['name']} ({merchant['distance_m']}m, {merchant.get('rating', 'N/A')}⭐)"
            )
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
