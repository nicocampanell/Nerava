"""
Dwolla Payout Provider

ACH-based payouts via Dwolla ($0.10-$0.25/txn vs Stripe's ~$2/txn).
Supports both sandbox and production environments.
"""
import logging
import os
import uuid

from app.services.payout_provider import AccountStatus, PayoutProvider

logger = logging.getLogger(__name__)

DWOLLA_KEY = os.getenv("DWOLLA_KEY", "")
DWOLLA_SECRET = os.getenv("DWOLLA_SECRET", "")
DWOLLA_ENV = os.getenv("DWOLLA_ENV", "sandbox")  # sandbox or production

# Initialize Dwolla client
dwolla_client = None
dwolla_api = None
if DWOLLA_KEY and DWOLLA_SECRET:
    try:
        import dwollav2
        dwolla_client = dwollav2.Client(
            key=DWOLLA_KEY,
            secret=DWOLLA_SECRET,
            environment=DWOLLA_ENV,
        )
        dwolla_api = dwolla_client.Auth.client()
        logger.info(f"Dwolla client initialized ({DWOLLA_ENV})")
    except ImportError:
        logger.warning("dwollav2 not installed, Dwolla payouts unavailable")
    except Exception as e:
        logger.error(f"Failed to initialize Dwolla client: {e}")


def _is_mock() -> bool:
    return not dwolla_api or not DWOLLA_KEY


class DwollaPayoutProvider(PayoutProvider):
    """Dwolla ACH payout provider."""

    def create_account(self, user_id: int, email: str, **kwargs) -> str:
        """Create a Dwolla receive-only customer."""
        if _is_mock():
            mock_url = f"https://api-sandbox.dwolla.com/customers/mock-{uuid.uuid4().hex[:12]}"
            logger.info(f"[MOCK] Created mock Dwolla customer {mock_url}")
            return mock_url

        first_name = kwargs.get("first_name", "Driver")
        last_name = kwargs.get("last_name", str(user_id))

        customer = dwolla_api.post("customers", {
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "type": "receive-only",
            "ipAddress": kwargs.get("ip_address"),
        })
        customer_url = customer.headers["Location"]
        logger.info(f"Created Dwolla customer {customer_url} for driver {user_id}")
        return customer_url

    def get_onboarding_url(self, account_id: str, return_url: str, refresh_url: str) -> str:
        """Dwolla receive-only customers don't need onboarding. Return success URL."""
        # For Dwolla, onboarding is handled via Plaid Link (bank account linking)
        # There's no separate Dwolla-hosted onboarding page
        return f"{return_url}?dwolla_ready=true"

    def check_account_status(self, account_id: str) -> AccountStatus:
        if _is_mock():
            return AccountStatus(
                provider="dwolla",
                account_id=account_id,
                onboarding_complete=True,
                status="active",
            )

        customer = dwolla_api.get(account_id)
        status = customer.body.get("status", "unverified")
        return AccountStatus(
            provider="dwolla",
            account_id=account_id,
            onboarding_complete=status in ("verified", "unverified"),  # receive-only always ready
            status="active" if status != "suspended" else "restricted",
        )

    def create_transfer(self, account_id: str, amount_cents: int, idempotency_key: str, **kwargs) -> str:
        """Create ACH transfer from Nerava master account to driver's bank."""
        if _is_mock():
            mock_url = f"https://api-sandbox.dwolla.com/transfers/mock-{uuid.uuid4().hex[:12]}"
            logger.info(f"[MOCK] Created mock Dwolla transfer {mock_url}")
            return mock_url

        funding_source = kwargs.get("funding_source_url")
        if not funding_source:
            raise ValueError("Dwolla transfer requires a funding_source_url (driver's bank account)")

        # Get Nerava master funding source
        master_funding_source = os.getenv("DWOLLA_MASTER_FUNDING_SOURCE", "")
        if not master_funding_source:
            raise ValueError("DWOLLA_MASTER_FUNDING_SOURCE not configured")

        amount_dollars = f"{amount_cents / 100:.2f}"

        transfer = dwolla_api.post("transfers", {
            "_links": {
                "source": {"href": master_funding_source},
                "destination": {"href": funding_source},
            },
            "amount": {
                "currency": "USD",
                "value": amount_dollars,
            },
            "metadata": {
                "idempotency_key": idempotency_key,
                "driver_id": kwargs.get("driver_id", ""),
            },
        }, headers={"Idempotency-Key": idempotency_key})

        transfer_url = transfer.headers["Location"]
        logger.info(f"Created Dwolla transfer {transfer_url}: ${amount_dollars}")
        return transfer_url

    def get_balance(self, account_id: str) -> int:
        """Dwolla receive-only customers don't hold balances. Return 0."""
        return 0
