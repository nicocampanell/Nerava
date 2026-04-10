"""
Secrets provider abstraction.

Provides a unified interface for reading secrets from environment variables
or AWS Secrets Manager (when enabled).

Default implementation reads from environment variables.
AWS Secrets Manager support can be enabled via SECRETS_PROVIDER env var.
"""
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class SecretProvider(ABC):
    """Abstract base class for secret providers"""
    
    @abstractmethod
    def get_secret(self, name: str) -> Optional[str]:
        """
        Get a secret by name.
        
        Args:
            name: Secret name (e.g., "DATABASE_URL", "JWT_SECRET")
        
        Returns:
            Secret value or None if not found
        """
        pass


class EnvSecretProvider(SecretProvider):
    """Secret provider that reads from environment variables (default)"""
    
    def get_secret(self, name: str) -> Optional[str]:
        """Get secret from environment variable"""
        return os.getenv(name)


class AWSSecretsManagerProvider(SecretProvider):
    """Secret provider that reads from AWS Secrets Manager"""
    
    def __init__(self, region: Optional[str] = None):
        """
        Initialize AWS Secrets Manager provider.
        
        Args:
            region: AWS region (defaults to AWS_DEFAULT_REGION env var)
        """
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self._client = None
    
    def _get_client(self):
        """Lazy-load boto3 client"""
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client('secretsmanager', region_name=self.region)
            except ImportError:
                raise ImportError(
                    "boto3 is required for AWS Secrets Manager. "
                    "Install with: pip install boto3"
                )
        return self._client
    
    def get_secret(self, name: str) -> Optional[str]:
        """
        Get secret from AWS Secrets Manager.
        
        Args:
            name: Secret name (ARN or name)
        
        Returns:
            Secret value or None if not found
        """
        try:
            client = self._get_client()
            
            # Try to get secret (handle both ARN and name)
            try:
                response = client.get_secret_value(SecretId=name)
                return response.get('SecretString')
            except client.exceptions.ResourceNotFoundException:
                logger.warning(f"Secret {name} not found in AWS Secrets Manager")
                return None
            except Exception as e:
                logger.error(f"Error retrieving secret {name} from AWS Secrets Manager: {e}")
                return None
        except ImportError:
            logger.error("boto3 not available, cannot use AWS Secrets Manager")
            return None


# Global secret provider instance
_secret_provider: Optional[SecretProvider] = None


def get_secret_provider() -> SecretProvider:
    """
    Get the configured secret provider.
    
    Returns:
        SecretProvider instance (EnvSecretProvider by default)
    """
    global _secret_provider
    
    if _secret_provider is None:
        provider_type = os.getenv("SECRETS_PROVIDER", "env").lower()
        
        if provider_type == "aws":
            region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
            _secret_provider = AWSSecretsManagerProvider(region=region)
            logger.info(f"Using AWS Secrets Manager provider (region: {region})")
        else:
            _secret_provider = EnvSecretProvider()
            logger.debug("Using environment variable secret provider")
    
    return _secret_provider


def get_secret(name: str) -> Optional[str]:
    """
    Get a secret by name using the configured provider.
    
    Args:
        name: Secret name (e.g., "DATABASE_URL", "JWT_SECRET")
    
    Returns:
        Secret value or None if not found
    
    Usage:
        db_url = get_secret("DATABASE_URL")
        jwt_secret = get_secret("JWT_SECRET")
    """
    provider = get_secret_provider()
    return provider.get_secret(name)







