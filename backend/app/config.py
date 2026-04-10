import os
from typing import Optional

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore", case_sensitive=False)

    # Environment
    ENV: str = os.getenv("ENV", "dev")  # dev, staging, prod
    ALLOWED_HOSTS: str = os.getenv("ALLOWED_HOSTS", "")

    # Database
    database_url: str = "sqlite:///./nerava.db"
    read_database_url: Optional[str] = None

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Logging
    log_level: str = "INFO"

    # Request handling
    request_timeout_s: int = 5
    rate_limit_per_minute: int = 120

    # EnergyHub
    energyhub_allow_demo_at: bool = True
    cache_ttl_windows: int = 60

    # CORS
    cors_allow_origins: str = os.getenv("ALLOWED_ORIGINS", "*")

    # Public base URL (for OAuth redirects, wallet pass URLs, QR URLs)
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8001")

    # Frontend URL for redirects (OAuth callbacks, etc.)
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:8001/app")

    # Region (read from REGION env var, default to "local" for dev)
    region: str = os.getenv("REGION", "local")
    primary_region: str = os.getenv("PRIMARY_REGION", "local")

    # Events
    events_driver: str = "inproc"

    # Feature flags
    enable_sync_credit: bool = False

    # Emergency kill-switches and business feature flags (P1 stability fix)
    nova_earn_enabled: bool = os.getenv("NOVA_EARN_ENABLED", "true").lower() == "true"
    nova_redeem_enabled: bool = os.getenv("NOVA_REDEEM_ENABLED", "true").lower() == "true"
    payouts_enabled: bool = os.getenv("PAYOUTS_ENABLED", "true").lower() == "true"
    emergency_readonly_mode: bool = os.getenv("EMERGENCY_READONLY_MODE", "false").lower() == "true"
    block_all_money_movement: bool = (
        os.getenv("BLOCK_ALL_MONEY_MOVEMENT", "false").lower() == "true"
    )

    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret")
    jwt_alg: str = os.getenv("JWT_ALG", "HS256")

    # Verify Rewards
    verify_reward_cents: int = int(os.getenv("VERIFY_REWARD_CENTS", "200"))
    verify_pool_pct: int = int(os.getenv("VERIFY_POOL_PCT", "10"))

    # Stripe Connect
    stripe_secret: str = os.getenv("STRIPE_SECRET", "")
    stripe_connect_client_id: str = os.getenv("STRIPE_CONNECT_CLIENT_ID", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Square OAuth (for merchant onboarding)
    square_env: str = os.getenv("SQUARE_ENV", "sandbox")  # sandbox or production

    # Sandbox credentials
    square_application_id_sandbox: str = os.getenv("SQUARE_APPLICATION_ID_SANDBOX", "REPLACE_ME")
    square_application_secret_sandbox: str = os.getenv(
        "SQUARE_APPLICATION_SECRET_SANDBOX", "REPLACE_ME"
    )
    # Redirect URL - will be computed via get_square_sandbox_config() to use PUBLIC_BASE_URL
    square_redirect_url_sandbox: str = os.getenv("SQUARE_REDIRECT_URL_SANDBOX", "")

    # Production credentials
    square_application_id_production: str = os.getenv("SQUARE_APPLICATION_ID_PRODUCTION", "")
    square_application_secret_production: str = os.getenv(
        "SQUARE_APPLICATION_SECRET_PRODUCTION", ""
    )
    square_redirect_url_production: str = os.getenv("SQUARE_REDIRECT_URL_PRODUCTION", "")

    # Legacy support (for backward compatibility)
    square_application_id: str = os.getenv("SQUARE_APPLICATION_ID", "")
    square_application_secret: str = os.getenv("SQUARE_APPLICATION_SECRET", "")
    square_redirect_url: str = os.getenv("SQUARE_REDIRECT_URL", "")

    square_base_url: str = (
        "https://connect.squareup.com"
        if os.getenv("SQUARE_ENV", "sandbox") == "production"
        else "https://connect.squareupsandbox.com"
    )

    # Payout Policy
    payout_min_cents: int = int(os.getenv("PAYOUT_MIN_CENTS", "100"))
    payout_max_cents: int = int(os.getenv("PAYOUT_MAX_CENTS", "10000"))
    payout_daily_cap_cents: int = int(os.getenv("PAYOUT_DAILY_CAP_CENTS", "20000"))

    # Purchase Rewards
    purchase_reward_flat_cents: int = int(os.getenv("PURCHASE_REWARD_FLAT_CENTS", "150"))
    purchase_match_radius_m: int = int(os.getenv("PURCHASE_MATCH_RADIUS_M", "120"))
    purchase_session_ttl_min: int = int(os.getenv("PURCHASE_SESSION_TTL_MIN", "30"))
    webhook_shared_secret: str = os.getenv("WEBHOOK_SHARED_SECRET", "")
    square_webhook_signature_key: str = os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY", "")

    # Anti-Fraud
    max_verify_per_hour: int = int(os.getenv("MAX_VERIFY_PER_HOUR", "6"))
    max_sessions_per_hour: int = int(os.getenv("MAX_SESSIONS_PER_HOUR", "6"))
    max_different_ips_per_day: int = int(os.getenv("MAX_DIFFERENT_IPS_PER_DAY", "5"))
    min_allowed_accuracy_m: float = float(os.getenv("MIN_ALLOWED_ACCURACY_M", "100"))
    max_geo_jump_km: float = float(os.getenv("MAX_GEO_JUMP_KM", "50"))
    block_score_threshold: int = int(os.getenv("BLOCK_SCORE_THRESHOLD", "100"))

    # Merchant Dashboard
    dashboard_enable: bool = os.getenv("DASHBOARD_ENABLE", "true").lower() == "true"

    # Events & Verification
    push_enabled: bool = os.getenv("PUSH_ENABLED", "true").lower() == "true"
    city_fallback: str = os.getenv("CITY_FALLBACK", "Austin")
    max_push_per_day_per_user: int = int(os.getenv("MAX_PUSH_PER_DAY_PER_USER", "2"))
    verify_geo_radius_m: int = int(os.getenv("VERIFY_GEO_RADIUS_M", "120"))
    verify_default_radius_m: int = int(os.getenv("VERIFY_DEFAULT_RADIUS_M", "120"))
    verify_min_accuracy_m: int = int(os.getenv("VERIFY_MIN_ACCURACY_M", "100"))
    verify_dwell_required_s: int = int(os.getenv("VERIFY_DWELL_REQUIRED_S", "60"))
    verify_ping_max_step_s: int = int(os.getenv("VERIFY_PING_MAX_STEP_S", "15"))
    verify_allow_start_without_target: bool = (
        os.getenv("VERIFY_ALLOW_START_WITHOUT_TARGET", "true").lower() == "true"
    )
    debug_verbose: bool = os.getenv("DEBUG_VERBOSE", "true").lower() == "true"
    verify_time_window_lead_min: int = int(os.getenv("VERIFY_TIME_WINDOW_LEAD_MIN", "10"))
    verify_time_window_tail_min: int = int(os.getenv("VERIFY_TIME_WINDOW_TAIL_MIN", "15"))
    pool_reward_cap_cents: int = int(os.getenv("POOL_REWARD_CAP_CENTS", "150"))

    # Demo Mode (relaxes time window restrictions for testing)
    demo_mode: bool = os.getenv("DEMO_MODE", "true").lower() == "true"

    # Pilot Hub Configuration
    pilot_mode: bool = os.getenv("PILOT_MODE", "true").lower() == "true"
    pilot_hub: str = os.getenv("PILOT_HUB", "domain")  # e.g., "domain"

    # Dev-only flags (DO NOT enable in production)
    nerava_dev_allow_anon_user: bool = (
        os.getenv("NERAVA_DEV_ALLOW_ANON_USER", "false").lower() == "true"
    )
    nerava_dev_allow_anon_driver: bool = (
        os.getenv("NERAVA_DEV_ALLOW_ANON_DRIVER", "false").lower() == "true"
    )

    # Smartcar configuration
    # For local dev, use sandbox mode. In production, set SMARTCAR_MODE=live
    smartcar_client_id: str = os.getenv("SMARTCAR_CLIENT_ID", "")
    smartcar_client_secret: str = os.getenv("SMARTCAR_CLIENT_SECRET", "")
    smartcar_redirect_uri: str = os.getenv("SMARTCAR_REDIRECT_URI", "")
    smartcar_mode: str = os.getenv("SMARTCAR_MODE", "sandbox")  # sandbox (dev) or live (production)
    smartcar_base_url: str = os.getenv("SMARTCAR_BASE_URL", "https://api.smartcar.com")
    smartcar_auth_url: str = os.getenv("SMARTCAR_AUTH_URL", "https://auth.smartcar.com")
    smartcar_connect_url: str = os.getenv("SMARTCAR_CONNECT_URL", "https://connect.smartcar.com")

    @property
    def is_prod(self) -> bool:
        return (self.ENV or "").lower() in ("prod", "production")

    def smartcar_enabled(self) -> bool:
        """
        Check if Smartcar integration is fully configured.
        Returns True only if client_id, client_secret, and redirect_uri are all set.
        """
        return bool(
            self.smartcar_client_id and self.smartcar_client_secret and self.smartcar_redirect_uri
        )


# Global settings instance
settings = Settings()


def get_square_sandbox_config():
    """
    Get Square SANDBOX configuration.

    Returns:
        Dict with application_id, application_secret, redirect_url, base_url, and auth_base_url
        Redirect URL defaults to PUBLIC_BASE_URL + callback path if not explicitly set
    """
    redirect_url = settings.square_redirect_url_sandbox
    if not redirect_url:
        # Default to PUBLIC_BASE_URL + callback path
        redirect_url = f"{settings.public_base_url}/v1/merchants/square/callback"

    return {
        "application_id": settings.square_application_id_sandbox,
        "application_secret": settings.square_application_secret_sandbox,
        "redirect_url": redirect_url,
        "base_url": "https://connect.squareupsandbox.com",  # For API calls
        "auth_base_url": "https://squareupsandbox.com",  # For OAuth authorize page
    }
