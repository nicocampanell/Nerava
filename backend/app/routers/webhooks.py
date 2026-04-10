from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.models_extra import IncentiveRule, UtilityEvent

router = APIRouter(prefix="/v1/webhooks", tags=["utility"])


@router.post("/utility/austin_energy/fake_event")
def fake_event(db: Session = Depends(get_db)):
    if settings.is_prod:
        raise HTTPException(status_code=403, detail="Not available in production")
    """
    Simulate a utility event and temporarily boost OFF_PEAK_BASE to the next 60 minutes.
    """
    now = datetime.utcnow()
    window = {
        "start": now.strftime("%H:%M"),
        "end": (now + timedelta(minutes=60)).strftime("%H:%M"),
    }

    db.add(
        UtilityEvent(
            provider="austin_energy",
            kind="DR_EVENT",
            window={
                "start_iso": now.isoformat() + "Z",
                "end_iso": (now + timedelta(minutes=60)).isoformat() + "Z",
            },
            payload={"demo": True},
        )
    )

    rule = db.query(IncentiveRule).filter(IncentiveRule.code == "OFF_PEAK_BASE").first()
    if rule:
        rule.params = {"cents": 50, "window": [window["start"], window["end"]]}
    else:
        db.add(
            IncentiveRule(
                code="OFF_PEAK_BASE",
                active=True,
                params={"cents": 50, "window": [window["start"], window["end"]]},
            )
        )

    db.commit()
    return {"ok": True, "off_peak_now_to_plus_60m": window}
