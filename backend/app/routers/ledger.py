from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_extra import RewardEvent
from ..services.ledger import get_proof, verify_proof

router = APIRouter(prefix="/v1/ledger", tags=["ledger"])

@router.get("/proofs")
async def get_proof_by_event(
    event_id: int = Query(..., description="Event ID to look up"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get a proof by event ID."""
    try:
        # First check if the event exists
        event = db.query(RewardEvent).filter(RewardEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        
        # Get the proof
        proof = get_proof(event_id)
        if not proof:
            raise HTTPException(status_code=404, detail="Proof not found")
        
        return {
            "event_id": event_id,
            "proof": proof,
            "verified": verify_proof(proof["id"])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get proof: {str(e)}")

@router.get("/proofs/verify")
async def verify_proof_endpoint(
    proof_id: str = Query(..., description="Proof ID to verify")
):
    """Verify a proof by its ID."""
    try:
        is_valid = verify_proof(proof_id)
        return {
            "proof_id": proof_id,
            "valid": is_valid
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify proof: {str(e)}")

@router.get("/status")
async def ledger_status():
    """Get ledger status and configuration."""
    import os

    from ..services.ledger import ledger
    
    ledger_exists = os.path.exists(ledger.ledger_path)
    ledger_size = 0
    
    if ledger_exists:
        with open(ledger.ledger_path) as f:
            ledger_size = sum(1 for line in f)
    
    return {
        "enabled": True,  # LEDGER_ENABLED from config
        "ledger_path": ledger.ledger_path,
        "exists": ledger_exists,
        "entries": ledger_size,
        "provider": "local_jsonl",  # Could be "polygon", "filecoin", etc.
        "description": "Local append-only ledger. Can be swapped for blockchain providers."
    }
