from __future__ import annotations

import json
from typing import Any, Dict


def send(user_id: int, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Stub: in real impl, persist and rate-limit
    return {"status": "queued", "user_id": user_id, "kind": kind, "payload": json.dumps(payload)}


