"""
Token encryption utilities using Fernet symmetric encryption.

Uses TOKEN_ENCRYPTION_KEY from environment (validated at startup).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet() -> Optional[Fernet]:
    """Get or create Fernet instance from TOKEN_ENCRYPTION_KEY."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        return None

    try:
        _fernet = Fernet(key.encode("utf-8"))
        return _fernet
    except Exception as e:
        logger.error(f"Invalid TOKEN_ENCRYPTION_KEY: {e}")
        return None


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns ciphertext as base64 string."""
    f = _get_fernet()
    if not f:
        logger.warning("TOKEN_ENCRYPTION_KEY not set, storing token unencrypted")
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token string. Falls back to returning raw value if not encrypted."""
    f = _get_fernet()
    if not f:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Token was stored before encryption was enabled — return as-is
        return ciphertext
