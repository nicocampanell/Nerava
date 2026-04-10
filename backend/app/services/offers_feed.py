"""
External offers feed provider interface
"""
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.utils.log import get_logger

logger = get_logger(__name__)


def fetch_external_offers(db: Session, merchant_id: int) -> List[Dict[str, Any]]:
    """
    Fetch external offers for a merchant from provider(s).
    
    For now, returns a stub offer.
    In production, this would call external APIs (e.g., CLO, affiliate networks).
    
    Returns:
        List of {
            "title": str,
            "window_start": str (time),
            "window_end": str (time),
            "est_reward_cents": int,
            "source": str
        }
    """
    # Stub: return one demo offer
    return [
        {
            "title": "Demo External Offer",
            "window_start": "14:00:00",
            "window_end": "16:00:00",
            "est_reward_cents": 250,
            "source": "external_stub"
        }
    ]
    
    # TODO: In production, implement:
    # 1. Lookup merchant external_id
    # 2. Call CLO/affiliate API with merchant_id
    # 3. Parse response and normalize to our format
    # 4. Cache results (Redis/memory) for TTL

