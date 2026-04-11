"""
Reward service for verify bonuses and other rewards
"""
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_engine
from app.utils.dbjson import as_db_json, get_table_columns
from app.utils.log import get_logger, log_reward_event

logger = get_logger(__name__)


def award_verify_bonus(
    db: Session,
    *,
    user_id: int,
    session_id: str,
    amount: int,
    now: datetime
) -> Dict[str, Any]:
    """
    Award a verify bonus reward (90/10 split).
    
    Atomic transaction; idempotent by checking reward_events for session_id.
    
    Returns:
        {
            "awarded": bool,
            "user_delta": int,
            "pool_delta": int,
            "reason": str (optional)
        }
    """
    log_reward_event(logger, "start", session_id, user_id, True, {"amount": amount})

    # Get database engine (lazy initialization)
    engine = get_engine()

    # Start transaction (SQLAlchemy sessions are transactional by default)
    try:
        # 1. Idempotency check
        log_reward_event(logger, "idempotency_check", session_id, user_id, True)
        
        # Check if reward already exists (adapt to actual schema)
        is_sqlite = settings.database_url.startswith("sqlite")
        if is_sqlite:
            # SQLite: use LIKE for JSON stored as TEXT
            existing = db.execute(text("""
                SELECT id FROM reward_events
                WHERE source = 'verify_bonus'
                AND user_id = :user_id
                AND meta LIKE :pattern
                LIMIT 1
            """), {
                "user_id": str(user_id),
                "pattern": f'%{session_id}%'
            }).first()
        else:
            # Postgres: can use JSON operators
            existing = db.execute(text("""
                SELECT id FROM reward_events
                WHERE source = 'verify_bonus'
                AND user_id = :user_id
                AND meta->>'session_id' = :session_id
                LIMIT 1
            """), {
                "user_id": str(user_id),
                "session_id": session_id
            }).first()
        
        if existing:
            log_reward_event(logger, "idempotency_check", session_id, user_id, False, {"reason": "already_exists"})
            return {
                "awarded": False,
                "user_delta": 0,
                "pool_delta": 0,
                "reason": "already_rewarded"
            }
        
        # 2. Compute split using configured pool pct
        pool_pct = int(getattr(settings, 'verify_pool_pct', 10))
        pool_cut = int((amount * pool_pct) // 100)
        user_cut = amount - pool_cut
        
        log_reward_event(logger, "compute_split", session_id, user_id, True, {
            "user_cut": user_cut,
            "pool_cut": pool_cut
        })
        
        # 3. Detect reward_events table schema
        columns = get_table_columns(engine, 'reward_events')
        
        # Build meta JSON
        meta_dict = {
            "session_id": session_id,
            "type": "verify_bonus",
            "amount": amount
        }
        meta_json = as_db_json(meta_dict, engine)
        
        # 4. Insert reward_events (adapt to actual schema)
        if 'source' in columns and 'gross_cents' in columns:
            # Existing schema: source, gross_cents, net_cents, community_cents, meta, created_at
            # Include created_at if column exists and is NOT NULL
            if 'created_at' in columns:
                db.execute(text("""
                    INSERT INTO reward_events (
                        user_id, source, gross_cents, net_cents, community_cents, meta, created_at
                    ) VALUES (
                        :user_id, 'verify_bonus', :gross_cents, :net_cents, :community_cents, :meta, :created_at
                    )
                """), {
                    "user_id": str(user_id),
                    "gross_cents": amount,
                    "net_cents": user_cut,
                    "community_cents": pool_cut,
                    "meta": meta_json,
                    "created_at": now
                })
            else:
                db.execute(text("""
                    INSERT INTO reward_events (
                        user_id, source, gross_cents, net_cents, community_cents, meta
                    ) VALUES (
                        :user_id, 'verify_bonus', :gross_cents, :net_cents, :community_cents, :meta
                    )
                """), {
                    "user_id": str(user_id),
                    "gross_cents": amount,
                    "net_cents": user_cut,
                    "community_cents": pool_cut,
                    "meta": meta_json
                })
        else:
            # Fallback: try migration schema if it exists
            raise ValueError(f"Unknown reward_events schema. Columns: {list(columns.keys())}")
        
        log_reward_event(logger, "reward_event_inserted", session_id, user_id, True)
        
        # 5. Insert wallet_ledger
        # Calculate running balance
        balance_result = db.execute(text("""
            SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
            WHERE user_id = :user_id
        """), {"user_id": user_id}).scalar()
        new_balance = int(balance_result) if balance_result else 0
        new_balance += user_cut
        
        wallet_metadata = as_db_json({
            "session_id": session_id,
            "type": "verify_bonus"
        }, engine)
        
        result = db.execute(text("""
            INSERT INTO wallet_ledger (
                user_id, amount_cents, transaction_type,
                reference_id, reference_type, balance_cents, metadata
            ) VALUES (
                :user_id, :amount_cents, 'credit',
                :reference_id, 'session', :balance_cents, :metadata
            )
        """), {
            "user_id": user_id,
            "amount_cents": user_cut,
            "reference_id": session_id,
            "balance_cents": new_balance,
            "metadata": wallet_metadata
        })
        
        log_reward_event(logger, "wallet_ledger_inserted", session_id, user_id, True, {
            "delta": user_cut,
            "new_balance": new_balance
        })
        
        # 6a. Record pool inflow in pool_ledger2 if available
        try:
            db.execute(text("""
                INSERT INTO pool_ledger2 (city, source, amount_cents, related_event_id, created_at)
                VALUES (:city, 'verify_pool', :amt, NULL, :created_at)
            """), {
                "city": getattr(settings, 'city_fallback', 'Austin'),
                "amt": pool_cut,
                "created_at": now
            })
            logger.info({"at": "verify", "step": "pool_inflow", "source": "verify_pool", "amount_cents": pool_cut})
        except Exception as e:
            logger.info({"at": "verify", "step": "pool_inflow", "err": str(e)})

        # 6b. Upsert community_pool (legacy fallback)
        month_key = int(now.strftime("%Y%m"))
        pool_name = f"verify_{month_key}"
        
        # Try UPDATE first
        update_result = db.execute(text("""
            UPDATE community_pool
            SET total_cents = total_cents + :pool_cut,
                updated_at = :updated_at
            WHERE pool_name = :pool_name
        """), {
            "pool_name": pool_name,
            "pool_cut": pool_cut,
            "updated_at": now
        })
        
        if update_result.rowcount == 0:
            # INSERT if pool doesn't exist
            db.execute(text("""
                INSERT INTO community_pool (
                    pool_name, total_cents, allocated_cents, status
                ) VALUES (
                    :pool_name, :total_cents, 0, 'active'
                )
            """), {
                "pool_name": pool_name,
                "total_cents": pool_cut
            })
            log_reward_event(logger, "pool_inserted", session_id, user_id, True, {
                "pool_name": pool_name,
                "pool_cut": pool_cut
            })
        else:
            log_reward_event(logger, "pool_updated", session_id, user_id, True, {
                "pool_name": pool_name,
                "pool_cut": pool_cut
            })
        
        # 7. Commit transaction
        db.commit()
        log_reward_event(logger, "commit", session_id, user_id, True, {
            "user_delta": user_cut,
            "pool_delta": pool_cut
        })
        
        return {
            "awarded": True,
            "user_delta": user_cut,
            "pool_delta": pool_cut
        }
        
    except Exception as e:
        db.rollback()
        log_reward_event(logger, "commit", session_id, user_id, False, {
            "error": str(e),
            "error_type": type(e).__name__
        })
        # Re-raise for caller to handle
        raise


def award_purchase_reward(
    db: Session,
    *,
    user_id: int,
    session_id: str,
    payment_id: Any,  # Can be int (SQLite) or str (UUID)
    amount: int,
    now: datetime
) -> Dict[str, Any]:
    """
    Award a purchase reward (90/10 split).
    
    Atomic transaction; idempotent by checking reward_events for payment_id.
    
    Returns:
        {
            "awarded": bool,
            "user_delta": int,
            "pool_delta": int,
            "reason": str (optional)
        }
    """
    payment_id_str = str(payment_id)
    log_reward_event(logger, "start", payment_id_str, user_id, True, {
        "amount": amount,
        "type": "purchase",
        "session_id": session_id
    })
    
    try:
        # 1. Idempotency check: look for existing purchase reward for this payment_id
        existing_reward_query = text("""
            SELECT id FROM reward_events
            WHERE user_id = :user_id
            AND source = 'purchase'
            AND meta LIKE :pattern
            LIMIT 1
        """)
        
        existing_reward = db.execute(existing_reward_query, {
            "user_id": str(user_id),
            "pattern": f'%{payment_id_str}%'
        }).first()
        
        if existing_reward:
            log_reward_event(logger, "idempotency_check", payment_id_str, user_id, False, {
                "reason": "already_rewarded"
            })
            return {"awarded": False, "user_delta": 0, "pool_delta": 0, "reason": "already_rewarded"}
        
        log_reward_event(logger, "idempotency_check", payment_id_str, user_id, True)
        
        # 2. Compute user and pool cuts (90/10)
        user_cut = amount * 90 // 100
        pool_cut = amount - user_cut
        log_reward_event(logger, "compute_split", payment_id_str, user_id, True, {
            "user_cut": user_cut,
            "pool_cut": pool_cut
        })
        
        # 3. Insert reward_events
        engine = db.bind
        meta_dict = {
            "payment_id": payment_id_str,
            "session_id": session_id,
            "type": "purchase",
            "amount": amount
        }
        meta_json = as_db_json(meta_dict, engine)
        
        columns = get_table_columns(db, "reward_events")
        insert_columns = ["user_id", "source", "gross_cents", "net_cents", "community_cents", "meta"]
        insert_values = {
            "user_id": str(user_id),
            "source": "purchase",
            "gross_cents": amount,
            "net_cents": user_cut,
            "community_cents": pool_cut,
            "meta": meta_json
        }
        
        if 'created_at' in columns:
            insert_columns.append("created_at")
            insert_values["created_at"] = now
        
        insert_stmt = text(f"""
            INSERT INTO reward_events ({', '.join(insert_columns)})
            VALUES ({', '.join([f':{col}' for col in insert_columns])})
        """)
        
        db.execute(insert_stmt, insert_values)
        log_reward_event(logger, "reward_event_inserted", payment_id_str, user_id, True)
        
        # 4. Insert wallet_ledger
        balance_result = db.execute(text("""
            SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
            WHERE user_id = :user_id
        """), {"user_id": user_id}).scalar()
        new_balance = int(balance_result) + user_cut
        
        db.execute(text("""
            INSERT INTO wallet_ledger (
                user_id, amount_cents, transaction_type,
                reference_id, reference_type, balance_cents, metadata, created_at
            ) VALUES (
                :user_id, :amount_cents, 'credit',
                :reference_id, 'purchase', :balance_cents, :metadata, :created_at
            )
        """), {
            "user_id": user_id,
            "amount_cents": user_cut,
            "reference_id": payment_id_str,
            "balance_cents": new_balance,
            "metadata": as_db_json({"payment_id": payment_id_str, "session_id": session_id, "reward_type": "purchase"}, engine),
            "created_at": now
        })
        log_reward_event(logger, "wallet_ledger_inserted", payment_id_str, user_id, True)
        
        # 5. Upsert community_pool
        month_key = int(now.strftime("%Y%m"))
        pool_name = f"purchase_{month_key}"
        
        update_result = db.execute(text("""
            UPDATE community_pool
            SET total_cents = total_cents + :pool_cut,
                updated_at = :updated_at
            WHERE pool_name = :pool
        """), {
            "pool": pool_name,
            "pool_cut": pool_cut,
            "updated_at": now
        })
        
        if update_result.rowcount == 0:
            db.execute(text("""
                INSERT INTO community_pool (pool_name, total_cents, created_at, updated_at)
                VALUES (:pool, :pool_cut, :created_at, :updated_at)
            """), {
                "pool_name": pool_name,
                "total_cents": pool_cut,
                "created_at": now,
                "updated_at": now
            })
            log_reward_event(logger, "pool_inserted", payment_id_str, user_id, True, {
                "pool_name": pool_name,
                "pool_cut": pool_cut
            })
        else:
            log_reward_event(logger, "pool_updated", payment_id_str, user_id, True, {
                "pool_name": pool_name,
                "pool_cut": pool_cut
            })
        
        # 6. Commit transaction
        db.commit()
        log_reward_event(logger, "commit", payment_id_str, user_id, True, {
            "user_delta": user_cut,
            "pool_delta": pool_cut
        })
        
        return {
            "awarded": True,
            "user_delta": user_cut,
            "pool_delta": pool_cut
        }
        
    except Exception as e:
        db.rollback()
        log_reward_event(logger, "commit", payment_id_str, user_id, False, {
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise

