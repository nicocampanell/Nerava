"""
Virtual Key Service for Tesla Virtual Key provisioning and management.

Handles Virtual Key lifecycle: creation, pairing, activation, and revocation.
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.virtual_key import VirtualKey
from app.services.virtual_key_qr import get_qr_service

logger = logging.getLogger(__name__)

# Provisioning token expiration (5 minutes)
PROVISIONING_TOKEN_EXPIRY_MINUTES = 5


class VirtualKeyService:
    """Service for Virtual Key provisioning and management."""

    async def create_provisioning_request(self, db: Session, user_id: int, vin: Optional[str] = None) -> VirtualKey:
        """
        Create a new Virtual Key provisioning request.
        Returns VirtualKey with QR code data for Tesla app scanning.
        
        Args:
            db: Database session
            user_id: User ID
            vin: Optional VIN (Vehicle Identification Number)
            
        Returns:
            VirtualKey with provisioning_token and qr_code_url set
        """
        # Check if user already has an active virtual key
        active_key = await self.get_active_virtual_key(db, user_id)
        if active_key:
            logger.info(f"User {user_id} already has active virtual key {active_key.id}")
            return active_key
        
        # Generate unique provisioning token
        provisioning_token = secrets.token_urlsafe(32)
        
        # Ensure uniqueness
        existing = (
            db.query(VirtualKey)
            .filter(VirtualKey.provisioning_token == provisioning_token)
            .first()
        )
        if existing:
            # Regenerate if collision (extremely rare)
            provisioning_token = secrets.token_urlsafe(32)
        
        # Generate QR code
        callback_url = f"{settings.PUBLIC_BASE_URL}/v1/virtual-key/webhook/tesla"
        qr_service = get_qr_service()
        qr_bytes = qr_service.generate_pairing_qr(provisioning_token, callback_url)
        
        # Upload QR code to S3
        qr_code_url = qr_service.upload_qr_to_s3(qr_bytes, key_prefix=f"virtual-keys/qr/{user_id}")
        
        # Set expiration (5 minutes for pairing)
        expires_at = datetime.utcnow() + timedelta(minutes=PROVISIONING_TOKEN_EXPIRY_MINUTES)
        
        # Create Virtual Key record
        virtual_key = VirtualKey(
            id=str(uuid.uuid4()),
            user_id=user_id,
            vin=vin,
            provisioning_token=provisioning_token,
            qr_code_url=qr_code_url,
            status='pending',
            expires_at=expires_at,
            pairing_attempts=0,
        )
        
        db.add(virtual_key)
        db.commit()
        db.refresh(virtual_key)
        
        logger.info(f"Created Virtual Key provisioning request {virtual_key.id} for user {user_id}")
        
        return virtual_key

    async def check_pairing_status(self, db: Session, provisioning_token: str) -> dict:
        """
        Check if user has completed pairing in Tesla app.
        Called by frontend polling or webhook.
        
        Args:
            db: Database session
            provisioning_token: Provisioning token to check
            
        Returns:
            Dict with status: 'pending', 'paired', 'expired', 'not_found'
        """
        virtual_key = (
            db.query(VirtualKey)
            .filter(VirtualKey.provisioning_token == provisioning_token)
            .first()
        )
        
        if not virtual_key:
            return {"status": "not_found"}
        
        # Check if expired
        if virtual_key.expires_at and virtual_key.expires_at < datetime.utcnow():
            virtual_key.status = 'expired'
            db.commit()
            return {"status": "expired"}
        
        # Return current status
        return {
            "status": virtual_key.status,
            "virtual_key_id": str(virtual_key.id) if virtual_key.status == 'paired' else None,
        }

    async def confirm_pairing(self, db: Session, provisioning_token: str, tesla_vehicle_id: str, vin: Optional[str] = None, vehicle_name: Optional[str] = None) -> VirtualKey:
        """
        Called by Tesla Fleet API webhook when pairing completes.
        Updates Virtual Key status to 'paired'.
        
        Args:
            db: Database session
            provisioning_token: Provisioning token from pairing
            tesla_vehicle_id: Tesla vehicle ID from Fleet API
            vin: Optional VIN
            vehicle_name: Optional vehicle name
            
        Returns:
            Updated VirtualKey with status 'paired'
            
        Raises:
            ValueError: If provisioning token not found or expired
        """
        virtual_key = (
            db.query(VirtualKey)
            .filter(VirtualKey.provisioning_token == provisioning_token)
            .first()
        )
        
        if not virtual_key:
            raise ValueError(f"Virtual Key with provisioning token {provisioning_token[:8]}... not found")
        
        if virtual_key.status != 'pending':
            raise ValueError(f"Virtual Key {virtual_key.id} is not in pending status (current: {virtual_key.status})")
        
        # Update with Tesla vehicle information
        virtual_key.tesla_vehicle_id = tesla_vehicle_id
        virtual_key.vin = vin or virtual_key.vin
        virtual_key.vehicle_name = vehicle_name
        virtual_key.status = 'paired'
        virtual_key.paired_at = datetime.utcnow()
        
        db.commit()
        db.refresh(virtual_key)
        
        logger.info(f"Confirmed pairing for Virtual Key {virtual_key.id} with vehicle {tesla_vehicle_id}")
        
        return virtual_key

    async def activate_virtual_key(self, db: Session, virtual_key_id: str) -> VirtualKey:
        """
        Activate Virtual Key for arrival tracking.
        Called after first successful arrival detection.
        
        Args:
            db: Database session
            virtual_key_id: Virtual Key ID
            
        Returns:
            Activated VirtualKey
            
        Raises:
            ValueError: If Virtual Key not found or not paired
        """
        virtual_key = (
            db.query(VirtualKey)
            .filter(VirtualKey.id == virtual_key_id)
            .first()
        )
        
        if not virtual_key:
            raise ValueError(f"Virtual Key {virtual_key_id} not found")
        
        if virtual_key.status != 'paired':
            raise ValueError(f"Virtual Key {virtual_key_id} must be paired before activation (current: {virtual_key.status})")
        
        # Deactivate any other active keys for this user
        other_active = (
            db.query(VirtualKey)
            .filter(
                and_(
                    VirtualKey.user_id == virtual_key.user_id,
                    VirtualKey.id != virtual_key_id,
                    VirtualKey.status == 'active',
                )
            )
            .all()
        )
        
        for key in other_active:
            key.status = 'revoked'
            key.revoked_at = datetime.utcnow()
        
        # Activate this key
        virtual_key.status = 'active'
        virtual_key.activated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(virtual_key)
        
        logger.info(f"Activated Virtual Key {virtual_key_id} for user {virtual_key.user_id}")
        
        return virtual_key

    async def revoke_virtual_key(self, db: Session, virtual_key_id: str, user_id: int) -> bool:
        """
        Revoke a Virtual Key (user-initiated or admin).
        
        Args:
            db: Database session
            virtual_key_id: Virtual Key ID
            user_id: User ID (for authorization check)
            
        Returns:
            True if revoked successfully
            
        Raises:
            ValueError: If Virtual Key not found or unauthorized
        """
        virtual_key = (
            db.query(VirtualKey)
            .filter(
                and_(
                    VirtualKey.id == virtual_key_id,
                    VirtualKey.user_id == user_id,
                )
            )
            .first()
        )
        
        if not virtual_key:
            raise ValueError(f"Virtual Key {virtual_key_id} not found for user {user_id}")
        
        virtual_key.status = 'revoked'
        virtual_key.revoked_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Revoked Virtual Key {virtual_key_id} for user {user_id}")
        
        return True

    async def get_user_virtual_keys(self, db: Session, user_id: int) -> List[VirtualKey]:
        """
        Get all Virtual Keys for a user.
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            List of VirtualKey records
        """
        return (
            db.query(VirtualKey)
            .filter(VirtualKey.user_id == user_id)
            .order_by(VirtualKey.created_at.desc())
            .all()
        )

    async def get_active_virtual_key(self, db: Session, user_id: int) -> Optional[VirtualKey]:
        """
        Get the currently active Virtual Key for arrival tracking.
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            Active VirtualKey or None
        """
        return (
            db.query(VirtualKey)
            .filter(
                and_(
                    VirtualKey.user_id == user_id,
                    VirtualKey.status == 'active',
                )
            )
            .first()
        )


# Singleton instance
_virtual_key_service: Optional[VirtualKeyService] = None


def get_virtual_key_service() -> VirtualKeyService:
    """Get singleton Virtual Key service instance."""
    global _virtual_key_service
    if _virtual_key_service is None:
        _virtual_key_service = VirtualKeyService()
    return _virtual_key_service
