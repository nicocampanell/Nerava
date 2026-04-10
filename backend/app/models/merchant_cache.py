"""
Models for Merchant Caching
- MerchantCache: Caches Google Places merchant data with geo cells
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON

from ..db import Base

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON


class MerchantCache(Base):
    """Caches Google Places merchant data by place_id and geo cell"""
    __tablename__ = "merchant_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Google Places identifier
    place_id = Column(String, nullable=False, index=True)
    
    # Geo cell for caching (simple lat/lng rounding, e.g., 0.001 degree ≈ 111m)
    geo_cell_lat = Column(Float, nullable=False, index=True)  # Rounded lat
    geo_cell_lng = Column(Float, nullable=False, index=True)  # Rounded lng
    
    # Cached merchant data (JSON)
    merchant_data = Column(JSON, nullable=False)  # Full merchant data from Google Places
    
    # Photo URL (if available)
    photo_url = Column(String, nullable=True)
    
    # Cache metadata
    cached_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)  # TTL for cache invalidation
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_merchant_cache_place_geo', 'place_id', 'geo_cell_lat', 'geo_cell_lng'),
        Index('idx_merchant_cache_geo', 'geo_cell_lat', 'geo_cell_lng'),
        Index('idx_merchant_cache_expires', 'expires_at'),
    )



