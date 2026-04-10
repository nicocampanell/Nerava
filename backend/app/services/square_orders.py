"""
Square Orders Service

Handles Square API integration for order lookup, creation, and payment.
Supports both sandbox and production environments.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

import httpx
from sqlalchemy.orm import Session

from app.models.domain import DomainMerchant
from app.services.token_encryption import TokenDecryptionError, decrypt_token

logger = logging.getLogger(__name__)


class SquareError(Exception):
    """Base exception for Square API errors"""
    pass


class SquareNotConnectedError(SquareError):
    """Merchant is not connected to Square"""
    pass


class SquareOrderTotalUnavailableError(SquareError):
    """Order total cannot be retrieved"""
    pass


def _get_square_base_url() -> str:
    """
    Get Square API base URL based on SQUARE_ENV.
    
    Returns:
        str: Base URL for Square API
    """
    square_env = os.getenv("SQUARE_ENV", "sandbox").lower()
    if square_env == "production" or square_env == "prod":
        return "https://connect.squareup.com"
    else:
        return "https://connect.squareupsandbox.com"


def get_square_token_for_merchant(merchant: DomainMerchant) -> str:
    """
    Decrypt and return Square access token for merchant.
    
    Args:
        merchant: DomainMerchant instance
        
    Returns:
        str: Decrypted Square access token
        
    Raises:
        SquareNotConnectedError: If token or location_id is missing
        TokenDecryptionError: If decryption fails
    """
    if not merchant.square_access_token:
        raise SquareNotConnectedError("Merchant Square access token not found")
    
    if not merchant.square_location_id:
        raise SquareNotConnectedError("Merchant Square location ID not found")
    
    try:
        token = decrypt_token(merchant.square_access_token)
        return token
    except TokenDecryptionError as e:
        logger.error(f"Failed to decrypt Square token for merchant {merchant.id}: {e}")
        raise SquareNotConnectedError(f"Failed to decrypt Square token: {e}")


def search_recent_orders(
    db: Session,
    merchant: DomainMerchant,
    minutes: int = 10,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Search for recent paid orders from Square.
    
    Args:
        db: Database session (unused but kept for consistency)
        merchant: DomainMerchant instance
        minutes: Number of minutes to look back
        limit: Maximum number of orders to return
        
    Returns:
        List of normalized order dicts with:
        - order_id: Square order ID
        - created_at: ISO timestamp
        - total_cents: Order total in cents
        - currency: Currency code (e.g., "USD")
        - display: Human-readable string (e.g., "$8.50 • 10:41 AM")
        
    Raises:
        SquareNotConnectedError: If merchant not connected
        SquareError: If API call fails
    """
    try:
        access_token = get_square_token_for_merchant(merchant)
        base_url = _get_square_base_url()
        
        # Calculate start_at timestamp (minutes ago)
        start_at = datetime.utcnow() - timedelta(minutes=minutes)
        start_at_iso = start_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Build request
        url = f"{base_url}/v2/orders/search"
        headers = {
            "Square-Version": "2024-01-18",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "location_ids": [merchant.square_location_id],
            "query": {
                "filter": {
                    "date_time_filter": {
                        "created_at": {
                            "start_at": start_at_iso
                        }
                    },
                    "state_filter": {
                        "states": ["COMPLETED"]
                    }
                },
                "sort": {
                    "sort_field": "CREATED_AT",
                    "sort_order": "DESC"
                }
            },
            "limit": limit
        }
        
        # Make API call
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        # Normalize orders
        orders = []
        for order_obj in data.get("orders", []):
            order_id = order_obj.get("id")
            created_at_str = order_obj.get("created_at")
            total_money = order_obj.get("total_money", {})
            total_cents = total_money.get("amount", 0)
            currency = total_money.get("currency", "USD")
            
            # Format display string
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                display_time = created_dt.strftime("%I:%M %p").lstrip("0")
                display_total = f"${total_cents / 100:.2f}"
                display = f"{display_total} • {display_time}"
            except Exception:
                display = f"${total_cents / 100:.2f}"
            
            orders.append({
                "order_id": order_id,
                "created_at": created_at_str,
                "total_cents": total_cents,
                "currency": currency,
                "display": display
            })
        
        logger.info(f"Found {len(orders)} recent orders for merchant {merchant.id}")
        return orders
        
    except SquareNotConnectedError:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Square API error: {e.response.status_code} - {e.response.text}")
        raise SquareError(f"Square API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error searching Square orders: {e}", exc_info=True)
        raise SquareError(f"Failed to search orders: {str(e)}")


def get_order_total_cents(
    db: Session,
    merchant: DomainMerchant,
    order_id: str
) -> int:
    """
    Get order total from Square.
    
    Args:
        db: Database session (unused but kept for consistency)
        merchant: DomainMerchant instance
        order_id: Square order ID
        
    Returns:
        int: Order total in cents
        
    Raises:
        SquareNotConnectedError: If merchant not connected
        SquareOrderTotalUnavailableError: If order total cannot be retrieved
        SquareError: If API call fails
    """
    try:
        access_token = get_square_token_for_merchant(merchant)
        base_url = _get_square_base_url()
        
        url = f"{base_url}/v2/orders/{order_id}"
        headers = {
            "Square-Version": "2024-01-18",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Make API call
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        order = data.get("order", {})
        
        # Validate location matches
        location_id = order.get("location_id")
        if location_id != merchant.square_location_id:
            raise SquareOrderTotalUnavailableError(
                f"Order location {location_id} does not match merchant location {merchant.square_location_id}"
            )
        
        # Get total
        total_money = order.get("total_money", {})
        total_cents = total_money.get("amount")
        
        if total_cents is None:
            raise SquareOrderTotalUnavailableError("Order total not available")
        
        return total_cents
        
    except SquareNotConnectedError:
        raise
    except SquareOrderTotalUnavailableError:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Square API error: {e.response.status_code} - {e.response.text}")
        raise SquareOrderTotalUnavailableError(f"Square API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error getting Square order total: {e}", exc_info=True)
        raise SquareOrderTotalUnavailableError(f"Failed to get order total: {str(e)}")


def create_order(
    db: Session,
    merchant: DomainMerchant,
    amount_cents: int,
    name: str = "Coffee"
) -> Dict[str, Any]:
    """
    Create a Square order (for Swagger demo).
    
    Args:
        db: Database session (unused but kept for consistency)
        merchant: DomainMerchant instance
        amount_cents: Order amount in cents
        name: Item name (default: "Coffee")
        
    Returns:
        Dict with:
        - order_id: Square order ID
        - total_cents: Order total in cents
        - created_at: ISO timestamp
        
    Raises:
        SquareNotConnectedError: If merchant not connected
        SquareError: If API call fails
    """
    try:
        access_token = get_square_token_for_merchant(merchant)
        base_url = _get_square_base_url()
        
        url = f"{base_url}/v2/orders"
        headers = {
            "Square-Version": "2024-01-18",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4())
        }
        
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "order": {
                "location_id": merchant.square_location_id,
                "line_items": [
                    {
                        "name": name,
                        "quantity": "1",
                        "base_price_money": {
                            "amount": amount_cents,
                            "currency": "USD"
                        }
                    }
                ]
            }
        }
        
        # Make API call
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        order = data.get("order", {})
        order_id = order.get("id")
        created_at = order.get("created_at")
        total_money = order.get("total_money", {})
        total_cents = total_money.get("amount", amount_cents)
        
        logger.info(f"Created Square order {order_id} for merchant {merchant.id}, amount: ${total_cents / 100:.2f}")
        
        return {
            "order_id": order_id,
            "total_cents": total_cents,
            "created_at": created_at
        }
        
    except SquareNotConnectedError:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Square API error: {e.response.status_code} - {e.response.text}")
        raise SquareError(f"Square API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error creating Square order: {e}", exc_info=True)
        raise SquareError(f"Failed to create order: {str(e)}")


def create_payment_for_order(
    db: Session,
    merchant: DomainMerchant,
    order_id: str,
    amount_cents: int
) -> Dict[str, Any]:
    """
    Create a payment for a Square order (for Swagger demo).
    
    Args:
        db: Database session (unused but kept for consistency)
        merchant: DomainMerchant instance
        order_id: Square order ID
        amount_cents: Payment amount in cents
        
    Returns:
        Dict with:
        - payment_id: Square payment ID
        - status: Payment status (e.g., "COMPLETED")
        
    Raises:
        SquareNotConnectedError: If merchant not connected
        SquareError: If API call fails
    """
    try:
        access_token = get_square_token_for_merchant(merchant)
        base_url = _get_square_base_url()
        
        url = f"{base_url}/v2/payments"
        headers = {
            "Square-Version": "2024-01-18",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4())
        }
        
        # Use sandbox test card nonce
        source_id = "cnon:card-nonce-ok"
        square_env = os.getenv("SQUARE_ENV", "sandbox").lower()
        if square_env != "sandbox":
            # In production, you'd need a real payment source
            raise SquareError("Payment creation only supported in sandbox mode")
        
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "source_id": source_id,
            "amount_money": {
                "amount": amount_cents,
                "currency": "USD"
            },
            "order_id": order_id
        }
        
        # Make API call
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        payment = data.get("payment", {})
        payment_id = payment.get("id")
        payment_status = payment.get("status", "UNKNOWN")
        
        logger.info(f"Created Square payment {payment_id} for order {order_id}, status: {payment_status}")
        
        return {
            "payment_id": payment_id,
            "status": payment_status
        }
        
    except SquareNotConnectedError:
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"Square API error: {e.response.status_code} - {e.response.text}")
        raise SquareError(f"Square API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error creating Square payment: {e}", exc_info=True)
        raise SquareError(f"Failed to create payment: {str(e)}")

