"""
POS Adapter abstraction for order lookup.

Provides a unified interface for looking up orders across POS systems.
Toast and Square adapters are stubs until partner API access is granted.
ManualPOSAdapter is the default for v1.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class POSOrder:
    """Normalized order from any POS system."""
    order_number: str
    status: str  # 'placed', 'ready', 'completed', 'unknown'
    total_cents: int
    customer_name: Optional[str] = None
    items_summary: Optional[str] = None


class POSAdapter(ABC):
    """Abstract interface for POS integrations."""

    @abstractmethod
    async def lookup_order(self, order_number: str) -> Optional[POSOrder]:
        """Find order by number. Returns None if not found."""

    @abstractmethod
    async def get_order_status(self, order_number: str) -> Optional[str]:
        """Get current status of an order."""

    @abstractmethod
    async def get_order_total(self, order_number: str) -> Optional[int]:
        """Get order total in cents."""


class ManualPOSAdapter(POSAdapter):
    """
    No POS integration — uses driver-reported data.
    This is the default adapter and always succeeds.
    """

    async def lookup_order(self, order_number: str) -> Optional[POSOrder]:
        return POSOrder(
            order_number=order_number,
            status="unknown",
            total_cents=0,
        )

    async def get_order_status(self, order_number: str) -> Optional[str]:
        return "unknown"

    async def get_order_total(self, order_number: str) -> Optional[int]:
        return None


class ToastPOSAdapter(POSAdapter):
    """
    Read-only Toast integration (stub).
    Requires Toast partner API access to implement fully.
    """

    def __init__(self, restaurant_guid: str, access_token: str):
        self.restaurant_guid = restaurant_guid
        self.access_token = access_token
        self.base_url = "https://ws-api.toasttab.com"

    async def lookup_order(self, order_number: str) -> Optional[POSOrder]:
        # TODO: Implement when Toast partner API access is granted.
        # GET /orders/v2/orders?businessDate={today}
        # Scan for check where displayNumber matches order_number
        logger.info(f"Toast lookup_order stub called for order #{order_number} "
                     f"(restaurant: {self.restaurant_guid})")
        return None  # Fall back to manual

    async def get_order_status(self, order_number: str) -> Optional[str]:
        order = await self.lookup_order(order_number)
        return order.status if order else None

    async def get_order_total(self, order_number: str) -> Optional[int]:
        order = await self.lookup_order(order_number)
        return order.total_cents if order else None


class SquarePOSAdapter(POSAdapter):
    """
    Square POS integration (stub).
    Can reuse existing square_service.py patterns.
    """

    def __init__(self, location_id: str, access_token: str):
        self.location_id = location_id
        self.access_token = access_token

    async def lookup_order(self, order_number: str) -> Optional[POSOrder]:
        logger.info(f"Square lookup_order stub called for order #{order_number}")
        return None

    async def get_order_status(self, order_number: str) -> Optional[str]:
        return None

    async def get_order_total(self, order_number: str) -> Optional[int]:
        return None


def get_pos_adapter(pos_integration: str, credentials=None) -> POSAdapter:
    """
    Factory: returns the right POS adapter for a merchant.

    Args:
        pos_integration: 'none', 'toast', or 'square'
        credentials: MerchantPOSCredentials row (or None)

    Returns:
        POSAdapter instance
    """
    if pos_integration == "toast" and credentials:
        # Decrypt tokens would happen here in production
        return ToastPOSAdapter(
            restaurant_guid=credentials.restaurant_guid or "",
            access_token="",  # Would decrypt credentials.access_token_encrypted
        )
    if pos_integration == "square" and credentials:
        return SquarePOSAdapter(
            location_id="",
            access_token="",
        )
    return ManualPOSAdapter()
