from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/pool/dump")
def debug_pool_dump(city: str = Query("Austin"), limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, source, amount_cents, city, created_at
        FROM pool_ledger2
        WHERE city = :city
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"city": city, "limit": limit}).mappings().all()
    return {"rows": [dict(r) for r in rows]}


