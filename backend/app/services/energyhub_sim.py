from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Dict, List, Optional


@dataclass
class EnergyWindow:
    id: str
    label: str
    start: time       # UTC
    end: time         # UTC
    price_per_kwh: float
    multiplier: float

    def is_active(self, now: datetime) -> bool:
        lt = now.time()
        return self.start <= lt < self.end

@dataclass
class ChargeSession:
    id: str
    user_id: str
    hub_id: str
    started_at: datetime
    stopped_at: Optional[datetime] = None
    kwh: Optional[float] = None
    window_id: Optional[str] = None

class EnergyHubSimulator:
    def __init__(self) -> None:
        self.windows: List[EnergyWindow] = [
            EnergyWindow(
                id="solar_surplus",
                label="Solar Surplus",
                start=time(10, 0),
                end=time(14, 0),
                price_per_kwh=0.03,
                multiplier=1.0,
            ),
            EnergyWindow(
                id="green_hour",
                label="Green Hour",
                start=time(14, 0),
                end=time(17, 0),
                price_per_kwh=0.08,
                multiplier=2.0,
            ),
        ]
        self.sessions: Dict[str, ChargeSession] = {}
        self.merchant_cofund_usd = 0.75

    def reset(self) -> None:
        self.sessions.clear()

    def _now(self, override_dt: Optional[datetime]) -> datetime:
        return override_dt or datetime.now(timezone.utc)

    def active_window(self, now: datetime) -> Optional[EnergyWindow]:
        for w in self.windows:
            if w.is_active(now):
                return w
        return None

    def list_windows(self, override_dt: Optional[datetime]) -> List[dict]:
        now = self._now(override_dt)
        return [{
            "id": w.id,
            "label": w.label,
            "start_utc": w.start.strftime("%H:%M"),
            "end_utc": w.end.strftime("%H:%M"),
            "price_per_kwh": w.price_per_kwh,
            "multiplier": w.multiplier,
            "active_now": w.is_active(now),
        } for w in self.windows]

    def start_session(self, user_id: str, hub_id: str, override_dt: Optional[datetime]) -> dict:
        now = self._now(override_dt)
        sid = str(uuid.uuid4())
        w = self.active_window(now)
        self.sessions[sid] = ChargeSession(
            id=sid, user_id=user_id, hub_id=hub_id, started_at=now, window_id=w.id if w else None
        )
        return {
            "session_id": sid,
            "active_window": None if not w else {
                "id": w.id,
                "label": w.label,
                "price_per_kwh": w.price_per_kwh,
                "multiplier": w.multiplier,
                "active_now": True,
            }
        }

    def stop_session(self, session_id: str, kwh: float, override_dt: Optional[datetime]) -> dict:
        now = self._now(override_dt)
        if session_id not in self.sessions:
            raise KeyError("session_not_found")
        s = self.sessions[session_id]
        s.stopped_at = now
        s.kwh = kwh

        w = None
        if s.window_id:
            w = next((x for x in self.windows if x.id == s.window_id), None)

        grid_reward = kwh * (w.price_per_kwh if w else 0.0) * (w.multiplier if w else 1.0)
        merchant_reward = self.merchant_cofund_usd
        total = grid_reward + merchant_reward

        return {
            "session_id": s.id,
            "user_id": s.user_id,
            "hub_id": s.hub_id,
            "kwh": kwh,
            "window_applied": s.window_id,
            "grid_reward_usd": round(grid_reward, 2),
            "merchant_reward_usd": round(merchant_reward, 2),
            "total_reward_usd": round(total, 2),
            "message": (
                f"Great timing! You charged during {w.label} and earned ${round(total,2)} "
                f"(${w.price_per_kwh}/kWh × {w.multiplier}x + ${merchant_reward} co-fund)."
                if w else
                f"You earned ${round(total,2)} with a ${merchant_reward} co-fund."
            )
        }

# Singleton
sim = EnergyHubSimulator()