"""
Pydantic schemas for demo mode.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class GridState(str, Enum):
    PEAK = "peak"
    OFFPEAK = "offpeak"

class MerchantShift(str, Enum):
    A_DOMINATES = "A_dominates"
    BALANCED = "balanced"

class RepProfile(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class City(str, Enum):
    AUSTIN = "austin"
    SAN_FRANCISCO = "san_francisco"
    NEW_YORK = "new_york"

class DemoScenarioRequest(BaseModel):
    key: Literal["grid_state", "merchant_shift", "rep_profile", "city"]
    value: str

class DemoScenarioResponse(BaseModel):
    ok: bool
    state: dict

class DemoStateResponse(BaseModel):
    state: dict

class DemoSeedResponse(BaseModel):
    seeded: bool
    skipped: bool = False
    message: str = None
    users: int
    merchants: int
    utilities: int
    counts: dict
    timing: dict

class DemoTourStep(BaseModel):
    name: str
    status: str
    ms: int
    error: str = None

class DemoTourResponse(BaseModel):
    steps: list[DemoTourStep]
    artifacts: dict
    artifact_dir: str = None
