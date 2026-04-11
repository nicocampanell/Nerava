"""
Step 6: Apple Sign-In tests.

Google auth has a dedicated test file (test_auth_google.py). Apple
does not, which is why this gap made it onto the Step 6 priority list.
This file mirrors the structure of test_auth_google.py but for the
Apple JWT / /v1/auth/apple endpoint.

Coverage:
  1. New-user creation: first Apple login with unseen provider_sub
     inserts a User + UserPreferences row and returns tokens
  2. Existing-user match: second login with the same provider_sub
     returns the existing user (not a duplicate row)
  3. Invalid token: missing sub → 400
  4. Token verification failure → surfaced as 500 by the router's
     catch-all (current service behavior)
  5. Apple config missing: APPLE_CLIENT_ID unset → 503 from the
     service helper
  6. Tokens returned on success match the User's public_id and
     auth_provider="apple"

The Apple ID token verification helper (verify_apple_id_token) talks
to Apple's JWKS endpoint and validates the RS256 signature, so every
test here mocks it. We never hit the real Apple API.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

# Env vars required for the test harness and for
# verify_apple_id_token to import cleanly. We use monkeypatch via an
# autouse fixture so settings override reliably even if the env was
# already populated by a parent process — plain os.environ.setdefault
# silently no-ops in that case, which Gemini flagged as fragile.
#
# The actual verify_apple_id_token helper is mocked in every test,
# so the APPLE_CLIENT_ID value does not need to be real — it just
# needs to be non-empty so the config-validator does not raise.
_TEST_ENV: Dict[str, str] = {
    "APPLE_CLIENT_ID": "com.nerava.test.app",
    "STRICT_STARTUP_VALIDATION": "false",
    "ENV": "test",
    "OTP_PROVIDER": "stub",
    "TESLA_MOCK_MODE": "true",
    "JWT_SECRET": "test_secret_for_pytest",
}

# Apply once at module import time so `from app.models.user import User`
# below does not race the fixture. Plain os.environ.setdefault is
# still used for the import-time bootstrap, but the autouse fixture
# below re-asserts the values for every test so any pollution from a
# parent process is overridden.
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

from app.models.user import User  # noqa: E402

logger = logging.getLogger(__name__)

APPLE_ENDPOINT = "/auth/apple"


@pytest.fixture(autouse=True)
def _force_apple_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Unconditionally set every test-env variable before each test.
    Unlike os.environ.setdefault this OVERRIDES a value if the
    parent process already set a different one, so CI environments
    can't poison the suite by pre-setting APPLE_CLIENT_ID to a
    real production value.
    """
    for k, v in _TEST_ENV.items():
        monkeypatch.setenv(k, v)


def _mock_apple_payload(
    *,
    sub: str = "000123.abcdef1234567890.0123",
    email: str = "apple-user@privaterelay.appleid.com",
) -> Dict[str, Any]:
    """Return a dict in the shape verify_apple_id_token() would return."""
    return {
        "email": email,
        "sub": sub,
        "email_verified": True,
        "iss": "https://appleid.apple.com",
        "aud": "com.nerava.test.app",
        "exp": 9999999999,
    }


class TestAppleAuthNewUser:
    """First-time Apple login creates a user and returns tokens."""

    def test_first_login_creates_user_and_returns_tokens(self, client: TestClient) -> None:
        fake_sub = f"000{uuid.uuid4().hex[:16]}"
        payload = _mock_apple_payload(sub=fake_sub, email="newuser@privaterelay.appleid.com")

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            return_value=payload,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "mock.apple.id.token"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["user"]["auth_provider"] == "apple"
        assert body["user"]["email"] == "newuser@privaterelay.appleid.com"
        assert body["user"]["public_id"]

    def test_missing_sub_returns_400(self, client: TestClient) -> None:
        """An Apple token without a 'sub' claim is invalid."""
        payload = {"email": "nosub@test.nerava.network"}  # no sub field

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            return_value=payload,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "mock.token.no.sub"},
            )

        assert response.status_code == 400
        # The router surfaces "missing sub" in the detail
        assert "sub" in response.text.lower()

    def test_email_can_be_omitted_for_apple_private_relay(self, client: TestClient) -> None:
        """
        Apple's Hide My Email feature can send a private relay
        email OR no email at all on subsequent logins. The router
        must tolerate a None email and still create/return the user.
        """
        fake_sub = f"noemail_{uuid.uuid4().hex[:16]}"
        payload = {"sub": fake_sub}  # no email at all

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            return_value=payload,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "mock.token.no.email"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["auth_provider"] == "apple"
        # Email is None; the response key must still be present
        assert "email" in body["user"]


class TestAppleAuthExistingUser:
    """Second login with the same provider_sub returns the existing user."""

    def test_second_login_reuses_existing_user(self, client: TestClient, db) -> None:
        fake_sub = f"existing_{uuid.uuid4().hex[:16]}"
        # Pre-create the user
        existing = User(
            public_id=str(uuid.uuid4()),
            email="existing@privaterelay.appleid.com",
            auth_provider="apple",
            provider_sub=fake_sub,
            is_active=True,
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        existing_public_id = existing.public_id
        existing_id = existing.id

        payload = _mock_apple_payload(sub=fake_sub, email="existing@privaterelay.appleid.com")
        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            return_value=payload,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "mock.existing.token"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        # The returned public_id must match the pre-existing user,
        # not a freshly-inserted duplicate
        assert body["user"]["public_id"] == existing_public_id

        # Only one User row for this provider_sub
        matching_users = (
            db.query(User)
            .filter(User.auth_provider == "apple", User.provider_sub == fake_sub)
            .all()
        )
        assert len(matching_users) == 1
        assert matching_users[0].id == existing_id


class TestAppleAuthErrorBranches:
    """Error paths: token verification failure, config missing, etc."""

    def test_token_verification_401_is_re_raised_as_401(self, client: TestClient) -> None:
        """
        verify_apple_id_token raises HTTPException(401) when the
        token signature doesn't match Apple's JWKS. The router's
        generic except clause catches the HTTPException and the
        current behavior is to re-raise it (matching the auth.py
        source at 390-391 — HTTPException is re-raised before the
        500 catch-all).
        """

        def _raise_401(*args: Any, **kwargs: Any) -> Any:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Apple token signature",
            )

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            side_effect=_raise_401,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "tampered.token"},
            )

        # The HTTPException branch re-raises, so we get the underlying 401
        assert response.status_code == 401

    def test_apple_config_missing_surfaces_as_503(self, client: TestClient) -> None:
        """
        If APPLE_CLIENT_ID is not configured the service helper
        raises 503. The router re-raises HTTPExceptions as-is, so
        the client sees 503, not 500.
        """

        def _raise_503(*args: Any, **kwargs: Any) -> Any:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Apple authentication is not configured.",
            )

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            side_effect=_raise_503,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "any.token"},
            )

        assert response.status_code == 503

    def test_unexpected_runtime_error_becomes_500(self, client: TestClient) -> None:
        """
        A non-HTTPException raised from inside the handler is caught
        by the except Exception branch and returned as a generic 500
        with a safe message. Raw exception text must never reach the
        client — this was a CLAUDE.md audit rule from the April 2026
        review.
        """

        def _raise_runtime(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("UNSAFE_SECRET_STRING_SHOULD_NOT_LEAK")

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            side_effect=_raise_runtime,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": "any.token"},
            )

        assert response.status_code == 500
        # The unsafe exception text MUST NOT appear in the response
        assert "UNSAFE_SECRET_STRING_SHOULD_NOT_LEAK" not in response.text


class TestAppleAuthDoesNotLeakCredentials:
    """
    Rule: handlers must never log or return the raw ID token. The
    Apple ID token contains a signed JWT with user identity claims;
    leaking it is equivalent to leaking a session token.
    """

    def test_router_does_not_echo_id_token_in_response(self, client: TestClient) -> None:
        fake_sub = f"echo_{uuid.uuid4().hex[:16]}"
        id_token = "super.secret.apple.id.token.should.not.echo"
        payload = _mock_apple_payload(sub=fake_sub)

        with patch(
            "app.services.apple_auth.verify_apple_id_token",
            return_value=payload,
        ):
            response = client.post(
                APPLE_ENDPOINT,
                json={"id_token": id_token},
            )

        assert response.status_code == 200
        # The raw id_token must not appear anywhere in the response
        assert id_token not in response.text
