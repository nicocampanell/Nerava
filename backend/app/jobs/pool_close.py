from typing import Optional

from app.services.pool2 import run_daily_close


def run(date_yyyymmdd: Optional[str] = None):
    return run_daily_close(date_yyyymmdd)


