"""Favorite charger model — mirrors FavoriteMerchant pattern."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from ..db import Base


class FavoriteCharger(Base):
    """User's favorite/bookmarked chargers."""
    __tablename__ = "favorite_chargers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    charger_id = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    charger = relationship("Charger", foreign_keys=[charger_id])

    __table_args__ = (
        UniqueConstraint("user_id", "charger_id", name="uq_favorite_charger"),
        Index('idx_favorite_charger_user', 'user_id'),
        Index('idx_favorite_charger_charger', 'charger_id'),
    )
