"""
Tests for configuration validation, including CORS origins validation
"""
import os
from unittest.mock import patch

import pytest
from app.core.env import is_local_env
from app.core.startup_validation import validate_cors_origins


# Clear cache before each test to ensure environment changes are picked up
@pytest.fixture(autouse=True)
def clear_env_cache():
    """Clear environment detection cache before each test."""
    is_local_env.cache_clear()
    yield
    is_local_env.cache_clear()


def test_cors_validation_fails_in_prod_with_wildcard():
    """Test that CORS validation fails in prod when ALLOWED_ORIGINS contains *"""
    with patch.dict(os.environ, {"ENV": "prod", "ALLOWED_ORIGINS": "*"}):
        with pytest.raises(ValueError, match="CORS wildcard"):
            validate_cors_origins()


def test_cors_validation_passes_in_local_with_wildcard():
    """Test that CORS validation passes in local when ALLOWED_ORIGINS contains *"""
    with patch.dict(os.environ, {"ENV": "local", "ALLOWED_ORIGINS": "*"}):
        # Should not raise
        validate_cors_origins()


def test_cors_validation_passes_in_prod_with_explicit_origins():
    """Test that CORS validation passes in prod when ALLOWED_ORIGINS has explicit origins"""
    with patch.dict(os.environ, {"ENV": "prod", "ALLOWED_ORIGINS": "https://app.nerava.com,https://www.nerava.com"}):
        # Should not raise
        validate_cors_origins()


def test_cors_validation_fails_in_prod_with_wildcard_in_list():
    """Test that CORS validation fails in prod when ALLOWED_ORIGINS contains * in list"""
    with patch.dict(os.environ, {"ENV": "prod", "ALLOWED_ORIGINS": "https://app.nerava.com,*"}):
        with pytest.raises(ValueError, match="CORS wildcard"):
            validate_cors_origins()

