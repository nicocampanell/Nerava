"""
Merchant Rewards Router

Handles:
- POST /v1/merchants/{place_id}/request-join — Request a merchant to join Nerava
- GET  /v1/merchants/{place_id}/request-join/count — Get join request count
- POST /v1/rewards/claim — Claim a merchant reward
- GET  /v1/rewards/claims/active — Get active claims for current driver
- GET  /v1/rewards/claims/{id} — Get claim detail
- POST /v1/rewards/claims/{id}/receipt — Upload receipt (base64 image)
"""
import base64
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver, get_current_driver_optional
from app.models import User
from app.schemas.merchant_reward import (
    ActiveClaimsResponse,
    ClaimDetailResponse,
    ClaimRewardRequest,
    ClaimRewardResponse,
    JoinRequestCountResponse,
    ReceiptUploadResponse,
    RequestToJoinRequest,
    RequestToJoinResponse,
)
from app.services.merchant_reward_service import (
    create_join_request,
    create_receipt_submission,
    create_reward_claim,
    get_active_claims,
    get_claim_by_id,
    get_join_request_count,
    get_receipt_for_claim,
    process_receipt_ocr,
    user_has_requested,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["merchant_rewards"])


# ---------------------------------------------------------------------------
# Request-to-Join
# ---------------------------------------------------------------------------

@router.post(
    "/merchants/{place_id}/request-join",
    response_model=RequestToJoinResponse,
    summary="Request a merchant to join Nerava",
)
def request_merchant_join(
    place_id: str,
    request: RequestToJoinRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Submit a request for a merchant to join Nerava.
    Idempotent per driver + place_id.
    """
    try:
        join_req = create_join_request(
            db=db,
            driver_user_id=driver.id,
            place_id=place_id,
            merchant_name=request.merchant_name,
            merchant_id=request.merchant_id,
            charger_id=request.charger_id,
            interest_tags=request.interest_tags,
            note=request.note,
        )
        count = get_join_request_count(db, place_id)

        return RequestToJoinResponse(
            id=join_req.id,
            place_id=place_id,
            merchant_name=join_req.merchant_name,
            status=join_req.status,
            request_count=count,
            created_at=join_req.created_at.isoformat(),
        )
    except Exception as e:
        logger.error(f"Error creating join request: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit request",
        )


@router.get(
    "/merchants/{place_id}/request-join/count",
    response_model=JoinRequestCountResponse,
    summary="Get join request count for a merchant",
)
def get_merchant_join_count(
    place_id: str,
    db: Session = Depends(get_db),
    driver: User = Depends(get_current_driver_optional),
):
    """Get the number of drivers who have requested this merchant. Public endpoint."""
    count = get_join_request_count(db, place_id)
    has_requested = False
    if driver:
        has_requested = user_has_requested(db, driver.id, place_id)

    return JoinRequestCountResponse(
        place_id=place_id,
        request_count=count,
        user_has_requested=has_requested,
    )


# ---------------------------------------------------------------------------
# Reward Claims
# ---------------------------------------------------------------------------

@router.post(
    "/rewards/claim",
    response_model=ClaimRewardResponse,
    summary="Claim a merchant reward",
)
def claim_reward(
    request: ClaimRewardRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Claim a merchant reward. Creates a time-limited claim (2 hours).
    Driver must visit merchant and upload receipt before expiry.
    Idempotent — returns existing active claim if one exists.
    """
    try:
        claim = create_reward_claim(
            db=db,
            driver_user_id=driver.id,
            merchant_name=request.merchant_name,
            merchant_id=request.merchant_id,
            place_id=request.place_id,
            reward_description=request.reward_description,
            charger_id=request.charger_id,
            session_event_id=request.session_event_id,
        )

        remaining = max(0, int((claim.expires_at - datetime.utcnow()).total_seconds()))

        return ClaimRewardResponse(
            id=claim.id,
            merchant_name=claim.merchant_name,
            reward_description=claim.reward_description,
            status=claim.status.value if hasattr(claim.status, 'value') else claim.status,
            claimed_at=claim.claimed_at.isoformat(),
            expires_at=claim.expires_at.isoformat(),
            remaining_seconds=remaining,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating reward claim: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim reward",
        )


@router.get(
    "/rewards/claims/active",
    response_model=ActiveClaimsResponse,
    summary="Get active reward claims",
)
def list_active_claims(
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get all active (unexpired) reward claims for the current driver."""
    claims = get_active_claims(db, driver.id)
    return ActiveClaimsResponse(
        claims=[
            ClaimRewardResponse(
                id=c.id,
                merchant_name=c.merchant_name or "",
                reward_description=c.reward_description,
                status=c.status.value if hasattr(c.status, 'value') else c.status,
                claimed_at=c.claimed_at.isoformat(),
                expires_at=c.expires_at.isoformat(),
                remaining_seconds=max(0, int((c.expires_at - datetime.utcnow()).total_seconds())),
            )
            for c in claims
        ]
    )


@router.get(
    "/rewards/claims/{claim_id}",
    response_model=ClaimDetailResponse,
    summary="Get claim detail with receipt status",
)
def get_claim_detail(
    claim_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get detailed claim info including receipt OCR results."""
    claim = get_claim_by_id(db, claim_id, driver.id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    receipt = get_receipt_for_claim(db, claim_id) if claim.receipt_submission_id else None
    receipt_data = None
    if receipt:
        receipt_data = ReceiptUploadResponse(
            id=receipt.id,
            reward_claim_id=receipt.reward_claim_id,
            status=receipt.status.value if hasattr(receipt.status, 'value') else receipt.status,
            ocr_merchant_name=receipt.ocr_merchant_name,
            ocr_total_cents=receipt.ocr_total_cents,
            ocr_confidence=receipt.ocr_confidence,
            approved_reward_cents=receipt.approved_reward_cents,
            rejection_reason=receipt.rejection_reason,
        )

    return ClaimDetailResponse(
        id=claim.id,
        merchant_name=claim.merchant_name or "",
        reward_description=claim.reward_description,
        status=claim.status.value if hasattr(claim.status, 'value') else claim.status,
        claimed_at=claim.claimed_at.isoformat(),
        expires_at=claim.expires_at.isoformat(),
        remaining_seconds=max(0, int((claim.expires_at - datetime.utcnow()).total_seconds())),
        receipt=receipt_data,
    )


# ---------------------------------------------------------------------------
# Receipt Upload
# ---------------------------------------------------------------------------

@router.post(
    "/rewards/claims/{claim_id}/receipt",
    response_model=ReceiptUploadResponse,
    summary="Upload receipt for a claimed reward",
)
async def upload_receipt(
    claim_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
    image: UploadFile = File(None),
    image_base64: str = Form(None),
):
    """
    Upload a receipt photo for OCR verification.
    Accepts either a file upload or a base64-encoded image.
    """
    claim = get_claim_by_id(db, claim_id, driver.id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    # Upload image to S3 (or use local storage for dev)
    image_url, image_key = await _store_receipt_image(
        claim_id=claim_id,
        driver_id=driver.id,
        image=image,
        image_base64=image_base64,
    )

    try:
        submission = create_receipt_submission(
            db=db,
            driver_user_id=driver.id,
            reward_claim_id=claim_id,
            image_url=image_url,
            image_key=image_key,
        )

        # Process OCR (async but fast — typically < 4 seconds)
        submission = await process_receipt_ocr(db, submission.id)

        return ReceiptUploadResponse(
            id=submission.id,
            reward_claim_id=submission.reward_claim_id,
            status=submission.status.value if hasattr(submission.status, 'value') else submission.status,
            ocr_merchant_name=submission.ocr_merchant_name,
            ocr_total_cents=submission.ocr_total_cents,
            ocr_confidence=submission.ocr_confidence,
            approved_reward_cents=submission.approved_reward_cents,
            rejection_reason=submission.rejection_reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process receipt",
        )


async def _store_receipt_image(
    claim_id: str,
    driver_id: int,
    image: UploadFile = None,
    image_base64: str = None,
) -> tuple:
    """Store receipt image in S3 or locally. Returns (url, key)."""
    import boto3
    from botocore.exceptions import ClientError

    s3_bucket = os.getenv("RECEIPT_S3_BUCKET", os.getenv("S3_BUCKET", ""))
    key = f"receipts/{driver_id}/{claim_id}/{uuid.uuid4()}.jpg"

    if image:
        image_bytes = await image.read()
    elif image_base64:
        # Strip data URI prefix if present
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        image_bytes = base64.b64decode(image_base64)
    else:
        raise ValueError("No image provided. Send either 'image' file or 'image_base64' form field.")

    if s3_bucket:
        try:
            s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            s3.put_object(
                Bucket=s3_bucket,
                Key=key,
                Body=image_bytes,
                ContentType="image/jpeg",
            )
            url = f"https://{s3_bucket}.s3.amazonaws.com/{key}"
            return url, key
        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            # Fall through to local storage

    # Local storage fallback (dev)
    local_dir = "/tmp/nerava_receipts"
    os.makedirs(local_dir, exist_ok=True)
    local_path = f"{local_dir}/{claim_id}_{uuid.uuid4()}.jpg"
    with open(local_path, "wb") as f:
        f.write(image_bytes)
    return f"file://{local_path}", local_path
