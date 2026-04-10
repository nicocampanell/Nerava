from __future__ import annotations

import uuid


def uid() -> str:
    return str(uuid.uuid4())


