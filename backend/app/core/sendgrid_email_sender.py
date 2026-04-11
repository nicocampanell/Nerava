"""
SendGrid Email Sender — sends transactional email via SendGrid REST API.
Uses httpx (already a dependency) to avoid adding the sendgrid pip package.
"""

import logging
import os
from typing import Optional

import httpx

from .email_sender import EmailSender

logger = logging.getLogger(__name__)

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


class SendGridEmailSender(EmailSender):
    """Send email via SendGrid v3 REST API."""

    def __init__(self):
        self._api_key = os.getenv("SENDGRID_API_KEY", "")
        self._from_email = os.getenv("SENDGRID_FROM_EMAIL", os.getenv("EMAIL_FROM", "noreply@nerava.network"))
        if not self._api_key:
            logger.warning("SENDGRID_API_KEY not set — emails will fail")

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
    ) -> bool:
        content = [{"type": "text/plain", "value": body_text}]
        if body_html:
            content.append({"type": "text/html", "value": body_html})

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self._from_email, "name": "Nerava"},
            "subject": subject,
            "content": content,
        }

        try:
            resp = httpx.post(
                SENDGRID_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code in (200, 201, 202):
                logger.info("SendGrid email sent to %s subject=%s", to_email, subject)
                return True
            else:
                logger.error("SendGrid send failed: status=%s body=%s", resp.status_code, resp.text[:200])
                return False
        except Exception as exc:
            logger.error("SendGrid send_email error: %s", exc)
            return False
