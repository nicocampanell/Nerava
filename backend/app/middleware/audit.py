"""
Audit logging middleware for compliance and security
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger("audit")

class AuditMiddleware(BaseHTTPMiddleware):
    """Audit logging middleware for compliance"""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.sensitive_paths = {
            "/v1/wallet/",
            "/v1/users/",
            "/v1/energyhub/events/charge-stop",
        }
        self.excluded_paths = {
            "/healthz",
            "/readyz",
            "/metrics",
        }
    
    async def dispatch(self, request: Request, call_next):
        # Skip audit for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)
        
        # Extract request information
        audit_data = self._extract_audit_data(request)
        
        # Process request
        response = await call_next(request)
        
        # Add response information
        audit_data.update({
            "response_status": response.status_code,
            "response_headers": dict(response.headers),
            "timestamp_end": datetime.utcnow().isoformat(),
        })
        
        # Log audit event
        self._log_audit_event(audit_data)
        
        return response
    
    def _extract_audit_data(self, request: Request) -> Dict[str, Any]:
        """Extract audit-relevant data from request"""
        user_id = getattr(request.state, 'user_id', None) if hasattr(request.state, 'user_id') else None
        user_role = getattr(request.state, 'user_role', None) if hasattr(request.state, 'user_role') else None
        
        audit_data = {
            "timestamp_start": datetime.utcnow().isoformat(),
            "request_id": request.headers.get("X-Request-ID", "unknown"),
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("User-Agent", "unknown"),
            "user_id": user_id,
            "user_role": user_role.value if user_role else None,
            "region": getattr(request.state, 'region', None) if hasattr(request.state, 'region') else None,
        }
        
        # Add request body for sensitive endpoints (sanitized)
        if any(path in request.url.path for path in self.sensitive_paths):
            try:
                body = request._body if hasattr(request, '_body') else None
                if body:
                    # Sanitize sensitive data
                    sanitized_body = self._sanitize_request_body(body)
                    audit_data["request_body"] = sanitized_body
            except Exception as e:
                logger.warning(f"Failed to extract request body for audit: {e}")
        
        return audit_data
    
    def _sanitize_request_body(self, body: bytes) -> Dict[str, Any]:
        """Sanitize request body to remove sensitive information"""
        try:
            body_str = body.decode('utf-8')
            body_data = json.loads(body_str)
            
            # Remove or mask sensitive fields
            sensitive_fields = ['password', 'token', 'secret', 'key', 'ssn', 'credit_card']
            
            def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
                if isinstance(data, dict):
                    sanitized = {}
                    for key, value in data.items():
                        if any(field in key.lower() for field in sensitive_fields):
                            sanitized[key] = "[REDACTED]"
                        elif isinstance(value, dict):
                            sanitized[key] = sanitize_dict(value)
                        elif isinstance(value, list):
                            sanitized[key] = [sanitize_dict(item) if isinstance(item, dict) else item for item in value]
                        else:
                            sanitized[key] = value
                    return sanitized
                return data
            
            return sanitize_dict(body_data)
            
        except Exception as e:
            logger.warning(f"Failed to sanitize request body: {e}")
            return {"error": "Failed to parse request body"}
    
    def _log_audit_event(self, audit_data: Dict[str, Any]):
        """Log audit event"""
        try:
            # Create structured audit log entry
            audit_entry = {
                "event_type": "api_request",
                "service": "nerava-api",
                "version": "0.9.0",
                **audit_data
            }
            
            # Log as JSON for structured logging
            logger.info(json.dumps(audit_entry))
            
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")

def log_security_event(event_type: str, user_id: Optional[str], details: Dict[str, Any]):
    """Log security-related events"""
    try:
        security_event = {
            "event_type": f"security_{event_type}",
            "service": "nerava-api",
            "version": "0.9.0",
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "details": details,
        }
        
        logger.warning(json.dumps(security_event))
        
    except Exception as e:
        logger.error(f"Failed to log security event: {e}")

def log_data_access(user_id: str, resource_type: str, resource_id: str, action: str):
    """Log data access events for compliance"""
    try:
        access_event = {
            "event_type": "data_access",
            "service": "nerava-api",
            "version": "0.9.0",
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
        }
        
        logger.info(json.dumps(access_event))
        
    except Exception as e:
        logger.error(f"Failed to log data access event: {e}")
