"""
Plaid Link endpoints for bank account linking (Dwolla payout users).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.models.funding_source import FundingSource
from app.services import plaid_service

router = APIRouter(prefix="/v1/wallet/plaid", tags=["plaid"])
logger = logging.getLogger(__name__)


class LinkTokenResponse(BaseModel):
    link_token: str
    expiration: str


class ExchangeRequest(BaseModel):
    public_token: str
    account_id: str  # Plaid account ID selected by user


class ExchangeResponse(BaseModel):
    ok: bool
    funding_source_id: str
    institution_name: Optional[str] = None
    account_mask: Optional[str] = None


class FundingSourceInfo(BaseModel):
    id: str
    institution_name: Optional[str] = None
    account_mask: Optional[str] = None
    account_type: Optional[str] = None
    is_default: bool
    created_at: str


class FundingSourcesResponse(BaseModel):
    funding_sources: List[FundingSourceInfo]


@router.post("/link-token", response_model=LinkTokenResponse)
def create_link_token(
    current_user: User = Depends(get_current_user),
):
    """Create a Plaid Link token for the frontend."""
    try:
        result = plaid_service.create_link_token(current_user.id)
        return LinkTokenResponse(**result)
    except Exception as e:
        logger.error(f"Failed to create Plaid link token: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize bank linking")


@router.post("/exchange", response_model=ExchangeResponse)
def exchange_public_token(
    body: ExchangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exchange Plaid public token after user links bank account."""
    try:
        # 1. Exchange token
        token_data = plaid_service.exchange_public_token(body.public_token)
        access_token = token_data["access_token"]

        # 2. Get account details
        accounts = plaid_service.get_accounts(access_token)
        selected = next((a for a in accounts if a["account_id"] == body.account_id), None)
        if not selected:
            selected = accounts[0] if accounts else {}

        # 3. Get institution name
        institution = plaid_service.get_institution_name(access_token)

        # 4. Create Dwolla processor token + funding source (only if Dwolla enabled)
        import os as _os
        _dwolla_enabled = _os.getenv("ENABLE_DWOLLA_PAYOUTS", "false").lower() == "true"

        from app.models.driver_wallet import DriverWallet
        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == current_user.id).first()

        external_funding_id = ""
        if _dwolla_enabled:
            processor_token = plaid_service.create_dwolla_processor_token(access_token, body.account_id)

            from app.services.dwolla_payout_provider import _is_mock, dwolla_api
            dwolla_customer_url = getattr(wallet, 'external_account_id', None) if wallet else None

            # Auto-create Dwolla customer if none exists
            if not dwolla_customer_url and not _is_mock():
                try:
                    from app.services.dwolla_payout_provider import DwollaPayoutProvider
                    provider = DwollaPayoutProvider()
                    email = getattr(current_user, 'email', '') or f"driver-{current_user.id}@nerava.network"
                    dwolla_customer_url = provider.create_account(
                        current_user.id, email,
                        first_name=getattr(current_user, 'first_name', 'Driver') or 'Driver',
                        last_name=getattr(current_user, 'last_name', str(current_user.id)) or str(current_user.id),
                    )
                    if wallet:
                        wallet.external_account_id = dwolla_customer_url
                        wallet.payout_provider = "dwolla"
                    logger.info(f"Auto-created Dwolla customer for user {current_user.id}")
                except Exception as dwolla_err:
                    logger.error(f"Failed to auto-create Dwolla customer: {dwolla_err}")

            if _is_mock() or not dwolla_customer_url:
                external_funding_id = f"mock-funding-{uuid.uuid4().hex[:12]}"
            else:
                fs_response = dwolla_api.post(
                    f"{dwolla_customer_url}/funding-sources",
                    {"plaidToken": processor_token, "name": selected.get("name", "Bank Account")},
                )
                external_funding_id = fs_response.headers["Location"]
        else:
            # Dwolla disabled — store Plaid account info only (Stripe handles payouts)
            external_funding_id = f"plaid-{body.account_id}"

        # 6. Store funding source
        # Mark existing as non-default
        db.query(FundingSource).filter(
            FundingSource.user_id == current_user.id,
            FundingSource.removed_at.is_(None),
        ).update({"is_default": False})

        fs = FundingSource(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            provider="dwolla",
            external_id=external_funding_id,
            institution_name=institution or selected.get("name"),
            account_mask=selected.get("mask"),
            account_type=selected.get("subtype", "checking"),
            is_default=True,
        )
        db.add(fs)

        # Update wallet bank_verified flag
        if wallet:
            wallet.bank_verified = True
            wallet.updated_at = datetime.utcnow()

        db.commit()

        logger.info(f"Bank account linked for user {current_user.id}: {institution} ...{selected.get('mask')}")

        return ExchangeResponse(
            ok=True,
            funding_source_id=fs.id,
            institution_name=fs.institution_name,
            account_mask=fs.account_mask,
        )

    except Exception as e:
        logger.error(f"Failed to exchange Plaid token: {e}")
        raise HTTPException(status_code=500, detail="Failed to link bank account")


# Funding sources endpoints on parent wallet prefix
wallet_router = APIRouter(prefix="/v1/wallet", tags=["wallet"])


@wallet_router.get("/funding-sources", response_model=FundingSourcesResponse)
def get_funding_sources(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get user's linked bank accounts/cards."""
    sources = db.query(FundingSource).filter(
        FundingSource.user_id == current_user.id,
        FundingSource.removed_at.is_(None),
    ).order_by(FundingSource.created_at.desc()).all()

    return FundingSourcesResponse(
        funding_sources=[
            FundingSourceInfo(
                id=str(s.id),
                institution_name=s.institution_name,
                account_mask=s.account_mask,
                account_type=s.account_type,
                is_default=s.is_default,
                created_at=s.created_at.isoformat(),
            )
            for s in sources
        ]
    )


@wallet_router.delete("/funding-sources/{source_id}")
def remove_funding_source(
    source_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a linked bank account/card."""
    source = db.query(FundingSource).filter(
        FundingSource.id == source_id,
        FundingSource.user_id == current_user.id,
        FundingSource.removed_at.is_(None),
    ).first()

    if not source:
        raise HTTPException(status_code=404, detail="Funding source not found")

    source.removed_at = datetime.utcnow()
    db.commit()

    logger.info(f"Funding source {source_id} removed for user {current_user.id}")
    return {"ok": True}
