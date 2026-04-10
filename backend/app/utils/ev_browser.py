"""
EV browser detection utilities.

Detects Tesla and other EV in-car browsers from User-Agent headers.
Used for validating that checkin requests come from in-car browsers.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


@dataclass
class EVBrowserInfo:
    """Information about detected EV browser."""
    is_ev_browser: bool
    brand: Optional[str] = None
    firmware_version: Optional[str] = None
    user_agent: Optional[str] = None


# Patterns for detection
TESLA_MODERN_PATTERN = re.compile(r'Tesla/(\d{4}\.\d+\.\d+(?:\.\d+)?)', re.IGNORECASE)
TESLA_LEGACY_PATTERN = re.compile(r'QtCarBrowser', re.IGNORECASE)

# Additional EV browser patterns
RIVIAN_PATTERN = re.compile(r'Rivian', re.IGNORECASE)
LUCID_PATTERN = re.compile(r'Lucid', re.IGNORECASE)
POLESTAR_PATTERN = re.compile(r'Polestar', re.IGNORECASE)


def detect_ev_browser(user_agent: str) -> EVBrowserInfo:
    """
    Detect if request is from an EV in-car browser.

    Currently supports:
    - Tesla (modern firmware with Tesla/xxxx.xx.xx)
    - Tesla (legacy with QtCarBrowser)
    - Android Automotive (Polestar, Volvo, etc.)
    - Rivian, Lucid, Polestar direct patterns

    Returns EVBrowserInfo with detected brand and firmware.
    """
    if not user_agent:
        return EVBrowserInfo(is_ev_browser=False, user_agent=user_agent)

    # Tesla modern detection (most common)
    tesla_match = TESLA_MODERN_PATTERN.search(user_agent)
    if tesla_match:
        return EVBrowserInfo(
            is_ev_browser=True,
            brand="Tesla",
            firmware_version=tesla_match.group(1),
            user_agent=user_agent,
        )

    # Tesla legacy detection
    if TESLA_LEGACY_PATTERN.search(user_agent):
        return EVBrowserInfo(
            is_ev_browser=True,
            brand="Tesla",
            firmware_version="legacy",
            user_agent=user_agent,
        )

    # Android Automotive (Polestar, Volvo, Mercedes, etc.)
    if 'android automotive' in user_agent.lower():
        # Try to extract specific brand
        brand = "Android Automotive"
        if POLESTAR_PATTERN.search(user_agent):
            brand = "Polestar"
        return EVBrowserInfo(
            is_ev_browser=True,
            brand=brand,
            user_agent=user_agent,
        )

    # Rivian direct detection
    if RIVIAN_PATTERN.search(user_agent):
        return EVBrowserInfo(
            is_ev_browser=True,
            brand="Rivian",
            user_agent=user_agent,
        )

    # Lucid direct detection
    if LUCID_PATTERN.search(user_agent):
        return EVBrowserInfo(
            is_ev_browser=True,
            brand="Lucid",
            user_agent=user_agent,
        )

    return EVBrowserInfo(is_ev_browser=False, user_agent=user_agent)


def is_tesla_browser(user_agent: str) -> bool:
    """Quick check if request is from Tesla browser."""
    if not user_agent:
        return False
    return bool(TESLA_MODERN_PATTERN.search(user_agent) or
                TESLA_LEGACY_PATTERN.search(user_agent))


def require_ev_browser(request: Request, allow_dev_bypass: bool = True) -> EVBrowserInfo:
    """
    Validate that request comes from an EV in-car browser.

    Use as a FastAPI dependency to enforce EV browser requirement.

    Args:
        request: FastAPI request object
        allow_dev_bypass: If True, allows bypass in dev mode with header

    Returns:
        EVBrowserInfo if valid

    Raises:
        HTTPException(403) if not an EV browser
    """
    user_agent = request.headers.get("User-Agent", "")

    # Dev bypass: allow X-EV-Browser-Bypass header in non-production
    if allow_dev_bypass:
        import os
        env = os.getenv("ENV", "dev").lower()
        if env != "prod" and request.headers.get("X-EV-Browser-Bypass") == "true":
            logger.info("EV browser check bypassed in dev mode")
            return EVBrowserInfo(
                is_ev_browser=True,
                brand="DevBypass",
                user_agent=user_agent,
            )

    info = detect_ev_browser(user_agent)

    if not info.is_ev_browser:
        logger.warning(f"Non-EV browser rejected: {user_agent[:100]}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "ev_browser_required",
                "message": "This feature is only available from your vehicle's browser. Please open this page in your Tesla or EV browser.",
            }
        )

    logger.info(f"EV browser validated: {info.brand} {info.firmware_version or ''}")
    return info


async def get_ev_browser_info(request: Request) -> EVBrowserInfo:
    """
    FastAPI dependency to get EV browser info without requiring it.

    Returns EVBrowserInfo with is_ev_browser=False if not detected.
    """
    user_agent = request.headers.get("User-Agent", "")
    return detect_ev_browser(user_agent)
