"""
Domain events for the Nerava application
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class DomainEvent:
    """Base class for domain events"""
    event_id: str = ""
    event_type: str = ""
    timestamp: datetime = None
    aggregate_id: str = ""
    version: int = 1
    
    def __post_init__(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.utcnow()

@dataclass
class ChargeStartedEvent(DomainEvent):
    """Event raised when a charging session starts"""
    session_id: str = ""
    user_id: str = ""
    hub_id: str = ""
    started_at: datetime = None
    window_id: Optional[str] = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "charge_started"
        self.aggregate_id = self.session_id

@dataclass
class ChargeStoppedEvent(DomainEvent):
    """Event raised when a charging session stops"""
    session_id: str = ""
    user_id: str = ""
    hub_id: str = ""
    stopped_at: datetime = None
    kwh_consumed: float = 0.0
    window_id: Optional[str] = None
    grid_reward_usd: float = 0.0
    merchant_reward_usd: float = 0.0
    total_reward_usd: float = 0.0
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "charge_stopped"
        self.aggregate_id = self.session_id

@dataclass
class WalletCreditedEvent(DomainEvent):
    """Event raised when a wallet is credited"""
    user_id: str = ""
    amount_cents: int = 0
    session_id: str = ""
    new_balance_cents: int = 0
    credited_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "wallet_credited"
        self.aggregate_id = self.user_id

@dataclass
class IncentiveWindowActivatedEvent(DomainEvent):
    """Event raised when an incentive window becomes active"""
    window_id: str = ""
    window_label: str = ""
    price_per_kwh: float = 0.0
    multiplier: float = 1.0
    activated_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "incentive_window_activated"
        self.aggregate_id = self.window_id

@dataclass
class IncentiveWindowDeactivatedEvent(DomainEvent):
    """Event raised when an incentive window becomes inactive"""
    window_id: str = ""
    window_label: str = ""
    deactivated_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "incentive_window_deactivated"
        self.aggregate_id = self.window_id

@dataclass
class DriverSignedUpEvent(DomainEvent):
    """Event raised when a driver signs up"""
    user_id: str = ""
    email: str = ""
    auth_provider: str = ""
    created_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "driver_signed_up"
        self.aggregate_id = self.user_id
        if not self.created_at:
            self.created_at = datetime.utcnow()

@dataclass
class WalletPassInstalledEvent(DomainEvent):
    """Event raised when a wallet pass is installed (Apple or Google)"""
    user_id: str = ""
    pass_type: str = ""  # "apple" or "google"
    installed_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "wallet_pass_installed"
        self.aggregate_id = self.user_id
        if not self.installed_at:
            self.installed_at = datetime.utcnow()

@dataclass
class NovaEarnedEvent(DomainEvent):
    """Event raised when Nova is earned"""
    user_id: str = ""
    amount_cents: int = 0
    session_id: Optional[str] = None
    new_balance_cents: int = 0
    earned_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "nova_earned"
        self.aggregate_id = self.user_id
        if not self.earned_at:
            self.earned_at = datetime.utcnow()

@dataclass
class NovaRedeemedEvent(DomainEvent):
    """Event raised when Nova is redeemed"""
    user_id: str = ""
    amount_cents: int = 0
    merchant_id: str = ""
    redemption_id: str = ""
    new_balance_cents: int = 0
    redeemed_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "nova_redeemed"
        self.aggregate_id = self.user_id
        if not self.redeemed_at:
            self.redeemed_at = datetime.utcnow()

@dataclass
class FirstRedemptionCompletedEvent(DomainEvent):
    """Event raised when a driver completes their first redemption"""
    user_id: str = ""
    redemption_id: str = ""
    merchant_id: str = ""
    amount_cents: int = 0
    completed_at: datetime = None
    
    def __post_init__(self):
        super().__post_init__()
        self.event_type = "first_redemption_completed"
        self.aggregate_id = self.user_id
        if not self.completed_at:
            self.completed_at = datetime.utcnow()

# Event type registry for serialization
EVENT_TYPES = {
    "charge_started": ChargeStartedEvent,
    "charge_stopped": ChargeStoppedEvent,
    "wallet_credited": WalletCreditedEvent,
    "incentive_window_activated": IncentiveWindowActivatedEvent,
    "incentive_window_deactivated": IncentiveWindowDeactivatedEvent,
    "driver_signed_up": DriverSignedUpEvent,
    "wallet_pass_installed": WalletPassInstalledEvent,
    "nova_earned": NovaEarnedEvent,
    "nova_redeemed": NovaRedeemedEvent,
    "first_redemption_completed": FirstRedemptionCompletedEvent,
}
