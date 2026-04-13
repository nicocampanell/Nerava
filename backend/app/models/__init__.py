"""
Models package - organized by domain
"""

# Re-export all models for backward compatibility
from .ad_impression import AdImpression
from .arrival_session import ArrivalSession
from .billing_event import BillingEvent
from .campaign import Campaign
from .car_pin import CarPin
from .charge_intent import ChargeIntent
from .claim_session import ClaimSession
from .device_token import DeviceToken
from .domain import (
    ApplePassRegistration,
    DomainChargingSession,
    DomainMerchant,
    DriverWallet,
    EnergyEvent,
    GoogleWalletLink,
    NovaTransaction,
    StripePayment,
    Zone,
)
from .driver_order import DriverOrder
from .driver_wallet import Payout, WalletLedger
from .email_otp_challenge import EmailOTPChallenge
from .exclusive_session import (
    ExclusiveSession,
    ExclusiveSessionStatus,
)
from .extra import (
    Challenge,
    CommunityPeriod,
    CreditLedger,
    DualZoneSession,
    FeatureFlag,
    Follow,
    FollowerShare,
    IncentiveRule,
    Participation,
    RewardEvent,
    UtilityEvent,
)
from .intent import (
    IntentSession,
    PerkUnlock,
)
from .loyalty import LoyaltyCard, LoyaltyProgress
from .merchant_account import (
    MerchantAccount,
    MerchantLocationClaim,
    MerchantPaymentMethod,
    MerchantPlacementRule,
)
from .merchant_cache import (
    MerchantCache,
)
from .merchant_notification_config import MerchantNotificationConfig
from .merchant_oauth_token import MerchantOAuthToken
from .merchant_pos_credentials import MerchantPOSCredentials
from .merchant_reward import (
    MerchantJoinRequest,
    ReceiptStatus,
    ReceiptSubmission,
    RewardClaim,
    RewardClaimStatus,
)
from .merchant_subscription import MerchantSubscription
from .notification_prefs import UserNotificationPrefs
from .otp_challenge import OTPChallenge
from .partner import Partner, PartnerAPIKey
from .queued_order import QueuedOrder, QueuedOrderStatus
from .refresh_token import RefreshToken
from .session_event import IncentiveGrant, SessionEvent
from .tesla_connection import EVVerificationCode, TeslaConnection
from .user import User, UserPreferences
from .user_consent import UserConsent
from .user_reputation import UserReputation
from .vehicle import (
    VehicleAccount,
    VehicleTelemetry,
    VehicleToken,
)
from .vehicle_onboarding import (
    VehicleOnboarding,
)
from .verified_visit import VerifiedVisit
from .virtual_key import VirtualKey
from .wallet_pass import (
    WalletPassActivation,
    WalletPassStateEnum,
)
from .wallet_pass_state import (
    WalletPassState,
)
from .while_you_charge import (
    AmenityVote,
    Charger,
    ChargerCluster,
    ChargerMerchant,
    FavoriteMerchant,
    Merchant,
    MerchantBalance,
    MerchantBalanceLedger,
    MerchantOfferCode,
    MerchantPerk,
)

__all__ = [
    # User models
    "User",
    "UserPreferences",
    "RefreshToken",
    "OTPChallenge",
    "EmailOTPChallenge",
    "UserNotificationPrefs",
    # Domain models
    "Zone",
    "EnergyEvent",
    "DomainMerchant",
    "DriverWallet",
    "NovaTransaction",
    "DomainChargingSession",
    "StripePayment",
    "ApplePassRegistration",
    "GoogleWalletLink",
    # Vehicle models
    "VehicleAccount",
    "VehicleToken",
    "VehicleTelemetry",
    # While You Charge models
    "Charger",
    "Merchant",
    "ChargerMerchant",
    "MerchantPerk",
    "MerchantBalance",
    "MerchantBalanceLedger",
    "MerchantOfferCode",
    "FavoriteMerchant",
    "ChargerCluster",
    "AmenityVote",
    # Intent models
    "IntentSession",
    "PerkUnlock",
    "ChargeIntent",
    "UserReputation",
    # Exclusive session models
    "ExclusiveSession",
    "ExclusiveSessionStatus",
    # Vehicle onboarding models
    "VehicleOnboarding",
    # Merchant cache models
    "MerchantCache",
    # Wallet pass state models
    "WalletPassState",
    "WalletPassActivation",
    "WalletPassStateEnum",
    # Merchant account models
    "MerchantAccount",
    "MerchantLocationClaim",
    "MerchantPlacementRule",
    "MerchantPaymentMethod",
    # Claim session models
    "ClaimSession",
    # Verified visit models
    "VerifiedVisit",
    # User consent models
    "UserConsent",
    # EV Arrival models
    "ArrivalSession",
    "CarPin",
    "MerchantNotificationConfig",
    "MerchantPOSCredentials",
    "BillingEvent",
    "QueuedOrder",
    "QueuedOrderStatus",
    # Virtual Key models
    "VirtualKey",
    # Device token models
    "DeviceToken",
    # Campaign / Incentive models
    "Campaign",
    "SessionEvent",
    "IncentiveGrant",
    # Extra models
    "CreditLedger",
    "IncentiveRule",
    "UtilityEvent",
    "Follow",
    "RewardEvent",
    "FollowerShare",
    "CommunityPeriod",
    "Challenge",
    "Participation",
    "FeatureFlag",
    "DualZoneSession",
    # Merchant overhaul models
    "MerchantOAuthToken",
    "MerchantSubscription",
    "AdImpression",
    # Partner models
    "Partner",
    "PartnerAPIKey",
    # Loyalty models
    "LoyaltyCard",
    "LoyaltyProgress",
    # Driver order models
    "DriverOrder",
    # Driver wallet models
    "Payout",
    "WalletLedger",
    # Merchant reward models
    "MerchantJoinRequest",
    "ReceiptStatus",
    "ReceiptSubmission",
    "RewardClaim",
    "RewardClaimStatus",
    # Tesla models
    "EVVerificationCode",
    "TeslaConnection",
]
