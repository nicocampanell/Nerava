"""
Structured logging utility for reward flows
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional


def get_logger(name: str) -> logging.Logger:
    """Get a logger with JSON-like structured output"""
    logger = logging.getLogger(name)
    return logger


def log_reward_event(
    logger: logging.Logger,
    step: str,
    session_id: str,
    user_id: int,
    ok: bool,
    extra: Optional[Dict[str, Any]] = None
):
    """
    Log a reward event with structured format:
    {"at":"reward","step":"...","sid":"...","uid":...,"ok":true/false,"extra":{...}}
    """
    log_data = {
        "at": "reward",
        "step": step,
        "sid": session_id,
        "uid": user_id,
        "ok": ok,
        "ts": datetime.utcnow().isoformat()
    }
    if extra:
        log_data["extra"] = extra
    
    # Format as JSON-like string for readability
    log_msg = json.dumps(log_data, separators=(',', ':'))
    
    if ok:
        logger.info(log_msg)
    else:
        logger.warning(log_msg)

