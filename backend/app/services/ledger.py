import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from ..models_extra import RewardEvent


class LedgerService:
    """Local append-only ledger for reward proofs. Can be swapped for blockchain later."""
    
    def __init__(self, ledger_path: str = "data/ledger.jsonl"):
        self.ledger_path = ledger_path
        self.ensure_ledger_dir()
    
    def ensure_ledger_dir(self):
        """Ensure the ledger directory exists."""
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
    
    def record_reward_proof(self, event: RewardEvent) -> Dict[str, Any]:
        """
        Record a reward proof in the append-only ledger.
        
        Args:
            event: RewardEvent instance
            
        Returns:
            Dict with proof_id and hash
        """
        # Create proof entry
        proof_entry = {
            "id": f"proof_{event.id}_{int(datetime.utcnow().timestamp())}",
            "timestamp": datetime.utcnow().isoformat(),
            "event_id": event.id,
            "user_id": event.user_id,
            "gross_cents": event.gross_cents,
            "net_cents": event.net_cents,
            "community_cents": event.community_cents,
            "source": event.source,
            "meta": event.meta or {}
        }
        
        # Create hash of the entry
        entry_json = json.dumps(proof_entry, sort_keys=True)
        entry_hash = hashlib.sha256(entry_json.encode()).hexdigest()
        proof_entry["hash"] = entry_hash
        
        # Append to ledger
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps(proof_entry) + "\n")
        
        return {
            "proof_id": proof_entry["id"],
            "hash": entry_hash,
            "timestamp": proof_entry["timestamp"]
        }
    
    def get_proof(self, event_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a proof by event ID.
        
        Args:
            event_id: Event ID to look up
            
        Returns:
            Proof entry or None if not found
        """
        if not os.path.exists(self.ledger_path):
            return None
        
        with open(self.ledger_path) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("event_id") == event_id:
                        return entry
                except json.JSONDecodeError:
                    continue
        
        return None
    
    def verify_proof(self, proof_id: str) -> bool:
        """
        Verify a proof by checking its hash.
        
        Args:
            proof_id: Proof ID to verify
            
        Returns:
            True if proof is valid, False otherwise
        """
        if not os.path.exists(self.ledger_path):
            return False
        
        with open(self.ledger_path) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("id") == proof_id:
                        # Recalculate hash
                        entry_copy = entry.copy()
                        stored_hash = entry_copy.pop("hash")
                        entry_json = json.dumps(entry_copy, sort_keys=True)
                        calculated_hash = hashlib.sha256(entry_json.encode()).hexdigest()
                        return stored_hash == calculated_hash
                except json.JSONDecodeError:
                    continue
        
        return False

# Global ledger instance
ledger = LedgerService()

def record_reward_proof(event: RewardEvent) -> Dict[str, Any]:
    """Record a reward proof in the ledger."""
    return ledger.record_reward_proof(event)

def get_proof(event_id: int) -> Optional[Dict[str, Any]]:
    """Get a proof by event ID."""
    return ledger.get_proof(event_id)

def verify_proof(proof_id: str) -> bool:
    """Verify a proof by checking its hash."""
    return ledger.verify_proof(proof_id)
