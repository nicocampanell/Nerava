
from fastapi import APIRouter, HTTPException, Query

from ..services import hubs_dynamic
from ..services.ml_ranker import rank_hubs_and_perks, score_hub

router = APIRouter(prefix="/v1/ml", tags=["ml"])

@router.get("/recommend/hubs")
async def recommend_hubs(
    user_id: str = Query(..., description="User ID"),
    lat: float = Query(..., description="User latitude"),
    lng: float = Query(..., description="User longitude"),
    limit: int = Query(5, description="Number of recommendations")
):
    """Get personalized hub recommendations for a user."""
    try:
        # Get dynamic hubs
        hubs = await hubs_dynamic.build_dynamic_hubs(lat=lat, lng=lng, radius_km=5.0, max_results=20)
        
        # Get recommendations
        result = rank_hubs_and_perks(user_id, lat, lng, hubs, [])
        
        return {
            'user_id': user_id,
            'recommendations': result['ranked_hubs'][:limit],
            'context': result['user_context']
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommendation failed: {str(e)}")

@router.get("/recommend/perks")
async def recommend_perks(
    user_id: str = Query(..., description="User ID"),
    hub_id: str = Query(..., description="Hub ID"),
    limit: int = Query(3, description="Number of recommendations")
):
    """Get personalized perk recommendations for a user at a specific hub."""
    try:
        # For now, return mock perks since we don't have a Perk model
        # In a real implementation, you'd query the database for perks
        mock_perks = [
            {
                'id': 'perk_1',
                'name': 'Coffee & Pastry',
                'description': 'Free coffee and pastry with charging',
                'value_cents': 500,
                'hub_id': hub_id
            },
            {
                'id': 'perk_2', 
                'name': 'Parking Validation',
                'description': '2 hours free parking',
                'value_cents': 300,
                'hub_id': hub_id
            },
            {
                'id': 'perk_3',
                'name': 'WiFi Access',
                'description': 'Premium WiFi during charging',
                'value_cents': 100,
                'hub_id': hub_id
            }
        ]
        
        # Get recommendations
        result = rank_hubs_and_perks(user_id, 0, 0, [], mock_perks)
        
        return {
            'user_id': user_id,
            'hub_id': hub_id,
            'recommendations': result['ranked_perks'][:limit],
            'context': result['user_context']
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Perk recommendation failed: {str(e)}")

@router.get("/score/hub")
async def score_hub_endpoint(
    hub_id: str = Query(..., description="Hub ID"),
    user_id: str = Query(..., description="User ID"),
    lat: float = Query(..., description="User latitude"),
    lng: float = Query(..., description="User longitude")
):
    """Get a score for a specific hub."""
    try:
        # Get hub data from dynamic hubs
        hubs = await hubs_dynamic.build_dynamic_hubs(lat=lat, lng=lng, radius_km=5.0, max_results=20)
        hub_data = next((h for h in hubs if h.get('id') == hub_id), None)
        
        if not hub_data:
            raise HTTPException(status_code=404, detail="Hub not found")
        
        context = {'user_lat': lat, 'user_lng': lng}
        score = score_hub(hub_data, user_id, context)
        
        return {
            'hub_id': hub_id,
            'score': score,
            'reason': "Hub scored based on location, rewards, and social factors"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scoring failed: {str(e)}")
