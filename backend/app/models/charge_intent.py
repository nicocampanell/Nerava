"""
ChargeIntent model for ORM access to charge_intents table
"""
from sqlalchemy import Column, DateTime, Float, Integer, String, func

from ..db import Base


class ChargeIntent(Base):
    __tablename__ = "charge_intents"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    station_id = Column(String)
    station_name = Column(String)
    merchant_name = Column(String)
    perk_title = Column(String)
    address = Column(String)
    eta_minutes = Column(Integer)
    starts_at = Column(DateTime)
    status = Column(String, default='saved')
    merchant_lat = Column(Float)
    merchant_lng = Column(Float)
    station_lat = Column(Float)
    station_lng = Column(Float)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    merchant = Column(String)
    perk_id = Column(String)
    window_text = Column(String)
    distance_text = Column(String)
