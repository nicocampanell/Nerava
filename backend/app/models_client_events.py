import json

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.db import Base


class ClientEvent(Base):
    __tablename__ = "client_events"
    
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    event = Column(String(100), nullable=False)
    ts = Column(DateTime, nullable=False)
    page = Column(String(200), nullable=True)
    meta = Column(Text, nullable=True)  # JSON string
    request_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event": self.event,
            "ts": self.ts.isoformat() if self.ts else None,
            "page": self.page,
            "meta": json.loads(self.meta) if self.meta else None,
            "request_id": self.request_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }








