import math
from typing import Any, Dict, List


def score_hub(hub: Dict[str, Any], user_id: str, context: Dict[str, Any]) -> float:
    """
    Score a hub based on expected reward, merchant value, social presence, and distance.
    
    Args:
        hub: Hub data with lat, lng, name, etc.
        user_id: Current user ID
        context: Context like user_lat, user_lng, preferences
    
    Returns:
        Score (higher is better)
    """
    # Base score components
    expected_reward = 100  # Base reward expectation
    merchant_value = 50   # Merchant partnership value
    social_presence = 25  # Social activity around hub
    
    # Distance penalty (closer is better)
    user_lat = context.get('user_lat', 0)
    user_lng = context.get('user_lng', 0)
    hub_lat = hub.get('lat', 0)
    hub_lng = hub.get('lng', 0)
    
    distance_km = haversine_distance(user_lat, user_lng, hub_lat, hub_lng)
    distance_cost = max(1, distance_km * 0.1)  # Penalty per km
    
    # Calculate final score
    score = (expected_reward + merchant_value + social_presence) / distance_cost
    
    return score

def score_perk(perk: Dict[str, Any], user_id: str, context: Dict[str, Any]) -> float:
    """
    Score a perk based on user preferences and value.
    
    Args:
        perk: Perk data with name, description, value, etc.
        user_id: Current user ID
        context: Context like user preferences, time of day
    
    Returns:
        Score (higher is better)
    """
    base_score = 50
    
    # Time-based scoring (coffee in morning, etc.)
    hour = context.get('hour', 12)
    if 'coffee' in perk.get('name', '').lower() and 6 <= hour <= 10 or 'lunch' in perk.get('name', '').lower() and 11 <= hour <= 14:
        base_score += 30
    
    # Value-based scoring
    value = perk.get('value_cents', 0)
    if value > 0:
        base_score += min(50, value / 10)  # Cap at 50 bonus points
    
    return base_score

def rank_hubs_and_perks(user_id: str, lat: float, lng: float, 
                        hubs: List[Dict[str, Any]], 
                        perks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Rank hubs and perks for a user.
    
    Args:
        user_id: Current user ID
        lat: User latitude
        lng: User longitude
        hubs: List of available hubs
        perks: List of available perks
        
    Returns:
        Dict with ranked hubs and perks with reasons
    """
    context = {
        'user_lat': lat,
        'user_lng': lng,
        'hour': 12  # Could be dynamic based on current time
    }
    
    # Score and rank hubs
    hub_scores = []
    for hub in hubs:
        score = score_hub(hub, user_id, context)
        hub_scores.append({
            'hub': hub,
            'score': score,
            'reason': "High value hub with good rewards and proximity"
        })
    
    # Sort by score (highest first)
    hub_scores.sort(key=lambda x: x['score'], reverse=True)
    
    # Score and rank perks
    perk_scores = []
    for perk in perks:
        score = score_perk(perk, user_id, context)
        perk_scores.append({
            'perk': perk,
            'score': score,
            'reason': f"Great value perk with {perk.get('name', 'unknown')}"
        })
    
    # Sort by score (highest first)
    perk_scores.sort(key=lambda x: x['score'], reverse=True)
    
    return {
        'ranked_hubs': hub_scores[:5],  # Top 5
        'ranked_perks': perk_scores[:3],  # Top 3
        'user_context': context
    }

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers."""
    R = 6371  # Earth's radius in km
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat/2) * math.sin(dlat/2) + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon/2) * math.sin(dlon/2))
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance = R * c
    
    return distance
