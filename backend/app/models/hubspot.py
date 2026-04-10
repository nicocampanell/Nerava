"""
HubSpot Outbox Model

P3: Stores HubSpot events for dry-run and replay.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON  # for non-sqlite engines
except Exception:
    JSON = SQLITE_JSON  # fallback for sqlite


class HubSpotOutbox(Base):
    """HubSpot outbox for storing events before sending (dry run mode)"""
    __tablename__ = "hubspot_outbox"
    
    id = Column(UUIDType(), primary_key=True)
    event_type = Column(String, nullable=False, index=True)  # "user_signup", "redemption", "wallet_pass_install"
    payload_json = Column(JSON, nullable=False)
    sent_at = Column(DateTime, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index('ix_hubspot_outbox_event_created', 'event_type', 'created_at'),
        Index('ix_hubspot_outbox_sent_created', 'sent_at', 'created_at'),
    )

