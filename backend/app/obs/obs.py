"""
Observability core utilities for structured logging and metrics.
"""
import logging
import uuid
from collections import defaultdict
from threading import Lock
from typing import Any, Dict

from fastapi import Request

logger = logging.getLogger(__name__)

# In-memory metrics storage
_metrics_lock = Lock()
_api_requests_total = defaultdict(int)
_api_request_ms = defaultdict(list)

def get_trace_id(request: Request) -> str:
    """Get trace ID from request header or generate new one."""
    trace_id = request.headers.get("X-Trace-Id")
    if not trace_id:
        trace_id = str(uuid.uuid4())
    return trace_id

def log_info(data: Dict[str, Any]) -> None:
    """Log info with structured data."""
    logger.info("api_info", extra=data)

def log_warn(data: Dict[str, Any]) -> None:
    """Log warning with structured data."""
    logger.warning("api_warning", extra=data)

def log_error(data: Dict[str, Any]) -> None:
    """Log error with structured data."""
    logger.error("api_error", extra=data)

def record_request(route: str, duration_ms: float) -> None:
    """Record request metrics."""
    with _metrics_lock:
        _api_requests_total[route] += 1
        _api_request_ms[route].append(duration_ms)
        
        # Keep only last 1000 measurements per route
        if len(_api_request_ms[route]) > 1000:
            _api_request_ms[route] = _api_request_ms[route][-1000:]

def get_metrics() -> Dict[str, Any]:
    """Get current metrics snapshot."""
    with _metrics_lock:
        metrics = {
            "api_requests_total": dict(_api_requests_total),
            "api_request_ms": {
                route: {
                    "count": len(times),
                    "avg_ms": sum(times) / len(times) if times else 0,
                    "p95_ms": sorted(times)[int(len(times) * 0.95)] if times else 0
                }
                for route, times in _api_request_ms.items()
            }
        }
    return metrics

def clear_metrics() -> None:
    """Clear all metrics (useful for testing)."""
    with _metrics_lock:
        _api_requests_total.clear()
        _api_request_ms.clear()
