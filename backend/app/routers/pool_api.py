"""Pool API router."""

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/pool", tags=["pool"])
logger = get_logger(__name__)


def _has_table(db: Session, name: str) -> bool:
    try:
        res = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}).first()
        return bool(res)
    except Exception:
        return False


@router.get("/summary")
def pool_summary(
    city: Optional[str] = Query(None),
    range_param: str = Query("today", alias="range"),
    db: Session = Depends(get_db)
):
    """Get pool summary statistics, preferring pool_ledger2 if present."""

    tz = ZoneInfo("America/Chicago")
    now_local = datetime.now(tz)
    if range_param == "today":
        start_local = datetime.combine(now_local.date(), time(0, 0, 0), tzinfo=tz)
    elif range_param == "7d":
        start_local = now_local - timedelta(days=7)
    elif range_param == "30d":
        start_local = now_local - timedelta(days=30)
    else:
        start_local = now_local - timedelta(days=7)

    start_time = start_local.astimezone(timezone.utc)
    end_time = now_local.astimezone(timezone.utc)

    use_v2 = _has_table(db, "pool_ledger2")

    resp = {
        "city": city or "",
        "range": range_param,
        "balance_cents": 0,
        "inflows": {},
        "outflows": {},
        "impact": {"verified_sessions": 0, "avg_reward_cents": 0},
        "updated_at": end_time.isoformat(),
    }

    try:
        if use_v2:
            where = "created_at BETWEEN :t0 AND :t1"
            params = {"t0": start_time, "t1": end_time}
            if city:
                where += " AND city = :city"
                params["city"] = city

            rows = db.execute(text(f"SELECT source, amount_cents FROM pool_ledger2 WHERE {where}"), params).fetchall()
            total = 0
            inflows_map = {}
            outflows_map = {}
            for row in rows:
                amt = int(row.amount_cents or 0)
                total += amt
                if amt > 0:
                    inflows_map[row.source] = inflows_map.get(row.source, 0) + amt
                elif amt < 0:
                    outflows_map[row.source] = outflows_map.get(row.source, 0) + abs(amt)
            resp["balance_cents"] = total
            resp["inflows"] = inflows_map
            resp["outflows"] = outflows_map

            try:
                reward_rows = db.execute(text("""
                    SELECT net_cents
                    FROM reward_events
                    WHERE source='verify_bonus'
                      AND created_at BETWEEN :t0 AND :t1
                """), {"t0": start_time, "t1": end_time}).fetchall()
                ver_count = len(reward_rows)
                avg_net = int(round(sum(int(r.net_cents or 0) for r in reward_rows) / ver_count)) if ver_count else 0
                resp["impact"]["verified_sessions"] = ver_count
                resp["impact"]["avg_reward_cents"] = avg_net
            except Exception:
                resp["impact"]["verified_sessions"] = 0
                resp["impact"]["avg_reward_cents"] = 0
        else:
            month_key = int(end_time.strftime("%Y%m"))
            bal_row = db.execute(text("""
                SELECT COALESCE(SUM(total_cents - COALESCE(allocated_cents,0)),0)
                FROM community_pool
                WHERE pool_name LIKE :p
            """), {"p": f"%{month_key}%"}).scalar()
            resp["balance_cents"] = int(bal_row or 0)

            try:
                rewards = db.execute(text("""
                    SELECT net_cents
                    FROM reward_events
                    WHERE source='verify_bonus'
                      AND created_at BETWEEN :t0 AND :t1
                """), {"t0": start_time, "t1": end_time}).fetchall()
                ver_count = len(rewards)
                avg_net = int(round(sum(int(r.net_cents or 0) for r in rewards) / ver_count)) if ver_count else 0
                resp["impact"] = {"verified_sessions": ver_count, "avg_reward_cents": avg_net}
            except Exception:
                resp["impact"] = {"verified_sessions": 0, "avg_reward_cents": 0}

        logger.info({"at": "pool", "step": "summary", "city": city, "range": range_param, "balance": resp["balance_cents"], "ok": True})
        return resp
    except Exception as e:
        logger.info({"at": "pool", "step": "summary", "city": city, "range": range_param, "err": str(e)})
        return {"ok": False, "reason": "internal_error"}


@router.get("/ledger")
def pool_ledger(
    city: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Get pool ledger entries (paginated)."""
    where_clause = "1=1"
    params = {"limit": limit}

    if cursor:
        where_clause += " AND id > :cursor"
        params["cursor"] = cursor

    if city:
        where_clause += " AND city = :city"
        params["city"] = city

    result = db.execute(text(f"""
        SELECT * FROM pool_ledger
        WHERE {where_clause}
        ORDER BY id ASC
        LIMIT :limit
    """), params)

    entries = [dict(row._mapping) for row in result]

    next_cursor = None
    if len(entries) == limit:
        next_cursor = entries[-1]["id"]

    return {
        "entries": entries,
        "next_cursor": next_cursor
    }

