from __future__ import annotations

import json
from typing import Any, Dict


def emit(topic: str, payload: Dict[str, Any]) -> None:
    # Stub: structured log
    print(json.dumps({"topic": topic, "payload": payload}))


