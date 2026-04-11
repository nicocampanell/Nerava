"""
Schemas for Wallet Pass Activation API
"""

from pydantic import BaseModel


class WalletActivateRequest(BaseModel):
    session_id: str
    merchant_id: str


class WalletState(BaseModel):
    state: str  # "ACTIVE"
    merchant_id: str
    expires_at: str  # ISO8601
    active_copy: str

    class Config:
        from_attributes = True


class WalletActivateResponse(BaseModel):
    status: str  # "ok"
    wallet_state: WalletState

