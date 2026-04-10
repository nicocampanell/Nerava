"""
Schemas for Merchant Rewards: Request-to-Join, Reward Claims, Receipt Submissions
"""
from typing import List, Optional

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Request-to-Join
# ---------------------------------------------------------------------------

class RequestToJoinRequest(BaseModel):
    place_id: str
    merchant_name: str
    merchant_id: Optional[str] = None
    charger_id: Optional[str] = None
    interest_tags: Optional[List[str]] = None     # ["coffee", "food", "discount"]
    note: Optional[str] = None


class RequestToJoinResponse(BaseModel):
    id: str
    place_id: str
    merchant_name: str
    status: str
    request_count: int                             # Total requests for this merchant
    created_at: str


class JoinRequestCountResponse(BaseModel):
    place_id: str
    merchant_name: Optional[str] = None
    request_count: int
    user_has_requested: bool


# ---------------------------------------------------------------------------
# Reward Claims
# ---------------------------------------------------------------------------

class ClaimRewardRequest(BaseModel):
    merchant_id: Optional[str] = None
    place_id: Optional[str] = None
    merchant_name: str
    reward_description: Optional[str] = None       # "Free Margarita"
    charger_id: Optional[str] = None
    session_event_id: Optional[str] = None


class ClaimRewardResponse(BaseModel):
    id: str
    merchant_name: str
    reward_description: Optional[str] = None
    status: str
    claimed_at: str
    expires_at: str
    remaining_seconds: int


class ActiveClaimsResponse(BaseModel):
    claims: List[ClaimRewardResponse]


# ---------------------------------------------------------------------------
# Receipt Submissions
# ---------------------------------------------------------------------------

class ReceiptUploadResponse(BaseModel):
    id: str
    reward_claim_id: str
    status: str
    ocr_merchant_name: Optional[str] = None
    ocr_total_cents: Optional[int] = None
    ocr_confidence: Optional[float] = None
    approved_reward_cents: Optional[int] = None
    rejection_reason: Optional[str] = None


class ClaimDetailResponse(BaseModel):
    id: str
    merchant_name: str
    reward_description: Optional[str] = None
    status: str
    claimed_at: str
    expires_at: str
    remaining_seconds: int
    receipt: Optional[ReceiptUploadResponse] = None


# ---------------------------------------------------------------------------
# Merchant Reward State (for merchant detail enrichment)
# ---------------------------------------------------------------------------

class MerchantRewardState(BaseModel):
    """Injected into merchant detail response to drive CTA logic."""
    has_active_reward: bool = False
    reward_description: Optional[str] = None
    reward_amount_cents: Optional[int] = None
    # Claim state for current driver
    active_claim_id: Optional[str] = None
    active_claim_status: Optional[str] = None       # claimed | receipt_uploaded | approved
    active_claim_expires_at: Optional[str] = None
    # Request-to-join state
    join_request_count: int = 0
    user_has_requested: bool = False
