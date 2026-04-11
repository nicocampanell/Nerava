"""
Account management router
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.dependencies_domain import get_current_user
from app.models import (
    ExclusiveSession,
    IntentSession,
    NovaTransaction,
    RefreshToken,
    User,
    UserConsent,
)
from app.models.device_token import DeviceToken
from app.models.domain import DriverWallet
from app.models.session_event import IncentiveGrant, SessionEvent
from app.models.tesla_connection import TeslaConnection
from app.models.vehicle import VehicleAccount, VehicleToken
from app.models.while_you_charge import FavoriteMerchant
from app.schemas.account import AccountStats, FavoriteChargerInfo, ProfileResponse, ProfileUpdate
from app.services.audit import log_admin_action

router = APIRouter(prefix="/v1/account", tags=["account"])

logger = logging.getLogger(__name__)


@router.put("/profile", response_model=ProfileResponse)
def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update user profile fields (email, display_name)."""
    if body.email is not None:
        current_user.email = body.email
    if body.display_name is not None:
        current_user.display_name = body.display_name
    db.commit()

    return ProfileResponse(
        email=current_user.email,
        display_name=current_user.display_name,
        phone=current_user.phone,
        vehicle_model=current_user.vehicle_model,
        member_since=current_user.created_at.isoformat() if current_user.created_at else None,
    )


@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current user profile."""
    return ProfileResponse(
        email=current_user.email,
        display_name=current_user.display_name,
        phone=current_user.phone,
        vehicle_model=current_user.vehicle_model,
        member_since=current_user.created_at.isoformat() if current_user.created_at else None,
    )


@router.get("/stats", response_model=AccountStats)
def get_account_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get driver account stats (total sessions, kWh, earnings, streak, CO2)."""
    from sqlalchemy import func

    user_id = current_user.id

    # Session stats
    session_stats = (
        db.query(
            func.count(SessionEvent.id),
            func.coalesce(func.sum(SessionEvent.kwh_delivered), 0),
        )
        .filter(
            SessionEvent.driver_user_id == user_id,
            SessionEvent.session_end.isnot(None),
        )
        .first()
    )

    total_sessions = session_stats[0] if session_stats else 0
    total_kwh = float(session_stats[1]) if session_stats else 0.0

    # Wallet / earnings
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user_id).first()
    total_earned_cents = 0
    total_nova = 0
    if wallet:
        total_earned_cents = getattr(wallet, "total_earned_cents", 0) or 0
        total_nova = getattr(wallet, "nova_balance", 0) or 0

    # Favorite charger (most sessions)
    fav_charger = None
    if total_sessions > 0:
        from app.models.while_you_charge import Charger

        fav_row = (
            db.query(
                SessionEvent.charger_id,
                func.count(SessionEvent.id).label("cnt"),
            )
            .filter(
                SessionEvent.driver_user_id == user_id,
                SessionEvent.session_end.isnot(None),
                SessionEvent.charger_id.isnot(None),
            )
            .group_by(SessionEvent.charger_id)
            .order_by(func.count(SessionEvent.id).desc())
            .first()
        )

        if fav_row and fav_row[0]:
            charger = db.query(Charger).filter(Charger.id == fav_row[0]).first()
            if charger:
                fav_charger = FavoriteChargerInfo(name=charger.name, sessions=fav_row[1])

    # Streak (consecutive days)
    streak = 0
    if wallet:
        streak = getattr(wallet, "streak_days", 0) or 0

    # CO2 avoided: EPA average ~0.4 kg CO2 per kWh displaced
    co2_avoided_kg = round(total_kwh * 0.4, 1)

    member_since = current_user.created_at.isoformat() if current_user.created_at else None

    return AccountStats(
        total_sessions=total_sessions,
        total_kwh=round(total_kwh, 1),
        total_earned_cents=total_earned_cents,
        total_nova=total_nova,
        favorite_charger=fav_charger,
        member_since=member_since,
        current_streak=streak,
        co2_avoided_kg=co2_avoided_kg,
    )


class PreferencesUpdate(BaseModel):
    notifications_enabled: Optional[bool] = None
    email_marketing: Optional[bool] = None


class PreferencesResponse(BaseModel):
    notifications_enabled: bool
    email_marketing: bool


@router.put("/preferences", response_model=PreferencesResponse)
@router.patch("/preferences", response_model=PreferencesResponse)
def update_preferences(
    body: PreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update user notification/marketing preferences."""
    if body.notifications_enabled is not None:
        current_user.notifications_enabled = body.notifications_enabled
    if body.email_marketing is not None:
        current_user.email_marketing = body.email_marketing
    db.commit()
    return PreferencesResponse(
        notifications_enabled=getattr(current_user, "notifications_enabled", True) or True,
        email_marketing=getattr(current_user, "email_marketing", False) or False,
    )


@router.get("/preferences", response_model=PreferencesResponse)
def get_preferences(
    current_user: User = Depends(get_current_user),
):
    """Get current user preferences."""
    return PreferencesResponse(
        notifications_enabled=getattr(current_user, "notifications_enabled", True) or True,
        email_marketing=getattr(current_user, "email_marketing", False) or False,
    )


class FeedbackRequest(BaseModel):
    message: str


class DeleteRequest(BaseModel):
    confirmation: str


class DeleteResponse(BaseModel):
    ok: bool


class ExportResponse(BaseModel):
    ok: bool
    data: dict


@router.post("/feedback")
def submit_feedback(
    body: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit user feedback."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Feedback message cannot be empty")
    logger.info(f"Feedback from user {current_user.id}: {body.message[:500]}")
    return {"ok": True}


# Also support POST /v1/account/delete for clients that can't send DELETE with body
@router.post("/delete")
def delete_account_post(
    request: DeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete account via POST (for clients that can't send DELETE with body)."""
    return delete_account(request, current_user, db)


# ─── Vehicle Endpoint ──────────────────────────────────────────────


class VehicleRequest(BaseModel):
    color: str
    model: str


class VehicleResponse(BaseModel):
    color: str
    model: str
    set_at: str


@router.put("/vehicle", response_model=VehicleResponse)
def set_vehicle(
    req: VehicleRequest,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Set or update driver vehicle info (one-time, editable)."""
    now = datetime.now(timezone.utc)
    current_user.vehicle_color = req.color
    current_user.vehicle_model = req.model
    current_user.vehicle_set_at = now
    db.commit()

    return VehicleResponse(
        color=req.color,
        model=req.model,
        set_at=now.isoformat() + "Z",
    )


@router.get("/vehicle", response_model=VehicleResponse)
def get_vehicle(
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get driver's saved vehicle info."""
    color = getattr(current_user, "vehicle_color", None)
    model = getattr(current_user, "vehicle_model", None)
    set_at = getattr(current_user, "vehicle_set_at", None)

    if not color and not model:
        raise HTTPException(status_code=404, detail="No vehicle saved")

    return VehicleResponse(
        color=color or "",
        model=model or "",
        set_at=set_at.isoformat() + "Z" if set_at else "",
    )


# ─── Export / Delete ───────────────────────────────────────────────


@router.post("/export", response_model=ExportResponse)
def export_account_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Export user account data in JSON format.

    Returns:
    - User profile (anonymized if deleted)
    - Wallet balance and transactions
    - Exclusive sessions (anonymized if deleted)
    - Intent sessions
    - Nova transactions
    - Consents
    """
    user_id = current_user.id

    logger.info(
        f"Account export requested for user {user_id} (public_id: {current_user.public_id})"
    )

    # 1. User profile
    user_data = {
        "id": user_id,
        "public_id": str(current_user.public_id),
        "email": current_user.email,
        "phone": current_user.phone,
        "display_name": current_user.display_name,
        "is_active": current_user.is_active,
        "role_flags": current_user.role_flags,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None,
    }

    # 2. Wallet balance
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user_id).first()
    wallet_data = None
    if wallet:
        wallet_data = {
            "nova_balance": wallet.nova_balance,
            "energy_reputation_score": wallet.energy_reputation_score,
            "created_at": wallet.created_at.isoformat() if wallet.created_at else None,
            "updated_at": wallet.updated_at.isoformat() if wallet.updated_at else None,
        }

    # 3. Nova transactions
    transactions = (
        db.query(NovaTransaction)
        .filter(NovaTransaction.driver_user_id == user_id)
        .order_by(NovaTransaction.created_at.desc())
        .all()
    )
    transactions_data = [
        {
            "id": str(tx.id),
            "type": tx.type,
            "amount": tx.amount,
            "merchant_id": tx.merchant_id,
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
            "metadata": tx.metadata,
        }
        for tx in transactions
    ]

    # 4. Exclusive sessions
    exclusive_sessions = (
        db.query(ExclusiveSession)
        .filter(ExclusiveSession.driver_id == user_id)
        .order_by(ExclusiveSession.created_at.desc())
        .all()
    )
    exclusive_sessions_data = [
        {
            "id": str(session.id),
            "merchant_id": session.merchant_id,
            "charger_id": session.charger_id,
            "status": (
                session.status.value if hasattr(session.status, "value") else str(session.status)
            ),
            "activated_at": session.activated_at.isoformat() if session.activated_at else None,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }
        for session in exclusive_sessions
    ]

    # 5. Intent sessions
    intent_sessions = (
        db.query(IntentSession)
        .filter(IntentSession.user_id == user_id)
        .order_by(IntentSession.created_at.desc())
        .all()
    )
    intent_sessions_data = [
        {
            "id": str(session.id),
            "lat": session.lat,
            "lng": session.lng,
            "accuracy_m": session.accuracy_m,
            "charger_id": session.charger_id,
            "charger_distance_m": session.charger_distance_m,
            "confidence_tier": session.confidence_tier,
            "source": session.source,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }
        for session in intent_sessions
    ]

    # 6. Consents
    consents = db.query(UserConsent).filter(UserConsent.user_id == user_id).all()
    consents_data = [
        {
            "consent_type": consent.consent_type,
            "granted": consent.is_granted(),
            "granted_at": consent.granted_at.isoformat() if consent.granted_at else None,
            "revoked_at": consent.revoked_at.isoformat() if consent.revoked_at else None,
            "created_at": consent.created_at.isoformat() if consent.created_at else None,
        }
        for consent in consents
    ]

    export_data = {
        "user": user_data,
        "wallet": wallet_data,
        "transactions": transactions_data,
        "exclusive_sessions": exclusive_sessions_data,
        "intent_sessions": intent_sessions_data,
        "consents": consents_data,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    return ExportResponse(ok=True, data=export_data)


@router.delete("", response_model=DeleteResponse)
def delete_account(
    request: DeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete user account with anonymization.

    Requires explicit confirmation by typing "DELETE" in the request body.

    Performs:
    - Anonymizes user data (email, phone, display_name)
    - Deletes related records (refresh_tokens, vehicle_tokens, favorite_merchants, user_consents)
    - Anonymizes references in exclusive_sessions (driver_id → -1)
    - Anonymizes references in nova_transactions (driver_user_id → -1, keep transactions immutable)
    - Logs deletion via audit service
    """
    if request.confirmation != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "CONFIRMATION_REQUIRED",
                "message": "Account deletion requires typing 'DELETE' as confirmation",
            },
        )

    user_id = current_user.id
    public_id = current_user.public_id

    try:
        # 1. Anonymize user data
        current_user.email = f"deleted_user_{user_id}@deleted.local"
        current_user.phone = "+00000000000"
        current_user.display_name = "Deleted User"
        current_user.is_active = False

        # 2. Cascade deletes: refresh_tokens, favorite_merchants, user_consents
        db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete()
        db.query(FavoriteMerchant).filter(FavoriteMerchant.user_id == user_id).delete()
        db.query(UserConsent).filter(UserConsent.user_id == user_id).delete()

        # 3. Delete vehicle accounts and tokens (cascade via relationship)
        vehicle_accounts = db.query(VehicleAccount).filter(VehicleAccount.user_id == user_id).all()
        for vehicle_account in vehicle_accounts:
            # Delete tokens first (they reference vehicle_account)
            db.query(VehicleToken).filter(
                VehicleToken.vehicle_account_id == vehicle_account.id
            ).delete()
            db.delete(vehicle_account)

        # 4. Anonymize exclusive_sessions (set driver_id to -1 for deleted user marker)
        db.query(ExclusiveSession).filter(ExclusiveSession.driver_id == user_id).update(
            {"driver_id": -1}, synchronize_session=False
        )

        # 5. Anonymize nova_transactions (keep immutable, but anonymize driver_user_id references)
        db.query(NovaTransaction).filter(NovaTransaction.driver_user_id == user_id).update(
            {"driver_user_id": -1}, synchronize_session=False
        )

        # 6. Anonymize verified_visits if they exist
        try:
            from app.models.verified_visit import VerifiedVisit

            db.query(VerifiedVisit).filter(VerifiedVisit.driver_id == user_id).update(
                {"driver_id": -1}, synchronize_session=False
            )
        except Exception:
            pass  # Table might not exist in all environments

        # 7. Log deletion via audit service
        log_admin_action(
            db=db,
            actor_id=user_id,  # User is deleting their own account
            action="account_deleted",
            target_type="user",
            target_id=str(user_id),
            before_json={"public_id": str(public_id), "email": "anonymized"},
            after_json={"status": "deleted", "anonymized": True},
            metadata={"deleted_at": datetime.now(timezone.utc).isoformat()},
        )

        db.commit()

        logger.info(f"Account deleted and anonymized for user {user_id} (public_id: {public_id})")

        return DeleteResponse(ok=True)

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete account for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account. Please contact support.",
        )


# ─── Admin: Transfer Sessions Between Users ──────────────────────


class TransferRequest(BaseModel):
    from_user_id: int
    to_phone: Optional[str] = None
    to_user_id: Optional[int] = None
    transfer_tesla: bool = True
    transfer_wallet: bool = True


@router.post("/admin/transfer-sessions")
def transfer_sessions(
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Transfer charging sessions (and optionally Tesla connection, wallet) from one user to another.
    Only the target user can pull sessions into their own account."""
    # Resolve target user first to check authorization
    if body.to_user_id and body.to_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Can only transfer to your own account")
    if body.to_phone:
        phone = body.to_phone if body.to_phone.startswith("+") else "+1" + body.to_phone
        if phone != current_user.phone:
            raise HTTPException(status_code=403, detail="Can only transfer to your own account")
    # Find source user
    src = db.query(User).filter(User.id == body.from_user_id).first()
    if not src:
        raise HTTPException(status_code=404, detail=f"Source user {body.from_user_id} not found")

    # Find target user
    if body.to_user_id:
        tgt = db.query(User).filter(User.id == body.to_user_id).first()
    elif body.to_phone:
        phone = body.to_phone
        if not phone.startswith("+"):
            phone = "+1" + phone
        tgt = db.query(User).filter(User.phone == phone).first()
    else:
        raise HTTPException(status_code=400, detail="Must specify to_user_id or to_phone")

    if not tgt:
        raise HTTPException(status_code=404, detail="Target user not found")

    if src.id == tgt.id:
        raise HTTPException(status_code=400, detail="Source and target are the same user")

    result = {
        "from_user": {
            "id": src.id,
            "phone": src.phone,
            "email": src.email,
            "provider": src.auth_provider,
        },
        "to_user": {
            "id": tgt.id,
            "phone": tgt.phone,
            "email": tgt.email,
            "provider": tgt.auth_provider,
        },
        "transferred": {},
    }

    # Transfer sessions
    sessions_updated = (
        db.query(SessionEvent)
        .filter(SessionEvent.driver_user_id == src.id)
        .update({"driver_user_id": tgt.id, "user_id": tgt.id}, synchronize_session=False)
    )
    result["transferred"]["sessions"] = sessions_updated

    # Transfer grants
    grants_updated = (
        db.query(IncentiveGrant)
        .filter(IncentiveGrant.driver_user_id == src.id)
        .update({"driver_user_id": tgt.id}, synchronize_session=False)
    )
    result["transferred"]["grants"] = grants_updated

    # Transfer Tesla connection
    if body.transfer_tesla:
        existing_tesla = db.query(TeslaConnection).filter(TeslaConnection.user_id == tgt.id).first()
        if not existing_tesla:
            tesla_updated = (
                db.query(TeslaConnection)
                .filter(TeslaConnection.user_id == src.id)
                .update({"user_id": tgt.id}, synchronize_session=False)
            )
            result["transferred"]["tesla_connection"] = tesla_updated
        else:
            result["transferred"]["tesla_connection"] = "target_already_has_one"

    # Transfer wallet balance
    if body.transfer_wallet:
        src_wallet = db.query(DriverWallet).filter(DriverWallet.user_id == src.id).first()
        if src_wallet and (src_wallet.balance_cents or 0) > 0:
            tgt_wallet = db.query(DriverWallet).filter(DriverWallet.user_id == tgt.id).first()
            if tgt_wallet:
                tgt_wallet.balance_cents = (tgt_wallet.balance_cents or 0) + (
                    src_wallet.balance_cents or 0
                )
                tgt_wallet.nova_balance = (tgt_wallet.nova_balance or 0) + (
                    src_wallet.nova_balance or 0
                )
                src_wallet.balance_cents = 0
                src_wallet.nova_balance = 0
                result["transferred"]["wallet"] = "merged"
            else:
                result["transferred"]["wallet"] = "no_target_wallet"
        else:
            result["transferred"]["wallet"] = "no_source_balance"

    # Transfer device tokens
    devices_updated = (
        db.query(DeviceToken)
        .filter(DeviceToken.user_id == src.id)
        .update({"user_id": tgt.id}, synchronize_session=False)
    )
    result["transferred"]["device_tokens"] = devices_updated

    db.commit()

    logger.info(
        "Admin %s transferred data from user %s to user %s: %s",
        current_user.id,
        src.id,
        tgt.id,
        result["transferred"],
    )

    return result
