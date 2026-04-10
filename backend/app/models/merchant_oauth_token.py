"""
MerchantOAuthToken model — stores encrypted OAuth tokens for merchant Google Business Profile access.
PosOAuthState model — DB-backed OAuth state for POS integrations (Toast, etc.) CSRF protection.
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Session

from ..core.uuid_type import UUIDType
from ..db import Base


class MerchantOAuthToken(Base):
    __tablename__ = "merchant_oauth_tokens"

    id = Column(UUIDType(), primary_key=True)
    merchant_account_id = Column(String, ForeignKey("merchant_accounts.id"), nullable=False, index=True)
    provider = Column(String, nullable=False, default="google_gbp")

    # Encrypted via core.token_encryption
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String, nullable=True)

    # Google Business Profile specific
    gbp_account_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    __table_args__ = (
        Index("uq_merchant_oauth_account_provider", "merchant_account_id", "provider", unique=True),
    )


class PosOAuthState(Base):
    """DB-backed OAuth state for POS integrations (Toast, etc.) — survives deploys and works across instances."""
    __tablename__ = "pos_oauth_states"

    state = Column(String(64), primary_key=True)
    data_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_pos_oauth_states_expires", "expires_at"),
    )

    @classmethod
    def store(cls, db: Session, state: str, data: dict, ttl_minutes: int = 10):
        row = cls(
            state=state,
            data_json=json.dumps(data, default=str),
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
        db.merge(row)
        db.commit()

    @classmethod
    def pop(cls, db: Session, state: str) -> Optional[dict]:
        row = db.query(cls).filter(cls.state == state).first()
        if not row:
            return None
        if row.expires_at < datetime.utcnow():
            db.delete(row)
            db.commit()
            return None
        data = json.loads(row.data_json)
        db.delete(row)
        db.commit()
        return data

    @classmethod
    def cleanup_expired(cls, db: Session):
        db.query(cls).filter(cls.expires_at < datetime.utcnow()).delete()
        db.commit()
