"""
Payout Provider Abstraction

Defines the PayoutProvider interface and factory for resolving
the correct provider (Stripe or Dwolla) per wallet.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AccountStatus:
    provider: str
    account_id: str
    onboarding_complete: bool
    status: str  # "active", "restricted", "pending"


class PayoutProvider(ABC):
    """Abstract base for payout providers (Stripe, Dwolla)."""

    @abstractmethod
    def create_account(self, user_id: int, email: str, **kwargs) -> str:
        """Create an external payout account. Returns external account ID."""
        ...

    @abstractmethod
    def get_onboarding_url(self, account_id: str, return_url: str, refresh_url: str) -> str:
        """Get onboarding URL for the driver to complete setup. Returns URL string."""
        ...

    @abstractmethod
    def check_account_status(self, account_id: str) -> AccountStatus:
        """Check the status of an external account."""
        ...

    @abstractmethod
    def create_transfer(self, account_id: str, amount_cents: int, idempotency_key: str, **kwargs) -> str:
        """Create a transfer to the driver. Returns transfer ID."""
        ...

    @abstractmethod
    def get_balance(self, account_id: str) -> int:
        """Get available balance in cents for the account."""
        ...


def get_provider(provider_name: str) -> PayoutProvider:
    """Factory — returns Stripe or Dwolla provider based on wallet.payout_provider.

    Dwolla is only used when ENABLE_DWOLLA_PAYOUTS=true AND the wallet is set to dwolla.
    Otherwise Stripe is always used (the primary provider).
    """
    import os
    dwolla_enabled = os.getenv("ENABLE_DWOLLA_PAYOUTS", "false").lower() == "true"
    if provider_name == "dwolla" and dwolla_enabled:
        from app.services.dwolla_payout_provider import DwollaPayoutProvider
        return DwollaPayoutProvider()
    from app.services.stripe_payout_provider import StripePayoutProvider
    return StripePayoutProvider()
