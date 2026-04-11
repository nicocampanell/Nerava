from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..core.security import hash_password
from ..db import get_db
from ..models import User, UserPreferences

router = APIRouter(prefix="/v1/users", tags=["users"])

class RegisterPayload(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    password: Optional[str] = None  # auto-generated if missing

@router.post("/register")
def legacy_register(payload: RegisterPayload, db: Session = Depends(get_db)):
    """
    Compatibility for old demos/tests.
    Creates (or ensures) a user in the new auth tables and default preferences.
    Returns 200 with {"status":"exists"} if already registered.
    """
    pw = payload.password or "nerava-autogen-pass"

    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        user = User(email=payload.email, password_hash=hash_password(pw))
        db.add(user)
        db.flush()  # ensure user.id

        db.add(UserPreferences(user_id=user.id))
        db.commit()
        return {"email": user.email, "id": user.id, "status": "created"}

    # ensure prefs row exists
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if prefs is None:
        db.add(UserPreferences(user_id=user.id))
        db.commit()

    return {"email": user.email, "id": user.id, "status": "exists"}
