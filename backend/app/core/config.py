import logging
import os
from datetime import timedelta
from typing import Dict

from app.core.secrets import get_secret
from pydantic import BaseModel

logger = logging.getLogger(__name__)


_env = os.getenv("ENV", "dev").lower()
_dev_fallback = "dev-secret-change-me" if _env in ("dev", "development", "test") else ""


class Settings(BaseModel):
    # JWT Secret (supports both JWT_SECRET and NERAVA_SECRET_KEY env vars for backward compatibility)
    # Fail closed: only use dev fallback in dev/test environments; empty string triggers validate_config() error otherwise
    JWT_SECRET: str = os.getenv("JWT_SECRET", os.getenv("NERAVA_SECRET_KEY", _dev_fallback))
    # SECRET_KEY is an alias for JWT_SECRET (used by JWT encoding code)
    SECRET_KEY: str = os.getenv("JWT_SECRET", os.getenv("NERAVA_SECRET_KEY", _dev_fallback))
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
    )  # 7 days; mobile app needs long-lived tokens
    ALGORITHM: str = "HS256"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nerava.db")

    # Stripe configuration
    STRIPE_SECRET_KEY: str = os.getenv(
        "STRIPE_SECRET_KEY", os.getenv("STRIPE_SECRET", "")
    )  # Support both names
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    @property
    def stripe_webhook_secret(self) -> str:
        """Lowercase alias used by stripe_api.py webhook endpoint."""
        return self.STRIPE_WEBHOOK_SECRET

    # Stripe feature flags
    ENABLE_STRIPE: bool = os.getenv("ENABLE_STRIPE", "true").lower() == "true"
    ENABLE_STRIPE_PAYOUTS: bool = os.getenv("ENABLE_STRIPE_PAYOUTS", "false").lower() == "true"
    STRIPE_PAYOUT_WEBHOOK_SECRET: str = os.getenv("STRIPE_PAYOUT_WEBHOOK_SECRET", "")
    MINIMUM_WITHDRAWAL_CENTS: int = int(
        os.getenv("MINIMUM_WITHDRAWAL_CENTS", "100")
    )  # $1 minimum (lowered for testing)
    WEEKLY_WITHDRAWAL_LIMIT_CENTS: int = int(
        os.getenv("WEEKLY_WITHDRAWAL_LIMIT_CENTS", "100000")
    )  # $1000/week

    # Integration feature flags (referenced by app/dependencies/feature_flags.py)
    ENABLE_SQUARE: bool = os.getenv("ENABLE_SQUARE", "false").lower() == "true"
    ENABLE_SMARTCAR: bool = os.getenv("ENABLE_SMARTCAR", "false").lower() == "true"
    ENABLE_GOOGLE_OAUTH: bool = os.getenv("ENABLE_GOOGLE_OAUTH", "true").lower() == "true"
    ENABLE_APPLE_WALLET_SIGNING: bool = (
        os.getenv("ENABLE_APPLE_WALLET_SIGNING", "false").lower() == "true"
    )

    # Fidel CLO (Card Linked Offers) configuration
    ENABLE_CLO: bool = os.getenv("ENABLE_CLO", "false").lower() == "true"
    FIDEL_SECRET_KEY: str = os.getenv("FIDEL_SECRET_KEY", "")
    FIDEL_PROGRAM_ID: str = os.getenv("FIDEL_PROGRAM_ID", "")
    FIDEL_WEBHOOK_SECRET: str = os.getenv("FIDEL_WEBHOOK_SECRET", "")

    # Frontend URL for redirects
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:8001")

    # Public Base URL (for redirects, webhooks, QR codes)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001")

    # Token Encryption Key (Fernet key for encrypting sensitive tokens)
    TOKEN_ENCRYPTION_KEY: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")

    # Allowed Hosts for TrustedHostMiddleware (comma-separated)
    ALLOWED_HOSTS: str = os.getenv("ALLOWED_HOSTS", "")

    # Smartcar configuration
    # For local dev, use sandbox mode. In production, set SMARTCAR_MODE=live
    SMARTCAR_CLIENT_ID: str = os.getenv("SMARTCAR_CLIENT_ID", "")
    SMARTCAR_CLIENT_SECRET: str = os.getenv("SMARTCAR_CLIENT_SECRET", "")
    SMARTCAR_REDIRECT_URI: str = os.getenv("SMARTCAR_REDIRECT_URI", "")
    SMARTCAR_MODE: str = os.getenv("SMARTCAR_MODE", "sandbox")  # sandbox (dev) or live (production)
    SMARTCAR_BASE_URL: str = os.getenv("SMARTCAR_BASE_URL", "https://api.smartcar.com")
    SMARTCAR_AUTH_URL: str = os.getenv("SMARTCAR_AUTH_URL", "https://auth.smartcar.com")
    SMARTCAR_CONNECT_URL: str = os.getenv("SMARTCAR_CONNECT_URL", "https://connect.smartcar.com")
    SMARTCAR_STATE_SECRET: str = os.getenv(
        "SMARTCAR_STATE_SECRET", ""
    )  # Distinct secret for Smartcar state JWT
    SMARTCAR_ENABLED: bool = (
        os.getenv("SMARTCAR_ENABLED", "false").lower() == "true"
    )  # Feature flag to disable Smartcar

    # Tesla Fleet API configuration
    # Try AWS Secrets Manager first, fall back to env vars
    TESLA_CLIENT_ID: str = get_secret("nerava/tesla-client-id") or os.getenv("TESLA_CLIENT_ID", "")
    TESLA_CLIENT_SECRET: str = get_secret("nerava/tesla-client-secret") or os.getenv(
        "TESLA_CLIENT_SECRET", ""
    )
    TESLA_PUBLIC_KEY_URL: str = os.getenv(
        "TESLA_PUBLIC_KEY_URL",
        "https://api.nerava.network/.well-known/appspecific/com.tesla.3p.public-key.pem",
    )
    TESLA_WEBHOOK_SECRET: str = get_secret("nerava/tesla-webhook-secret") or os.getenv(
        "TESLA_WEBHOOK_SECRET", ""
    )
    TESLA_FLEET_TELEMETRY_ENDPOINT: str = os.getenv(
        "TESLA_FLEET_TELEMETRY_ENDPOINT", "wss://fleet-telemetry.nerava.com"
    )
    FEATURE_VIRTUAL_KEY_ENABLED: bool = (
        os.getenv("FEATURE_VIRTUAL_KEY_ENABLED", "false").lower() == "true"
    )
    TESLA_MOCK_MODE: bool = os.getenv("TESLA_MOCK_MODE", "false").lower() == "true"

    # Tesla Fleet Telemetry configuration
    TESLA_EC_PUBLIC_KEY_PEM: str = os.getenv("TESLA_EC_PUBLIC_KEY_PEM", "")
    TESLA_FLEET_TELEMETRY_CA_CERT: str = os.getenv(
        "TESLA_FLEET_TELEMETRY_CA_CERT", ""
    )  # Full cert PEM for ca field
    TESLA_TELEMETRY_HMAC_SECRET: str = get_secret(
        "nerava/tesla-telemetry-hmac-secret"
    ) or os.getenv("TESLA_TELEMETRY_HMAC_SECRET", "")
    TELEMETRY_WEBHOOK_ENABLED: bool = (
        os.getenv("TELEMETRY_WEBHOOK_ENABLED", "true").lower() == "true"
    )
    VEHICLE_COMMAND_PROXY_URL: str = os.getenv(
        "VEHICLE_COMMAND_PROXY_URL", ""
    )  # e.g. https://nlb-dns:4443

    # APNs Push Notification configuration
    APNS_KEY_PATH: str = os.getenv("APNS_KEY_PATH", "")
    APNS_KEY_CONTENT: str = os.getenv(
        "APNS_KEY_CONTENT", ""
    )  # .p8 key content as env var (alternative to file path)
    APNS_KEY_ID: str = os.getenv("APNS_KEY_ID", "")
    APNS_TEAM_ID: str = os.getenv("APNS_TEAM_ID", "")
    APNS_BUNDLE_ID: str = os.getenv("APNS_BUNDLE_ID", "com.nerava.driver")
    APNS_USE_SANDBOX: bool = os.getenv("APNS_USE_SANDBOX", "false").lower() == "true"

    # Firebase Cloud Messaging (FCM) for Android push notifications
    FIREBASE_CREDENTIALS_JSON: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

    # App URLs
    API_BASE_URL: str = os.getenv("API_BASE_URL", "https://api.nerava.network")
    DRIVER_APP_URL: str = os.getenv("DRIVER_APP_URL", "https://app.nerava.network")

    # Debug/Testing mode
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    TESTING: bool = os.getenv("TESTING", "false").lower() == "true"

    @property
    def is_prod(self) -> bool:
        return (self.ENV or "").lower() in ("prod", "production")

    @property
    def smartcar_enabled(self) -> bool:
        """
        Check if Smartcar integration is fully configured and enabled.
        Returns True only if SMARTCAR_ENABLED=true AND client_id, client_secret, and redirect_uri are all set.
        """
        if not self.SMARTCAR_ENABLED:
            return False
        return bool(
            self.SMARTCAR_CLIENT_ID and self.SMARTCAR_CLIENT_SECRET and self.SMARTCAR_REDIRECT_URI
        )

    @property
    def jwt_secret(self) -> str:
        """Alias for JWT_SECRET to maintain compatibility with validation code."""
        return self.JWT_SECRET

    # Google Places API (New) configuration
    GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "")

    # Merchant auth mock mode
    MERCHANT_AUTH_MOCK: bool = os.getenv("MERCHANT_AUTH_MOCK", "false").lower() == "true"

    # Intent capture configuration
    LOCATION_ACCURACY_THRESHOLD_M: float = float(
        os.getenv("LOCATION_ACCURACY_THRESHOLD_M", "100")
    )  # Default 100m
    INTENT_SESSION_ONBOARDING_THRESHOLD: int = int(
        os.getenv("INTENT_SESSION_ONBOARDING_THRESHOLD", "3")
    )  # Require onboarding after N sessions

    # Confidence tier thresholds (in meters)
    CONFIDENCE_TIER_A_THRESHOLD_M: float = float(
        os.getenv("CONFIDENCE_TIER_A_THRESHOLD_M", "120")
    )  # Tier A: <120m
    CONFIDENCE_TIER_B_THRESHOLD_M: float = float(
        os.getenv("CONFIDENCE_TIER_B_THRESHOLD_M", "400")
    )  # Tier B: <400m

    # Exclusive session configuration
    CHARGER_RADIUS_M: float = float(
        os.getenv("CHARGER_RADIUS_M", "150")
    )  # Charger radius for activation (meters)
    EXCLUSIVE_DURATION_MIN: int = int(
        os.getenv("EXCLUSIVE_DURATION_MIN", "60")
    )  # Exclusive session duration (minutes)

    # Native iOS App Configuration
    NATIVE_SESSION_ENGINE_ENABLED: bool = (
        os.getenv("NATIVE_SESSION_ENGINE_ENABLED", "true").lower() == "true"
    )
    NATIVE_BRIDGE_ENABLED: bool = os.getenv("NATIVE_BRIDGE_ENABLED", "true").lower() == "true"

    # Native iOS session engine configuration
    NATIVE_CHARGER_INTENT_RADIUS_M: float = float(
        os.getenv("NATIVE_CHARGER_INTENT_RADIUS_M", "400")
    )  # Charger intent zone radius (meters)
    NATIVE_CHARGER_ANCHOR_RADIUS_M: float = float(
        os.getenv("NATIVE_CHARGER_ANCHOR_RADIUS_M", "30")
    )  # Charger anchor radius (meters)
    NATIVE_CHARGER_DWELL_SECONDS: int = int(
        os.getenv("NATIVE_CHARGER_DWELL_SECONDS", "120")
    )  # Required dwell time at charger (seconds)
    NATIVE_MERCHANT_UNLOCK_RADIUS_M: float = float(
        os.getenv("NATIVE_MERCHANT_UNLOCK_RADIUS_M", "40")
    )  # Merchant unlock radius (meters)
    NATIVE_GRACE_PERIOD_SECONDS: int = int(
        os.getenv("NATIVE_GRACE_PERIOD_SECONDS", "900")
    )  # Grace period after leaving charger (seconds)
    NATIVE_HARD_TIMEOUT_SECONDS: int = int(
        os.getenv("NATIVE_HARD_TIMEOUT_SECONDS", "3600")
    )  # Hard timeout for entire session (seconds)
    NATIVE_LOCATION_ACCURACY_THRESHOLD_M: float = float(
        os.getenv("NATIVE_LOCATION_ACCURACY_THRESHOLD_M", "50")
    )  # Minimum location accuracy required (meters)
    NATIVE_SPEED_THRESHOLD_FOR_DWELL_MPS: float = float(
        os.getenv("NATIVE_SPEED_THRESHOLD_FOR_DWELL_MPS", "1.5")
    )  # Max speed for dwell detection (m/s)

    # Charger search configuration
    CHARGER_SEARCH_LIMIT: int = int(
        os.getenv("CHARGER_SEARCH_LIMIT", "20")
    )  # Max chargers returned by intent capture

    # Google Places search radius
    GOOGLE_PLACES_SEARCH_RADIUS_M: int = int(
        os.getenv("GOOGLE_PLACES_SEARCH_RADIUS_M", "800")
    )  # 800m radius for merchant search

    # Merchant cache TTL (in seconds)
    MERCHANT_CACHE_TTL_SECONDS: int = int(
        os.getenv("MERCHANT_CACHE_TTL_SECONDS", "3600")
    )  # 1 hour default

    # Privacy policy version for consent tracking
    PRIVACY_POLICY_VERSION: str = os.getenv("PRIVACY_POLICY_VERSION", "1.0")

    # Redis configuration (for idempotency cache, rate limiting, etc.)
    REDIS_URL: str = os.getenv("REDIS_URL", "")  # e.g., "redis://localhost:6379/0"
    REDIS_ENABLED: bool = os.getenv("REDIS_ENABLED", "false").lower() == "true"

    # Vehicle onboarding photo retention (in days)
    VEHICLE_ONBOARDING_RETENTION_DAYS: int = int(
        os.getenv("VEHICLE_ONBOARDING_RETENTION_DAYS", "90")
    )  # 90 days default

    # Perk unlock caps
    MAX_PERK_UNLOCKS_PER_SESSION: int = int(
        os.getenv("MAX_PERK_UNLOCKS_PER_SESSION", "1")
    )  # Max unlocks per intent session
    PERK_COOLDOWN_MINUTES_PER_MERCHANT: int = int(
        os.getenv("PERK_COOLDOWN_MINUTES_PER_MERCHANT", "60")
    )  # Cooldown in minutes per merchant

    # Platform fee configuration (in basis points, 2000 = 20%)
    PLATFORM_FEE_BPS: int = int(os.getenv("PLATFORM_FEE_BPS", "2000"))

    # Auth Provider Configuration
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_ID: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", "")
    )
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
    GOOGLE_GBP_REQUIRED: bool = os.getenv("GOOGLE_GBP_REQUIRED", "true").lower() == "true"
    APPLE_CLIENT_ID: str = os.getenv("APPLE_CLIENT_ID", "")
    APPLE_TEAM_ID: str = os.getenv("APPLE_TEAM_ID", "")
    APPLE_KEY_ID: str = os.getenv("APPLE_KEY_ID", "")
    APPLE_PRIVATE_KEY: str = os.getenv("APPLE_PRIVATE_KEY", "")

    # Email OTP / SES Configuration
    EMAIL_SENDER: str = os.getenv(
        "EMAIL_SENDER", "console"
    )  # "console" (dev) or "ses" (production)

    # Phone OTP Configuration (Twilio)
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_VERIFY_SERVICE_SID: str = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")
    OTP_FROM_NUMBER: str = os.getenv("OTP_FROM_NUMBER", "")
    OTP_PROVIDER: str = os.getenv(
        "OTP_PROVIDER", "twilio_verify"
    )  # twilio_verify, twilio_sms, stub
    OTP_DEV_ALLOWLIST: str = os.getenv("OTP_DEV_ALLOWLIST", "")  # Comma-separated phone numbers
    TWILIO_TIMEOUT_SECONDS: int = int(
        os.getenv("TWILIO_TIMEOUT_SECONDS", "30")
    )  # Timeout for Twilio API calls

    # Refresh Token Configuration
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

    # Demo Mode Settings
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"
    DEMO_ADMIN_KEY: str = os.getenv("DEMO_ADMIN_KEY", "")

    # Wallet and Nova Settings
    DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "America/Chicago")
    NOVA_TO_USD_CONVERSION_RATE_CENTS: int = int(
        os.getenv("NOVA_TO_USD_CONVERSION_RATE_CENTS", "10")
    )

    # Environment and Debug Settings
    ENV: str = os.getenv("ENV", "dev")  # dev, staging, prod
    region: str = os.getenv("REGION", "us-east-1")
    emergency_readonly_mode: bool = os.getenv("EMERGENCY_READONLY_MODE", "false").lower() == "true"
    DEBUG_RETURN_MAGIC_LINK: bool = os.getenv("DEBUG_RETURN_MAGIC_LINK", "false").lower() == "true"

    # Apple Wallet Configuration
    APPLE_WALLET_SIGNING_ENABLED: bool = (
        os.getenv("APPLE_WALLET_SIGNING_ENABLED", "false").lower() == "true"
    )
    APPLE_WALLET_PASS_TYPE_ID: str = os.getenv(
        "APPLE_WALLET_PASS_TYPE_ID", "pass.com.nerava.wallet"
    )
    APPLE_WALLET_TEAM_ID: str = os.getenv("APPLE_WALLET_TEAM_ID", "")
    APPLE_WALLET_CERT_P12_PATH: str = os.getenv("APPLE_WALLET_CERT_P12_PATH", "")
    APPLE_WALLET_CERT_P12_PASSWORD: str = os.getenv("APPLE_WALLET_CERT_P12_PASSWORD", "")
    APPLE_WALLET_APNS_KEY_ID: str = os.getenv("APPLE_WALLET_APNS_KEY_ID", "")
    APPLE_WALLET_APNS_TEAM_ID: str = os.getenv("APPLE_WALLET_APNS_TEAM_ID", "")
    APPLE_WALLET_APNS_AUTH_KEY_PATH: str = os.getenv("APPLE_WALLET_APNS_AUTH_KEY_PATH", "")

    # Merchant subscription Stripe Price IDs
    STRIPE_PRICE_PRO_MONTHLY: str = os.getenv("STRIPE_PRICE_PRO_MONTHLY", "")
    STRIPE_PRICE_ADS_FLAT_MONTHLY: str = os.getenv("STRIPE_PRICE_ADS_FLAT_MONTHLY", "")
    STRIPE_MERCHANT_WEBHOOK_SECRET: str = os.getenv("STRIPE_MERCHANT_WEBHOOK_SECRET", "")
    MERCHANT_PORTAL_URL: str = os.getenv("MERCHANT_PORTAL_URL", "https://merchant.nerava.network")

    # Preview signing key for merchant funnel (HMAC-SHA256)
    PREVIEW_SIGNING_KEY: str = os.getenv("PREVIEW_SIGNING_KEY", "")

    # HubSpot Configuration
    HUBSPOT_ENABLED: bool = os.getenv("HUBSPOT_ENABLED", "false").lower() == "true"
    HUBSPOT_SEND_LIVE: bool = os.getenv("HUBSPOT_SEND_LIVE", "false").lower() == "true"
    HUBSPOT_PRIVATE_APP_TOKEN: str = os.getenv("HUBSPOT_PRIVATE_APP_TOKEN", "")
    HUBSPOT_PORTAL_ID: str = os.getenv("HUBSPOT_PORTAL_ID", "")

    # Partner Incentive API
    PARTNER_DEFAULT_RATE_LIMIT_RPM: int = int(os.getenv("PARTNER_DEFAULT_RATE_LIMIT_RPM", "60"))

    # Toast POS Integration
    TOAST_CLIENT_ID: str = os.getenv("TOAST_CLIENT_ID", "")
    TOAST_CLIENT_SECRET: str = os.getenv("TOAST_CLIENT_SECRET", "")
    TOAST_MOCK_MODE: bool = os.getenv("TOAST_MOCK_MODE", "true").lower() == "true"

    # Feature Flags (default OFF for safety)
    feature_merchant_intel: bool = False
    feature_behavior_cloud: bool = False
    feature_autonomous_reward_routing: bool = False
    feature_city_marketplace: bool = False
    feature_multimodal: bool = False
    feature_merchant_credits: bool = False
    feature_charge_verify_api: bool = False
    feature_energy_wallet_ext: bool = False
    feature_merchant_utility_coops: bool = False
    feature_whitelabel_sdk: bool = False
    feature_energy_rep: bool = False
    feature_carbon_micro_offsets: bool = False
    feature_fleet_workplace: bool = False
    feature_smart_home_iot: bool = False
    feature_contextual_commerce: bool = False
    feature_energy_events: bool = False
    feature_uap_partnerships: bool = False
    feature_ai_reward_opt: bool = False
    feature_esg_finance_gateway: bool = False
    feature_ai_growth_automation: bool = False
    feature_dual_radius_verification: bool = False
    feature_virtual_card: bool = False  # Virtual card generation feature


settings = Settings()
ACCESS_TOKEN_EXPIRE = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

# Feature flag cache
_flag_cache: Dict[str, bool] = {}


def flag_enabled(key: str) -> bool:
    """Check if a feature flag is enabled with in-memory cache"""
    if key not in _flag_cache:
        # In production, this would query the FeatureFlag table
        # For now, use environment variables as fallback
        env_key = f"FEATURE_{key.upper()}"
        _flag_cache[key] = os.getenv(env_key, "false").lower() == "true"
    return _flag_cache[key]


def clear_flag_cache():
    """Clear the flag cache (useful for testing)"""
    global _flag_cache
    _flag_cache.clear()


def is_demo() -> bool:
    """Check if demo mode is enabled."""
    return settings.DEMO_MODE


def validate_config():
    """Validate configuration at startup. Raises ValueError if invalid."""

    # Validate Apple Wallet configuration if signing is enabled
    if settings.APPLE_WALLET_SIGNING_ENABLED:
        missing = []
        if not settings.APPLE_WALLET_PASS_TYPE_ID:
            missing.append("APPLE_WALLET_PASS_TYPE_ID")
        if not settings.APPLE_WALLET_TEAM_ID:
            missing.append("APPLE_WALLET_TEAM_ID")

        # Check for P12 or PEM cert/key
        has_p12 = bool(
            settings.APPLE_WALLET_CERT_P12_PATH
            and os.path.exists(settings.APPLE_WALLET_CERT_P12_PATH)
        )
        cert_path = os.getenv("APPLE_WALLET_CERT_PATH", "")
        key_path = os.getenv("APPLE_WALLET_KEY_PATH", "")
        has_pem = bool(
            cert_path and os.path.exists(cert_path) and key_path and os.path.exists(key_path)
        )

        if not (has_p12 or has_pem):
            missing.append(
                "APPLE_WALLET_CERT_P12_PATH (or APPLE_WALLET_CERT_PATH + APPLE_WALLET_KEY_PATH)"
            )

        if missing:
            error_msg = f"Apple Wallet signing enabled but missing required configuration: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.info("Apple Wallet configuration validated")

    # Validate HubSpot configuration if send_live is enabled
    if settings.HUBSPOT_SEND_LIVE:
        if not settings.HUBSPOT_ENABLED:
            error_msg = "HUBSPOT_SEND_LIVE is true but HUBSPOT_ENABLED is false"
            logger.error(error_msg)
            raise ValueError(error_msg)
        missing = []
        if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
            missing.append("HUBSPOT_PRIVATE_APP_TOKEN")
        if not settings.HUBSPOT_PORTAL_ID:
            missing.append("HUBSPOT_PORTAL_ID")
        if missing:
            error_msg = f"HubSpot send_live enabled but missing required configuration: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.info("HubSpot configuration validated")

    # Validate OTP configuration in production
    if settings.is_prod:
        if settings.OTP_PROVIDER == "stub":
            error_msg = "OTP_PROVIDER=stub is not allowed in production"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if settings.OTP_PROVIDER in ["twilio_verify", "twilio_sms"]:
            missing = []
            if not settings.TWILIO_ACCOUNT_SID:
                missing.append("TWILIO_ACCOUNT_SID")
            if not settings.TWILIO_AUTH_TOKEN:
                missing.append("TWILIO_AUTH_TOKEN")

            if settings.OTP_PROVIDER == "twilio_verify":
                if not settings.TWILIO_VERIFY_SERVICE_SID:
                    missing.append("TWILIO_VERIFY_SERVICE_SID")
            elif settings.OTP_PROVIDER == "twilio_sms":
                if not settings.OTP_FROM_NUMBER:
                    missing.append("OTP_FROM_NUMBER")

            if missing:
                error_msg = f"OTP enabled in production but missing required configuration: {', '.join(missing)}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.info(f"OTP configuration validated (provider: {settings.OTP_PROVIDER})")

    # Validate Google OAuth configuration if merchant SSO is enabled
    # Note: We check if Google client ID is set as a proxy for merchant SSO being enabled
    if settings.is_prod and settings.GOOGLE_OAUTH_CLIENT_ID:
        # Require client secret for code exchange flow
        missing = []
        if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
            missing.append("GOOGLE_OAUTH_CLIENT_SECRET")
        if missing:
            error_msg = f"Google OAuth enabled in production but missing required configuration: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.info("Google OAuth configuration validated")

    # Production safety gates
    if settings.is_prod:
        # Validate MERCHANT_AUTH_MOCK is disabled
        if settings.MERCHANT_AUTH_MOCK:
            error_msg = "MERCHANT_AUTH_MOCK=true is not allowed in production"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate DEMO_MODE is disabled
        if settings.DEMO_MODE:
            error_msg = "DEMO_MODE=true is not allowed in production"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate JWT_SECRET is not default/empty
        if (
            not settings.JWT_SECRET
            or settings.JWT_SECRET == "dev-secret-change-me"
            or settings.JWT_SECRET == "dev-secret"
        ):
            error_msg = (
                "CRITICAL SECURITY ERROR: JWT_SECRET must be set and not use default value in production. "
                "Set JWT_SECRET or NERAVA_SECRET_KEY environment variable to a secure random value."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate TOKEN_ENCRYPTION_KEY is set
        if not settings.TOKEN_ENCRYPTION_KEY:
            error_msg = (
                "CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY environment variable is required in production. "
                "This key is used to encrypt vehicle and Square tokens. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate TOKEN_ENCRYPTION_KEY format (Fernet keys are 44-char base64)
        if len(settings.TOKEN_ENCRYPTION_KEY) != 44:
            error_msg = (
                "CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY must be a valid Fernet key (44 characters base64). "
                f"Current length: {len(settings.TOKEN_ENCRYPTION_KEY)}. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate Fernet key format
        try:
            from cryptography.fernet import Fernet

            Fernet(settings.TOKEN_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as e:
            error_msg = (
                f"CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY is not a valid Fernet key: {str(e)}. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
            logger.error(error_msg)
            raise ValueError(error_msg) from e

        # Validate PUBLIC_BASE_URL is not localhost
        if (
            "localhost" in settings.PUBLIC_BASE_URL.lower()
            or "127.0.0.1" in settings.PUBLIC_BASE_URL
        ):
            error_msg = (
                f"CRITICAL: PUBLIC_BASE_URL cannot point to localhost in production. "
                f"Current value: {settings.PUBLIC_BASE_URL}. "
                "Set PUBLIC_BASE_URL to your production API domain (e.g., https://api.nerava.network)"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate FRONTEND_URL is not localhost
        if "localhost" in settings.FRONTEND_URL.lower() or "127.0.0.1" in settings.FRONTEND_URL:
            error_msg = (
                f"CRITICAL: FRONTEND_URL cannot point to localhost in production. "
                f"Current value: {settings.FRONTEND_URL}. "
                "Set FRONTEND_URL to your production frontend domain (e.g., https://app.nerava.network)"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info("Production safety gates validated")

    # Log final config summary (with secrets redacted)
    logger.info("Configuration validation complete")
    logger.info(f"Environment: {settings.ENV}")
    logger.info(f"OTP Provider: {settings.OTP_PROVIDER}")
    if settings.TWILIO_ACCOUNT_SID:
        logger.info(f"Twilio Account SID: {settings.TWILIO_ACCOUNT_SID[:8]}...")
    if settings.GOOGLE_OAUTH_CLIENT_ID:
        logger.info(f"Google OAuth Client ID: {settings.GOOGLE_OAUTH_CLIENT_ID[:20]}...")
