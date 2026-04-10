from typing import Optional

from fastapi import HTTPException, Request


def resolve_user_id(request: Request, body_user_id: Optional[int]) -> int:
    if body_user_id:
        return body_user_id
    header_val = request.headers.get("X-User-Id")
    if header_val and header_val.isdigit():
        return int(header_val)
    raise HTTPException(status_code=401, detail="user_id required")


