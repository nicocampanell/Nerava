"""
Stripe Payout Provider

Wraps existing Stripe Express calls into the PayoutProvider interface.
Zero behavior change for existing Stripe users.
"""
import logging
import os
import uuid

from app.services.payout_provider import AccountStatus, PayoutProvider

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
ENABLE_STRIPE_PAYOUTS = os.getenv("ENABLE_STRIPE_PAYOUTS", "false").lower() == "true"

# Initialize Stripe
stripe = None
if STRIPE_SECRET_KEY:
    try:
        import stripe as stripe_module
        stripe = stripe_module
        stripe.api_key = STRIPE_SECRET_KEY
    except ImportError:
        pass


def _is_mock() -> bool:
    return not ENABLE_STRIPE_PAYOUTS or not stripe or not STRIPE_SECRET_KEY


class StripePayoutProvider(PayoutProvider):
    """Stripe Express payout provider."""

    def create_account(self, user_id: int, email: str, **kwargs) -> str:
        if _is_mock():
            mock_id = f"acct_mock_{uuid.uuid4().hex[:16]}"
            logger.info(f"[MOCK] Created mock Stripe account {mock_id}")
            return mock_id

        account = stripe.Account.create(
            type="express",
            country="US",
            email=email or None,
            business_type="individual",
            capabilities={"transfers": {"requested": True}},
            business_profile={
                "product_description": "EV charging rewards recipient",
                "mcc": "7299",
                "url": "https://nerava.network",
            },
            metadata={"driver_id": str(user_id), "platform": "nerava"},
        )

        logger.info(f"Created Stripe Express account {account.id} for driver {user_id}")
        return account.id

    def get_onboarding_url(self, account_id: str, return_url: str, refresh_url: str) -> str:
        if _is_mock():
            return f"{return_url}?mock_onboarding=complete"

        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )
        return link.url

    def check_account_status(self, account_id: str) -> AccountStatus:
        if _is_mock():
            return AccountStatus(
                provider="stripe",
                account_id=account_id,
                onboarding_complete=True,
                status="active",
            )

        account = stripe.Account.retrieve(account_id)
        capabilities = account.get("capabilities", {})
        transfers_active = capabilities.get("transfers") == "active"
        return AccountStatus(
            provider="stripe",
            account_id=account_id,
            onboarding_complete=transfers_active,
            status="active" if transfers_active else "restricted",
        )

    def create_transfer(self, account_id: str, amount_cents: int, idempotency_key: str, **kwargs) -> str:
        if _is_mock():
            mock_id = f"tr_mock_{uuid.uuid4().hex[:16]}"
            logger.info(f"[MOCK] Created mock transfer {mock_id}")
            return mock_id

        transfer = stripe.Transfer.create(
            amount=amount_cents,
            currency="usd",
            destination=account_id,
            metadata=kwargs.get("metadata", {}),
            idempotency_key=idempotency_key,
        )
        logger.info(f"Created Stripe transfer {transfer.id}")
        return transfer.id

    def get_balance(self, account_id: str) -> int:
        if _is_mock():
            return 0

        balance = stripe.Balance.retrieve(stripe_account=account_id)
        available = balance.get("available", [])
        for entry in available:
            if entry.get("currency") == "usd":
                return entry.get("amount", 0)
        return 0
