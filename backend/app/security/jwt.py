"""
JWT token handling for authentication
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

class JWTManager:
    """JWT token manager"""
    
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
    
    def create_token(self, user_id: str, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT token for a user"""
        try:
            if expires_delta:
                expire = datetime.utcnow() + expires_delta
            else:
                expire = datetime.utcnow() + timedelta(hours=24)
            
            payload = {
                "user_id": user_id,
                "exp": expire,
                "iat": datetime.utcnow(),
                "iss": "nerava-api"
            }
            
            token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
            return token
            
        except Exception as e:
            logger.error(f"Error creating JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Token creation failed"
            )
    
    def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify and decode a JWT token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired"
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        except Exception as e:
            logger.error(f"Error verifying JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token verification failed"
            )
    
    def refresh_token(self, token: str, expires_delta: Optional[timedelta] = None) -> str:
        """Refresh a JWT token"""
        try:
            payload = self.verify_token(token)
            user_id = payload.get("user_id")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token payload"
                )
            
            return self.create_token(user_id, expires_delta)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error refreshing JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token refresh failed"
            )

# Global JWT manager (in production, use proper secret management)
jwt_manager = JWTManager(secret_key=settings.jwt_secret)

def get_current_user(token: str) -> str:
    """Get current user from JWT token"""
    payload = jwt_manager.verify_token(token)
    return payload["user_id"]

def create_user_token(user_id: str) -> str:
    """Create a token for a user"""
    return jwt_manager.create_token(user_id)
