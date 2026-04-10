from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Dict

from sqlalchemy import text

from app.db import SessionLocal
from app.domains.schemas import AffiliateNotifyReq


def build_click(user_id: int, merchant_id: int, offer_id: str) -> Dict[str, Any]:
    # Minimal: return synthetic click with redirect template
    click_id = f"clk_{user_id}_{merchant_id}_{offer_id}_{int(datetime.utcnow().timestamp())}"
    redirect_url = f"https://example-aff.net/track?subId={click_id}&mid={merchant_id}&offer={offer_id}"
    return {"click_id": click_id, "redirect_url": redirect_url}


def ingest_conversion(payload: AffiliateNotifyReq) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        # Idempotency: if a txn exists with meta_json like network+click_id, skip
        meta_key = f"{payload.network}:{payload.click_id}"
        found = db.execute(
            text("SELECT id FROM transactions WHERE meta_json LIKE :meta LIMIT 1"),
            {"meta": f"%{meta_key}%"},
        ).first()
        if found:
            return {"status": "ok", "idempotent": True}

        db.execute(
            text(
                """
            INSERT INTO transactions (user_id, merchant_id, event_id, source, amount_cents, status, meta_json)
            VALUES (:user_id, :merchant_id, NULL, 'affiliate', :amount, 'captured', :meta)
            """
            ),
            {
                "user_id": payload.meta.get("user_id") if payload.meta else None,
                "merchant_id": payload.meta.get("merchant_id") if payload.meta else None,
                "amount": payload.amount_cents,
                "meta": {"network": payload.network, "click_id": payload.click_id}.__repr__(),
            },
        )

        # Create pending reward available next day 09:00 UTC (simplified)
        tomorrow_9 = datetime.utcnow().date() + timedelta(days=1)
        available_at = datetime.combine(tomorrow_9, time(9, 0))
        db.execute(
            text(
                """
            INSERT INTO rewards2 (user_id, type, amount_cents, earn_date, available_at, ref_txn_id, ref_event_id)
            VALUES (:user_id, 'cashback', :amount, :earn_date, :avail, NULL, NULL)
            """
            ),
            {
                "user_id": payload.meta.get("user_id") if payload.meta else None,
                "amount": int(payload.amount_cents * 0.1),  # 10% cashback demo
                "earn_date": datetime.utcnow().date(),
                "avail": available_at,
            },
        )

        # Optional pool inflow (1% of GMV demo) when green_hour_commit_pct present — simplified here
        city = payload.meta.get("city") if payload.meta else None
        if city:
            db.execute(
                text(
                    """
                INSERT INTO pool_ledger2 (city, source, amount_cents, related_event_id)
                VALUES (:city, 'user_purchase', :amt, NULL)
                """
                ),
                {"city": city, "amt": int(payload.amount_cents * 0.01)},
            )

        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


