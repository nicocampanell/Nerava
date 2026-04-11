import uuid
from datetime import datetime
from typing import Dict, List, Optional


class ChargingService:
    """Service for managing charging sessions"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
    
    async def start_session(self, user_id: str, hub_id: str) -> Dict:
        """Start a new charging session"""
        session_id = str(uuid.uuid4())
        session = {
            'id': session_id,
            'user_id': user_id,
            'hub_id': hub_id,
            'started_at': datetime.utcnow().isoformat(),
            'status': 'active'
        }
        self.sessions[session_id] = session
        return session
    
    async def stop_session(self, session_id: str, kwh: float) -> Dict:
        """Stop a charging session"""
        if session_id not in self.sessions:
            raise KeyError("session_not_found")
        
        session = self.sessions[session_id]
        session['stopped_at'] = datetime.utcnow().isoformat()
        session['kwh'] = kwh
        session['status'] = 'completed'
        
        return session
    
    async def get_active_sessions(self, user_id: str) -> List[Dict]:
        """Get active sessions for a user"""
        return [
            session for session in self.sessions.values()
            if session['user_id'] == user_id and session['status'] == 'active'
        ]
    
    async def get_session(self, session_id: str) -> Optional[Dict]:
        """Get a specific session by ID"""
        return self.sessions.get(session_id)

# Global service instance
charging_service = ChargingService()
