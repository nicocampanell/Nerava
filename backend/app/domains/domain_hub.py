"""
Domain Hub Configuration

Single source of truth for The Domain hub in Austin.
Defines chargers and pilot merchant IDs for the Domain pilot.
"""
from typing import Any, Dict, List

# Hub identifiers
HUB_ID = "domain"
HUB_NAME = "Domain – Austin"

# Domain charger locations
# Based on Tesla and ChargePoint chargers at The Domain shopping center
DOMAIN_CHARGERS: List[Dict[str, Any]] = [
    {
        "id": "ch_domain_tesla_001",
        "external_id": None,  # Will be populated from NREL if available
        "name": "Tesla Supercharger – Domain",
        "network_name": "Tesla",
        "lat": 30.4021,
        "lng": -97.7266,
        "address": "11601 Domain Dr, Austin, TX 78758",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78758",
        "connector_types": ["Tesla"],
        "power_kw": 250.0,
        "is_public": True,
        "radius_m": 1000,  # Search radius for nearby merchants
    },
    {
        "id": "ch_domain_chargepoint_001",
        "external_id": None,
        "name": "ChargePoint – Domain Shopping Center",
        "network_name": "ChargePoint",
        "lat": 30.4039,
        "lng": -97.7250,
        "address": "11601 Domain Dr, Austin, TX 78758",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78758",
        "connector_types": ["CCS", "CHAdeMO"],
        "power_kw": 62.5,
        "is_public": True,
        "radius_m": 1000,
    },
    {
        "id": "ch_domain_chargepoint_002",
        "external_id": None,
        "name": "ChargePoint – Domain Parking Garage",
        "network_name": "ChargePoint",
        "lat": 30.4025,
        "lng": -97.7258,
        "address": "11400 Century Oaks Terrace, Austin, TX 78758",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78758",
        "connector_types": ["CCS", "J1772"],
        "power_kw": 7.2,
        "is_public": True,
        "radius_m": 800,
    },
]

# Pilot merchant IDs
# These should match merchant IDs used/seeded elsewhere
# For now, these are placeholders - actual IDs will be determined when merchants are seeded
DOMAIN_MERCHANT_IDS: List[str] = [
    # Placeholder merchant IDs - these will be populated when Domain merchants are seeded
    # Example format: "m_domain_starbucks_001", "m_domain_target_001", etc.
    # Actual IDs will be generated during seeding process
]

