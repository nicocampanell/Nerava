"""
Admin Audit Log Service

P1-1: Service for logging all wallet mutations and admin actions.
Ensures logs never include secrets/tokens.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.utils.log import get_logger

logger = get_logger(__name__)

# Secrets/tokens to filter from audit logs
SECRET_FIELDS = {
    'password', 'password_hash', 'token', 'access_token', 'refresh_token',
    'api_key', 'secret', 'secret_key', 'jwt_secret', 'encryption_key',
    'square_access_token', 'stripe_secret', 'stripe_webhook_secret',
    'apple_authentication_token', 'wallet_pass_token'
}


def _filter_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
    """Filter secrets from audit log data"""
    if not isinstance(data, dict):
        return data
    
    filtered = {}
    for key, value in data.items():
        key_lower = key.lower()
        # Check if key contains any secret field name
        if any(secret_field in key_lower for secret_field in SECRET_FIELDS):
            filtered[key] = "[REDACTED]"
        elif isinstance(value, dict):
            filtered[key] = _filter_secrets(value)
        elif isinstance(value, list):
            filtered[key] = [_filter_secrets(item) if isinstance(item, dict) else item for item in value]
        else:
            filtered[key] = value
    
    return filtered


def log_wallet_mutation(
    db: Session,
    actor_id: int,
    action: str,
    user_id: str,
    before_balance: int,
    after_balance: int,
    amount: int,
    metadata: Optional[Dict[str, Any]] = None
) -> AdminAuditLog:
    """
    Log a wallet mutation (credit/debit/redeem).
    
    Args:
        db: Database session
        actor_id: User ID of the actor (who performed the action)
        action: Action type ("wallet_credit", "wallet_debit", "wallet_redeem", etc.)
        user_id: Target user ID (whose wallet was modified)
        before_balance: Balance before mutation
        after_balance: Balance after mutation
        amount: Amount of mutation (positive for credit, negative for debit)
        metadata: Optional metadata (will be filtered for secrets)
    
    Returns:
        Created AdminAuditLog record
    """
    before_json = {"balance_cents": before_balance}
    after_json = {"balance_cents": after_balance}
    metadata_json = _filter_secrets(metadata or {})
    
    audit_log = AdminAuditLog(
        id=str(uuid.uuid4()),
        actor_id=actor_id,
        action=action,
        target_type="wallet",
        target_id=str(user_id),
        before_json=before_json,
        after_json=after_json,
        metadata_json=metadata_json,
        created_at=datetime.utcnow()
    )
    
    db.add(audit_log)
    # Don't commit here - let caller commit
    
    logger.info(
        f"[AUDIT] {action}: actor={actor_id}, target=wallet:{user_id}, "
        f"before={before_balance}, after={after_balance}, amount={amount}"
    )
    
    return audit_log


def log_admin_action(
    db: Session,
    actor_id: int,
    action: str,
    target_type: str,
    target_id: str,
    before_json: Optional[Dict[str, Any]] = None,
    after_json: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> AdminAuditLog:
    """
    Log an admin action (manual adjustments, grants, etc.).
    
    Args:
        db: Database session
        actor_id: User ID of the actor (admin who performed the action)
        action: Action type ("admin_adjust", "admin_grant", etc.)
        target_type: Type of target ("wallet", "merchant_balance", "user", etc.)
        target_id: Target ID
        before_json: State before action (will be filtered for secrets)
        after_json: State after action (will be filtered for secrets)
        metadata: Optional metadata (will be filtered for secrets)
    
    Returns:
        Created AdminAuditLog record
    """
    before_filtered = _filter_secrets(before_json or {})
    after_filtered = _filter_secrets(after_json or {})
    metadata_filtered = _filter_secrets(metadata or {})
    
    audit_log = AdminAuditLog(
        id=str(uuid.uuid4()),
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_json=before_filtered,
        after_json=after_filtered,
        metadata_json=metadata_filtered,
        created_at=datetime.utcnow()
    )
    
    db.add(audit_log)
    # Don't commit here - let caller commit
    
    logger.info(
        f"[AUDIT] {action}: actor={actor_id}, target={target_type}:{target_id}"
    )
    
    return audit_log


def log_merchant_balance_mutation(
    db: Session,
    actor_id: int,
    action: str,
    merchant_id: str,
    before_balance: int,
    after_balance: int,
    amount: int,
    metadata: Optional[Dict[str, Any]] = None
) -> AdminAuditLog:
    """
    Log a merchant balance mutation (credit/debit).
    
    Args:
        db: Database session
        actor_id: User ID of the actor (merchant admin who performed the action)
        action: Action type ("merchant_credit", "merchant_debit", etc.)
        merchant_id: Target merchant ID
        before_balance: Balance before mutation
        after_balance: Balance after mutation
        amount: Amount of mutation
        metadata: Optional metadata (will be filtered for secrets)
    
    Returns:
        Created AdminAuditLog record
    """
    before_json = {"balance_cents": before_balance}
    after_json = {"balance_cents": after_balance}
    metadata_json = _filter_secrets(metadata or {})
    
    audit_log = AdminAuditLog(
        id=str(uuid.uuid4()),
        actor_id=actor_id,
        action=action,
        target_type="merchant_balance",
        target_id=merchant_id,
        before_json=before_json,
        after_json=after_json,
        metadata_json=metadata_json,
        created_at=datetime.utcnow()
    )
    
    db.add(audit_log)
    # Don't commit here - let caller commit
    
    logger.info(
        f"[AUDIT] {action}: actor={actor_id}, target=merchant_balance:{merchant_id}, "
        f"before={before_balance}, after={after_balance}, amount={amount}"
    )
    
    return audit_log







