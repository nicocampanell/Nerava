"""
Refresh token service for token rotation and management
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import (
    generate_refresh_token,
    hash_refresh_token,
    verify_refresh_token,
)
from ..models import RefreshToken, User


class RefreshTokenService:
    """Service for managing refresh tokens with rotation"""
    
    @staticmethod
    def create_refresh_token(db: Session, user: User) -> Tuple[str, RefreshToken]:
        """
        Create a new refresh token for a user.
        
        Returns:
            Tuple of (plain_token_string, RefreshToken_model)
        """
        plain_token = generate_refresh_token()
        token_hash = hash_refresh_token(plain_token)
        
        expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        
        refresh_token = RefreshToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked=False
        )
        
        db.add(refresh_token)
        db.flush()
        
        return plain_token, refresh_token
    
    @staticmethod
    def validate_refresh_token(db: Session, plain_token: str) -> Optional[RefreshToken]:
        """
        Validate a refresh token and return the token record if valid.
        
        Returns:
            RefreshToken if valid, None if invalid
        """
        # Find token by hash (we need to check all tokens for this user)
        # Since we can't query by hash directly, we'll need to check tokens for all users
        # This is inefficient but necessary for security. In production with Redis, we'd cache this.
        
        # Get all non-expired, non-revoked tokens
        tokens = db.query(RefreshToken).filter(
            and_(
                RefreshToken.revoked == False,
                RefreshToken.expires_at > datetime.utcnow()
            )
        ).all()
        
        for token in tokens:
            if verify_refresh_token(plain_token, token.token_hash):
                return token
        
        return None
    
    @staticmethod
    def rotate_refresh_token(db: Session, old_token: RefreshToken) -> Tuple[str, RefreshToken]:
        """
        Rotate a refresh token: revoke the old one and create a new one.
        
        Returns:
            Tuple of (new_plain_token_string, new_RefreshToken_model)
        """
        # Revoke old token
        old_token.revoked = True
        old_token.updated_at = datetime.utcnow()
        
        # Create new token
        user = db.query(User).filter(User.id == old_token.user_id).first()
        if not user:
            raise ValueError(f"User {old_token.user_id} not found")
        
        new_plain_token, new_refresh_token = RefreshTokenService.create_refresh_token(db, user)
        
        # Link old token to new one
        old_token.replaced_by = new_refresh_token.id
        
        db.flush()
        
        return new_plain_token, new_refresh_token
    
    @staticmethod
    def revoke_refresh_token(db: Session, token: RefreshToken) -> None:
        """Revoke a refresh token"""
        token.revoked = True
        token.updated_at = datetime.utcnow()
        db.flush()
    
    @staticmethod
    def revoke_all_user_tokens(db: Session, user_id: int) -> None:
        """Revoke all refresh tokens for a user (e.g., on logout)"""
        tokens = db.query(RefreshToken).filter(
            and_(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked == False
            )
        ).all()
        
        for token in tokens:
            token.revoked = True
            token.updated_at = datetime.utcnow()
        
        db.flush()
    
    @staticmethod
    def cleanup_expired_tokens(db: Session, days_old: int = 7) -> int:
        """
        Clean up expired tokens older than specified days.
        Returns count of deleted tokens.
        """
        cutoff = datetime.utcnow() - timedelta(days=days_old)
        deleted = db.query(RefreshToken).filter(
            RefreshToken.expires_at < cutoff
        ).delete()
        db.commit()
        return deleted








