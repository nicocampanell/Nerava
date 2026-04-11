"""
Plaid Link Integration for Bank Account + Debit Card Linking

Uses Plaid-Dwolla integration for instant bank verification.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.getenv("PLAID_SECRET", "")
PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")  # sandbox or production

# Initialize Plaid client
plaid_client = None
if PLAID_CLIENT_ID and PLAID_SECRET:
    try:
        import plaid
        from plaid.api import plaid_api

        host_map = {
            "sandbox": plaid.Environment.Sandbox,
            "development": plaid.Environment.Development,
            "production": plaid.Environment.Production,
        }
        configuration = plaid.Configuration(
            host=host_map.get(PLAID_ENV, plaid.Environment.Sandbox),
            api_key={
                "clientId": PLAID_CLIENT_ID,
                "secret": PLAID_SECRET,
            },
        )
        api_client = plaid.ApiClient(configuration)
        plaid_client = plaid_api.PlaidApi(api_client)
        logger.info(f"Plaid client initialized ({PLAID_ENV})")
    except ImportError:
        logger.warning("plaid-python not installed, Plaid Link unavailable")
    except Exception as e:
        logger.error(f"Failed to initialize Plaid client: {e}")


def _is_mock() -> bool:
    return not plaid_client or not PLAID_CLIENT_ID


def create_link_token(user_id: int, client_name: str = "Nerava") -> dict:
    """Create a Plaid Link token for the frontend to open Plaid Link."""
    if _is_mock():
        return {
            "link_token": f"link-sandbox-mock-{user_id}",
            "expiration": "2099-01-01T00:00:00Z",
        }

    from plaid.model.country_code import CountryCode
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.products import Products

    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=str(user_id)),
        client_name=client_name,
        products=[Products("auth")],
        country_codes=[CountryCode("US")],
        language="en",
    )

    response = plaid_client.link_token_create(request)
    return {
        "link_token": response.link_token,
        "expiration": response.expiration,
    }


def exchange_public_token(public_token: str) -> dict:
    """Exchange Plaid public token for access token and processor token."""
    if _is_mock():
        return {
            "access_token": f"access-sandbox-mock-{public_token[:8]}",
            "item_id": f"item-mock-{public_token[:8]}",
        }

    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = plaid_client.item_public_token_exchange(request)
    return {
        "access_token": response.access_token,
        "item_id": response.item_id,
    }


def create_dwolla_processor_token(access_token: str, account_id: str) -> str:
    """Create a Dwolla processor token from Plaid access token."""
    if _is_mock():
        return f"processor-sandbox-mock-{account_id[:8]}"

    from plaid.model.processor_token_create_request import ProcessorTokenCreateRequest

    request = ProcessorTokenCreateRequest(
        access_token=access_token,
        account_id=account_id,
        processor="dwolla",
    )
    response = plaid_client.processor_token_create(request)
    return response.processor_token


def get_accounts(access_token: str) -> list:
    """Get accounts linked via Plaid."""
    if _is_mock():
        return [
            {
                "account_id": "mock-account-1",
                "name": "Mock Checking",
                "mask": "1234",
                "type": "depository",
                "subtype": "checking",
            }
        ]

    from plaid.model.accounts_get_request import AccountsGetRequest

    request = AccountsGetRequest(access_token=access_token)
    response = plaid_client.accounts_get(request)
    return [
        {
            "account_id": acct.account_id,
            "name": acct.name,
            "mask": acct.mask,
            "type": acct.type.value if hasattr(acct.type, "value") else str(acct.type),
            "subtype": acct.subtype.value if hasattr(acct.subtype, "value") else str(acct.subtype),
        }
        for acct in response.accounts
    ]


def get_institution_name(access_token: str) -> Optional[str]:
    """Get the institution name for a Plaid item."""
    if _is_mock():
        return "Mock Bank"

    try:
        from plaid.model.country_code import CountryCode
        from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
        from plaid.model.item_get_request import ItemGetRequest

        item_request = ItemGetRequest(access_token=access_token)
        item_response = plaid_client.item_get(item_request)
        institution_id = item_response.item.institution_id

        if institution_id:
            inst_request = InstitutionsGetByIdRequest(
                institution_id=institution_id,
                country_codes=[CountryCode("US")],
            )
            inst_response = plaid_client.institutions_get_by_id(inst_request)
            return inst_response.institution.name
    except Exception as e:
        logger.warning(f"Failed to get institution name: {e}")
    return None
