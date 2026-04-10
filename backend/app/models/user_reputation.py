"""
UserReputation model for ORM access to user_reputations table
"""
from sqlalchemy import Column, DateTime, Integer, String, func

from ..db import Base


class UserReputation(Base):
    __tablename__ = "user_reputation"

    user_id = Column(String, primary_key=True, nullable=False, index=True)
    score = Column(Integer, default=0)
    tier = Column(String, default='Bronze')
    streak_days = Column(Integer, default=0)
    followers_count = Column(Integer, default=0)
    following_count = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
