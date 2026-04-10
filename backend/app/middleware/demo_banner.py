"""
Demo banner middleware to add scenario headers.
"""
import json

from app.core.config import is_demo
from app.dependencies import get_db
from app.models_demo import DemoState
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class DemoBannerMiddleware(BaseHTTPMiddleware):
    """Add demo scenario headers when in demo mode."""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        if is_demo():
            # Add demo header
            response.headers["x-nerava-demo"] = "true"
            
            # Get current scenario state
            try:
                db = next(get_db())
                states = db.query(DemoState).all()
                scenario = {
                    "grid_state": "offpeak",
                    "merchant_shift": "balanced", 
                    "rep_profile": "medium",
                    "city": "austin"
                }
                
                for state in states:
                    scenario[state.key] = state.value
                
                response.headers["x-nerava-scenario"] = json.dumps(scenario)
            except Exception:
                # Fallback to defaults if database query fails
                response.headers["x-nerava-scenario"] = json.dumps({
                    "grid_state": "offpeak",
                    "merchant_shift": "balanced", 
                    "rep_profile": "medium",
                    "city": "austin"
                })
        
        return response
