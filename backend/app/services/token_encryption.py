"""
Token encryption utility for OAuth tokens (Smartcar, Square, etc.)
Uses Fernet symmetric encryption for at-rest encryption.

Backward compatibility: Supports decryption of tokens encrypted with legacy hash/pad-derived keys.
New encryption always uses validated Fernet keys (fail-fast in non-local).
"""
import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.env import is_local_env

logger = logging.getLogger(__name__)


class TokenDecryptionError(Exception):
    """Raised when token decryption fails"""
    pass


def _get_encryption_key() -> bytes:
    """
    Get encryption key from environment variable for NEW encryption.
    In production, this must be a valid Fernet key. In local dev, allows deterministic key with warning.
    
    Returns:
        Valid Fernet key bytes
        
    Raises:
        ValueError: If key is missing/invalid in non-local environment
    """
    from app.core.env import is_local_env
    
    key_str = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    
    if not key_str:
        if is_local_env():
            # In local dev, use a deterministic key (NOT for production)
            logger.warning("Using deterministic dev encryption key (NOT SECURE FOR PRODUCTION)")
            key_str = "dev-token-encryption-key-32-bytes!!"  # 32 bytes
        else:
            # Production: require key
            from app.core.env import get_env_name
            env = get_env_name()
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY environment variable is required in non-local environment. "
                f"ENV={env}. Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
    
    # Validate key by constructing Fernet instance (fail-fast)
    try:
        # Try to use as-is (might already be base64-encoded Fernet key)
        key_bytes = key_str.encode() if isinstance(key_str, str) else key_str
        # Validate by constructing Fernet (will raise if invalid)
        Fernet(key_bytes)
        return key_bytes
    except Exception:
        # If not valid Fernet key, try base64 decoding
        try:
            key_bytes = base64.urlsafe_b64decode(key_str)
            Fernet(key_bytes)  # Validate
            return key_bytes
        except Exception as e:
            if not is_local_env():
                raise ValueError(
                    "TOKEN_ENCRYPTION_KEY is not a valid Fernet key. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                ) from e
            # In local, allow and warn
            logger.warning(f"Invalid Fernet key format in local env, attempting to use as-is: {e}")
            return key_str.encode()[:44]  # Truncate to max Fernet key length


def _get_legacy_key() -> bytes:
    """
    Get legacy encryption key using old hash/pad derivation method.
    Used ONLY for backward-compatible decryption of existing ciphertext.
    
    Returns:
        Legacy-derived key bytes
    """
    key_str = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    
    if not key_str:
        # Fallback to dev key for legacy compatibility
        key_str = "dev-token-encryption-key-32-bytes!!"
    
    # Legacy hash/pad logic (for backward compatibility)
    import hashlib as h
    if len(key_str) < 32 or len(key_str) > 44:
        key_bytes = h.sha256(key_str.encode()).digest()
        key_str = base64.urlsafe_b64encode(key_bytes).decode()
    
    try:
        return key_str.encode()
    except Exception:
        return base64.urlsafe_b64encode(key_str.encode()[:32])


_fernet_instance: Optional[Fernet] = None
_legacy_fernet_instance: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """
    Get Fernet instance for encryption/decryption.
    Fail-fast validation in non-local environments.
    """
    global _fernet_instance
    if _fernet_instance is None:
        key = _get_encryption_key()
        # Validate key by constructing Fernet (fail-fast)
        try:
            _fernet_instance = Fernet(key)
        except Exception as e:
            if not is_local_env():
                raise ValueError(
                    "Failed to construct Fernet instance with provided key. "
                    "Generate valid key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                ) from e
            # In local, allow and warn
            logger.warning(f"Invalid Fernet key in local env, attempting to use anyway: {e}")
            _fernet_instance = Fernet(key)
    return _fernet_instance


def _get_legacy_fernet() -> Fernet:
    """Get legacy Fernet instance for backward-compatible decryption"""
    global _legacy_fernet_instance
    if _legacy_fernet_instance is None:
        key = _get_legacy_key()
        _legacy_fernet_instance = Fernet(key)
    return _legacy_fernet_instance


def encrypt_token(plaintext_token: str) -> str:
    """
    Encrypt a token for storage at rest.
    
    Uses ONLY the current validated Fernet key (never legacy).
    Fail-fast in non-local if key is missing/invalid.
    
    Args:
        plaintext_token: Plaintext token to encrypt
        
    Returns:
        Encrypted token (base64-encoded)
        
    Raises:
        ValueError: If encryption key is not configured (in production)
        TokenDecryptionError: If encryption fails
    """
    if not plaintext_token:
        return plaintext_token
    
    try:
        fernet = _get_fernet()  # Uses validated key only
        encrypted = fernet.encrypt(plaintext_token.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Token encryption failed: {e}", exc_info=True)
        raise TokenDecryptionError(f"Failed to encrypt token: {str(e)}") from e


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt a token for use.
    
    Backward compatibility: Tries current key first, then legacy key on InvalidToken.
    This allows existing ciphertext encrypted with legacy hash/pad-derived keys to be decrypted.
    
    Args:
        encrypted_token: Encrypted token (base64-encoded) or plaintext
        
    Returns:
        Plaintext token
        
    Raises:
        TokenDecryptionError: If decryption fails with both keys
    """
    if not encrypted_token:
        return encrypted_token
    
    # Check if token is already plaintext (for migration compatibility)
    # If it doesn't start with Fernet's base64 prefix, assume it's plaintext
    if not encrypted_token.startswith("gAAAAA"):
        # Might be plaintext from before encryption was added
        logger.warning("Token appears to be plaintext (not encrypted). Consider migrating.")
        return encrypted_token
    
    # Try current validated key first
    try:
        fernet = _get_fernet()
        decrypted = fernet.decrypt(encrypted_token.encode())
        return decrypted.decode()
    except InvalidToken:
        # Current key failed, try legacy key for backward compatibility
        try:
            legacy_fernet = _get_legacy_fernet()
            decrypted = legacy_fernet.decrypt(encrypted_token.encode())
            logger.debug("Decrypted token using legacy key (backward compatibility)")
            return decrypted.decode()
        except InvalidToken:
            # Both keys failed
            logger.error("Token decryption failed with both current and legacy keys")
            raise TokenDecryptionError("Failed to decrypt token: invalid key or corrupted data")
    except Exception as e:
        logger.error(f"Token decryption failed: {e}", exc_info=True)
        raise TokenDecryptionError(f"Failed to decrypt token: {str(e)}") from e
