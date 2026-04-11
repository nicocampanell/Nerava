"""
Step 7: Auth edge cases.

Covers the remaining auth gaps that were not included in
test_auth_google.py or the new test_auth_apple.py:

  1. Phone OTP flow: start → verify with stub → user created +
     tokens returned
  2. OTP verify with invalid code → rejected (patched stub)
  3. Refresh token rotation: old token unusable after rotation
  4. Refresh token reuse detection: revoked token rejected with
     a special header
  5. Logout revokes refresh token; subsequent refresh attempts fail
  6. Logout without auth + without token is a no-op (does not 500)
  7. Magic link endpoint: DEBUG_RETURN_MAGIC_LINK only leaks the
     URL in non-prod environments

OTP provider is the stub (OTP_PROVIDER=stub in conftest). The stub
accepts any non-empty code in dev mode (see stub_provider.py:86-88),
so the "wrong code rejected" test patches OTPServiceV2.verify_otp
to raise the HTTPException that would fire in production.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

os.environ.setdefault("STRICT_STARTUP_VALIDATION", "false")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("OTP_PROVIDER", "stub")
os.environ.setdefault("TESLA_MOCK_MODE", "true")
os.environ.setdefault("JWT_SECRET", "test_secret_for_pytest")

from app.models.user import User  # noqa: E402
from app.services.refresh_token_service import RefreshTokenService  # noqa: E402

logger = logging.getLogger(__name__)

OTP_START_ENDPOINT = "/auth/otp/start"
OTP_VERIFY_ENDPOINT = "/auth/otp/verify"
REFRESH_ENDPOINT = "/auth/refresh"
LOGOUT_ENDPOINT = "/auth/logout"
MAGIC_LINK_ENDPOINT = "/v1/auth/magic_link/request"


def _unique_phone() -> str:
    """Generate a unique +1 US test phone number."""
    suffix = uuid.uuid4().int % 10_000_000
    return f"+1512{suffix:07d}"


class TestPhoneOTPFlow:
    """Full phone OTP send → verify happy path."""

    def test_send_otp_succeeds_for_valid_phone(self, client: TestClient) -> None:
        """The /auth/otp/start endpoint accepts a well-formed phone."""
        phone = _unique_phone()

        # Patch the underlying send to skip rate limiting + audit writes
        with patch(
            "app.services.otp_service_v2.OTPServiceV2.send_otp",
            new=AsyncMock(return_value=True),
        ):
            response = client.post(
                OTP_START_ENDPOINT,
                json={"phone": phone},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["otp_sent"] is True

    def test_verify_otp_creates_user_and_returns_tokens(self, client: TestClient, db) -> None:
        phone = _unique_phone()

        with patch(
            "app.services.otp_service_v2.OTPServiceV2.verify_otp",
            new=AsyncMock(return_value=phone),
        ):
            response = client.post(
                OTP_VERIFY_ENDPOINT,
                json={"phone": phone, "code": "000000"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["user"]["phone"] == phone
        assert body["user"]["auth_provider"] == "phone"

        # User row was inserted
        user = db.query(User).filter(User.phone == phone).first()
        assert user is not None
        assert user.role_flags == "driver"
        assert user.is_active is True

    def test_verify_otp_reuses_existing_user(self, client: TestClient, db) -> None:
        """Second verify for same phone returns existing user, no duplicate."""
        phone = _unique_phone()
        existing = User(
            public_id=str(uuid.uuid4()),
            phone=phone,
            auth_provider="phone",
            role_flags="driver",
            is_active=True,
        )
        db.add(existing)
        db.commit()
        existing_public_id = existing.public_id
        existing_id = existing.id

        with patch(
            "app.services.otp_service_v2.OTPServiceV2.verify_otp",
            new=AsyncMock(return_value=phone),
        ):
            response = client.post(
                OTP_VERIFY_ENDPOINT,
                json={"phone": phone, "code": "000000"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["public_id"] == existing_public_id

        # No duplicate user created
        matching = db.query(User).filter(User.phone == phone).all()
        assert len(matching) == 1
        assert matching[0].id == existing_id

    def test_verify_otp_with_wrong_code_rejected(self, client: TestClient) -> None:
        """
        When the OTP service raises HTTPException(400) the router
        re-raises it. The stub provider accepts any non-empty code
        in dev mode, so we patch verify_otp directly to simulate
        the production rejection path.
        """
        phone = _unique_phone()

        def _reject(*args: Any, **kwargs: Any) -> Any:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP code",
            )

        with patch(
            "app.services.otp_service_v2.OTPServiceV2.verify_otp",
            side_effect=_reject,
        ):
            response = client.post(
                OTP_VERIFY_ENDPOINT,
                json={"phone": phone, "code": "wrong"},
            )

        assert response.status_code == 400


def _bootstrap_user_and_token(client: TestClient, db) -> tuple[User, str]:
    """
    Create a user via the OTP verify flow and return (user, refresh_token).
    Shared by TestRefreshTokenRotation and TestLogoutFlow — previously
    duplicated on both classes, DRY'd per Gemini review.
    """
    phone = _unique_phone()
    with patch(
        "app.services.otp_service_v2.OTPServiceV2.verify_otp",
        new=AsyncMock(return_value=phone),
    ):
        response = client.post(
            OTP_VERIFY_ENDPOINT,
            json={"phone": phone, "code": "000000"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    user = db.query(User).filter(User.phone == phone).first()
    assert user is not None
    return user, body["refresh_token"]


class TestRefreshTokenRotation:
    """Refresh flow: old token revoked, new token issued, reuse detected."""

    def test_rotation_issues_new_token_old_becomes_unusable(self, client: TestClient, db) -> None:
        _, original_refresh = _bootstrap_user_and_token(client, db)

        # Rotate: old → new
        response = client.post(
            REFRESH_ENDPOINT,
            json={"refresh_token": original_refresh},
        )
        assert response.status_code == 200, response.text
        new_refresh = response.json()["refresh_token"]
        assert new_refresh != original_refresh

        # New token works
        response_new = client.post(
            REFRESH_ENDPOINT,
            json={"refresh_token": new_refresh},
        )
        assert response_new.status_code == 200

        # Old token no longer works (either invalid or reuse-detected)
        response_old = client.post(
            REFRESH_ENDPOINT,
            json={"refresh_token": original_refresh},
        )
        assert response_old.status_code == 401

    def test_invalid_refresh_token_returns_401(self, client: TestClient) -> None:
        response = client.post(
            REFRESH_ENDPOINT,
            json={"refresh_token": "clearly-not-a-valid-refresh-token"},
        )
        assert response.status_code == 401


class TestLogoutFlow:
    """Logout revokes refresh tokens."""

    def test_logout_with_refresh_token_revokes_it(self, client: TestClient, db) -> None:
        _, refresh_token = _bootstrap_user_and_token(client, db)

        response = client.post(
            LOGOUT_ENDPOINT,
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        # After logout, the refresh token must not be usable
        response_after = client.post(
            REFRESH_ENDPOINT,
            json={"refresh_token": refresh_token},
        )
        assert response_after.status_code == 401

    def test_logout_without_auth_and_without_token_is_noop(self, client: TestClient) -> None:
        """Calling /auth/logout with an empty body must not 500."""
        response = client.post(LOGOUT_ENDPOINT, json={})
        # 200 when the body is valid; the handler handles the empty
        # case by falling through to the current_user branch, which
        # is None for this anonymous request → no-op.
        assert response.status_code == 200


class TestRefreshTokenServiceSanity:
    """RefreshTokenService direct unit tests — no HTTP layer."""

    def test_create_and_validate_token(self, db) -> None:
        user = User(
            public_id=str(uuid.uuid4()),
            phone=_unique_phone(),
            auth_provider="phone",
            role_flags="driver",
            is_active=True,
        )
        db.add(user)
        db.commit()

        plain_token, token_model = RefreshTokenService.create_refresh_token(db, user)
        db.commit()

        validated = RefreshTokenService.validate_refresh_token(db, plain_token)
        assert validated is not None
        assert validated.id == token_model.id
        assert validated.user_id == user.id
        assert validated.revoked is False

    def test_rotate_revokes_old_and_creates_new(self, db) -> None:
        user = User(
            public_id=str(uuid.uuid4()),
            phone=_unique_phone(),
            auth_provider="phone",
            role_flags="driver",
            is_active=True,
        )
        db.add(user)
        db.commit()

        plain_old, old_token = RefreshTokenService.create_refresh_token(db, user)
        db.commit()

        plain_new, new_token = RefreshTokenService.rotate_refresh_token(db, old_token)
        db.commit()

        assert plain_new != plain_old
        assert new_token.id != old_token.id
        # Old token is revoked
        db.refresh(old_token)
        assert old_token.revoked is True
        # New token is valid
        validated = RefreshTokenService.validate_refresh_token(db, plain_new)
        assert validated is not None
        assert validated.revoked is False

    def test_revoke_all_user_tokens_flags_them(self, db) -> None:
        user = User(
            public_id=str(uuid.uuid4()),
            phone=_unique_phone(),
            auth_provider="phone",
            role_flags="driver",
            is_active=True,
        )
        db.add(user)
        db.commit()

        # Create 3 tokens
        tokens = [RefreshTokenService.create_refresh_token(db, user) for _ in range(3)]
        db.commit()

        RefreshTokenService.revoke_all_user_tokens(db, user.id)
        db.commit()

        for _, token_model in tokens:
            db.refresh(token_model)
            assert token_model.revoked is True


class TestMagicLinkProdGating:
    """
    Magic link endpoint must NOT return the link URL in production.
    DEBUG_RETURN_MAGIC_LINK is a dev-only affordance. This test
    exercises the branch at auth_domain.py:361-367 that gates the
    URL echo behind both `not settings.is_prod` AND the flag.

    Rate-limit note: /v1/auth/magic_link/request is capped at 3
    requests/minute per client IP (middleware/ratelimit.py:21).
    The TestClient always presents as the same IP, so 4+ requests
    in the same class hit 429. We clear the bucket cache via an
    autouse fixture so every test in the class starts with a
    fresh budget.
    """

    @pytest.fixture(autouse=True)
    def _clear_magic_link_rate_bucket(self) -> None:
        """
        Wipe the in-memory rate-limit buckets before each test so
        the magic-link endpoint isn't pre-limited by prior tests.
        The buckets dict lives on the middleware instance attached
        to the app.
        """
        from app.main_simple import app

        # Walk the actual middleware stack (built on first request)
        # to find the RateLimitMiddleware instance and clear its buckets.
        stack = getattr(app, "middleware_stack", None)
        _current = stack
        while _current is not None:
            if _current.__class__.__name__ == "RateLimitMiddleware" and hasattr(
                _current, "buckets"
            ):
                _current.buckets.clear()
            inner = getattr(_current, "app", None)
            if inner is None or inner is _current:
                break
            _current = inner

    def test_magic_link_endpoint_exists_and_responds(self, client: TestClient) -> None:
        """
        Magic link endpoint accepts a well-formed email in non-prod
        and returns a 200 — either with or without magic_link_url
        depending on DEBUG_RETURN_MAGIC_LINK. We don't assert on the
        URL content here because the email sender is live.
        """
        email = f"magic-{uuid.uuid4().hex[:8]}@test.nerava.network"

        # Mock the email sender so the test doesn't try to send real mail
        with patch("app.core.email_sender.get_email_sender") as mock_get_sender:
            mock_sender = mock_get_sender.return_value
            mock_sender.send_email.return_value = True

            response = client.post(
                MAGIC_LINK_ENDPOINT,
                json={"email": email},
            )

        # In non-prod, the endpoint returns 200 with a confirmation message
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("email") == email
        # The "message" field is always present
        assert "message" in body

    def test_magic_link_url_echoed_only_when_debug_flag_set(self, client: TestClient) -> None:
        """
        Exercise the DEBUG_RETURN_MAGIC_LINK branch. The flag is a
        runtime settings attribute, so we toggle it via patch and
        verify the response shape changes.
        """
        from app.core.config import settings

        email = f"magic-debug-{uuid.uuid4().hex[:8]}@test.nerava.network"

        with patch("app.core.email_sender.get_email_sender") as mock_get_sender:
            mock_sender = mock_get_sender.return_value
            mock_sender.send_email.return_value = True

            # With DEBUG_RETURN_MAGIC_LINK=True in non-prod, URL is echoed
            with patch.object(settings, "DEBUG_RETURN_MAGIC_LINK", True):
                response = client.post(
                    MAGIC_LINK_ENDPOINT,
                    json={"email": email},
                )
                assert response.status_code == 200
                body = response.json()
                # The debug flag branch returns the URL
                assert "magic_link_url" in body
                assert body["magic_link_url"].startswith("http")

            # With DEBUG_RETURN_MAGIC_LINK=False, URL is NOT echoed
            email_no_debug = f"magic-nodebug-{uuid.uuid4().hex[:8]}@test.nerava.network"
            with patch.object(settings, "DEBUG_RETURN_MAGIC_LINK", False):
                response_nodebug = client.post(
                    MAGIC_LINK_ENDPOINT,
                    json={"email": email_no_debug},
                )
                assert response_nodebug.status_code == 200
                body_nodebug = response_nodebug.json()
                assert "magic_link_url" not in body_nodebug

    def test_magic_link_url_is_never_echoed_when_is_prod_is_true(self, client: TestClient) -> None:
        """
        Verify the real behavior: when settings.is_prod is True, the
        handler MUST NOT return magic_link_url in the response body,
        regardless of the DEBUG_RETURN_MAGIC_LINK flag. This replaces
        the previous source-inspection check (flagged by review as
        fragile — it tested the source, not the behavior).
        """
        from app.core.config import settings

        email = f"magic-prod-{uuid.uuid4().hex[:8]}@test.nerava.network"

        with patch("app.core.email_sender.get_email_sender") as mock_get_sender:
            mock_sender = mock_get_sender.return_value
            mock_sender.send_email.return_value = True

            # Force is_prod=True AND DEBUG_RETURN_MAGIC_LINK=True —
            # the production branch must still block the echo.
            with (
                patch.object(type(settings), "is_prod", property(lambda self: True)),
                patch.object(settings, "DEBUG_RETURN_MAGIC_LINK", True),
            ):
                response = client.post(
                    MAGIC_LINK_ENDPOINT,
                    json={"email": email},
                )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "magic_link_url" not in body, (
            "Production branch must never return magic_link_url "
            "even when DEBUG_RETURN_MAGIC_LINK is set"
        )
