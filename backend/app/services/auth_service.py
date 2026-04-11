"""
Auth Service - User registration, authentication, and role management
for Domain Charge Party MVP
"""
import logging
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.models_domain import DomainMerchant, DriverWallet

logger = logging.getLogger(__name__)


class AuthService:
    """Service for authentication and user management"""
    
    @staticmethod
    def register_user(
        db: Session,
        email: str,
        password: str,
        display_name: Optional[str] = None,
        roles: Optional[List[str]] = None,
        auth_provider: str = "local"
    ) -> User:
        """Register a new user with roles"""
        # Check if user already exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            raise ValueError(f"User with email {email} already exists")
        
        # Default role is driver
        if roles is None:
            roles = ["driver"]
        
        role_flags = ",".join(roles) if isinstance(roles, list) else roles
        
        # Create user
        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name or email.split("@")[0],
            role_flags=role_flags,
            auth_provider=auth_provider,
            is_active=True
        )
        db.add(user)
        db.flush()
        
        # Create driver wallet if user is a driver
        if "driver" in roles:
            wallet = DriverWallet(user_id=user.id, nova_balance=0, energy_reputation_score=0)
            db.add(wallet)
        
        db.commit()
        db.refresh(user)
        
        logger.info(f"Registered user: {user.id} ({email}) with roles: {role_flags}")
        
        # Emit driver_signed_up event if user is a driver (non-blocking)
        if "driver" in roles:
            try:
                from datetime import datetime

                from app.events.domain import DriverSignedUpEvent
                from app.events.outbox import store_outbox_event
                event = DriverSignedUpEvent(
                    user_id=str(user.id),
                    email=email,
                    auth_provider=auth_provider,
                    created_at=datetime.utcnow()
                )
                store_outbox_event(db, event)
            except Exception as e:
                logger.warning(f"Failed to emit driver_signed_up event: {e}")
        
        return user
    
    @staticmethod
    def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
        """Authenticate user by email and password"""
        user = db.query(User).filter(User.email == email).first()
        if not user:
            logger.warning(f"Authentication failed: user not found for email {email}")
            return None
        
        if not verify_password(password, user.password_hash):
            logger.warning(f"Authentication failed: invalid password for email {email}")
            return None
        
        if not user.is_active:
            logger.warning(f"Authentication failed: user {user.id} is inactive")
            return None
        
        logger.info(f"User authenticated: {user.id} ({email})")
        return user
    
    @staticmethod
    def create_session_token(user: User, expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT token for user session"""
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # Use public_id (UUID string) as subject, not integer id
        return create_access_token(user.public_id, expires_delta, auth_provider=user.auth_provider)
    
    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
        """Get user by ID (integer primary key)"""
        return db.query(User).filter(User.id == user_id).first()
    
    @staticmethod
    def get_user_by_public_id(db: Session, public_id: str) -> Optional[User]:
        """Get user by public_id (UUID string)"""
        return db.query(User).filter(User.public_id == public_id).first()
    
    @staticmethod
    def get_user_roles(user: User) -> List[str]:
        """Get user roles as list"""
        if not user.role_flags:
            return []
        return [r.strip() for r in user.role_flags.split(",") if r.strip()]
    
    @staticmethod
    def has_role(user: User, role: str) -> bool:
        """Check if user has a specific role"""
        roles = AuthService.get_user_roles(user)
        return role in roles
    
    @staticmethod
    def get_user_merchant(db: Session, user_id: int, merchant_id: Optional[str] = None) -> Optional[DomainMerchant]:
        """Get merchant owned by user. If merchant_id given, verify that specific merchant is owned by user."""
        query = db.query(DomainMerchant).filter(
            and_(
                DomainMerchant.owner_user_id == user_id,
                DomainMerchant.status == "active"
            )
        )
        if merchant_id:
            query = query.filter(DomainMerchant.id == merchant_id)
        return query.first()

