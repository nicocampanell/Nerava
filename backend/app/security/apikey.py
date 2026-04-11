"""
API key validation and scoping.
"""
import os
from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import is_demo

# Mock API key storage - in production this would be in database
_API_KEYS = {
    "nerava-verify-key-2024": {
        "scopes": ["verify:charge"],
        "active": True
    },
    "nerava-admin-key-2024": {
        "scopes": ["admin:all"],
        "active": True
    },
    "nerava-merchant-key-2024": {
        "scopes": ["merchant:read", "merchant:write"],
        "active": True
    },
    "demo_admin_key": {
        "scopes": ["admin:demo", "verify:charge"],
        "active": True
    }
}

def ensure_demo_api_key():
    """Ensure demo API key exists when in demo mode."""
    if is_demo():
        # In demo mode, ensure the demo key exists
        if "demo_admin_key" not in _API_KEYS:
            _API_KEYS["demo_admin_key"] = {
                "scopes": ["admin:demo", "verify:charge"],
                "active": True
            }

def require_api_key(scope: str):
    """Dependency to require API key with specific scope."""
    def api_key_checker(x_nerava_key: Optional[str] = Header(None, alias="X-Nerava-Key")):
        if not x_nerava_key:
            raise HTTPException(status_code=403, detail="Missing X-Nerava-Key header")
        
        # Check environment variable first
        env_key = os.getenv("NERAVA_API_KEY")
        if env_key and x_nerava_key == env_key:
            return {"api_key": x_nerava_key, "scopes": ["admin:all"]}
        
        # Check in-memory storage
        key_data = _API_KEYS.get(x_nerava_key)
        if not key_data or not key_data["active"]:
            raise HTTPException(status_code=403, detail="Invalid or inactive API key")
        
        if scope not in key_data["scopes"] and "admin:all" not in key_data["scopes"]:
            raise HTTPException(
                status_code=403,
                detail=f"API key missing required scope: {scope}. Available: {key_data['scopes']}"
            )
        
        return {"api_key": x_nerava_key, "scopes": key_data["scopes"]}
    
    return api_key_checker
