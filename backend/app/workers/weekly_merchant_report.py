"""
Weekly Merchant Report Worker

Sends weekly email reports to claimed merchants every Monday at 8am CT.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

CT_TZ = pytz.timezone("America/Chicago")
CHECK_INTERVAL = 3600  # Check every hour


class WeeklyMerchantReportWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_sent_week: Optional[int] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Weekly merchant report worker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Weekly merchant report worker stopped")

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                await self._check_and_send()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Weekly report worker error: {e}", exc_info=True)

    async def _check_and_send(self):
        """Check if it's Monday 8am CT and send reports if not already sent this week."""
        now_ct = datetime.now(CT_TZ)

        # Only send on Monday, hour 8
        if now_ct.weekday() != 0 or now_ct.hour != 8:
            return

        iso_week = now_ct.isocalendar()[1]
        if self._last_sent_week == iso_week:
            return

        logger.info("Sending weekly merchant reports...")
        self._last_sent_week = iso_week

        try:
            await self._send_all_reports()
        except Exception as e:
            logger.error(f"Failed to send weekly reports: {e}", exc_info=True)

    async def _send_all_reports(self):
        """Query all claimed merchants and send individual reports."""
        from app.core.email_sender import get_email_sender
        from app.db import SessionLocal
        from app.models import User
        from app.models.domain import DomainMerchant
        from app.models.session_event import SessionEvent
        from app.models.while_you_charge import Charger
        from app.services.geo import haversine_m
        from sqlalchemy import func

        db = SessionLocal()
        try:
            # Get all active merchants with owners
            merchants = (
                db.query(DomainMerchant)
                .filter(
                    DomainMerchant.status == "active",
                    DomainMerchant.owner_user_id.isnot(None),
                )
                .all()
            )

            if not merchants:
                logger.info("No active merchants to report on")
                return

            email_sender = get_email_sender()
            now = datetime.utcnow()
            week_start = now - timedelta(days=7)
            prev_week_start = now - timedelta(days=14)

            for merchant in merchants:
                try:
                    owner = db.query(User).filter(User.id == merchant.owner_user_id).first()
                    if not owner or not getattr(owner, "email", None):
                        continue

                    # Find nearby chargers
                    delta = 0.006
                    chargers = (
                        db.query(Charger.id, Charger.lat, Charger.lng)
                        .filter(
                            Charger.lat.between(merchant.lat - delta, merchant.lat + delta),
                            Charger.lng.between(merchant.lng - delta, merchant.lng + delta),
                        )
                        .all()
                    )

                    nearby_ids = []
                    for cid, clat, clng in chargers:
                        if haversine_m(merchant.lat, merchant.lng, clat, clng) <= 500:
                            nearby_ids.append(cid)

                    if not nearby_ids:
                        continue

                    # This week stats
                    this_week = (
                        db.query(
                            func.count(SessionEvent.id).label("total"),
                        )
                        .filter(
                            SessionEvent.charger_id.in_(nearby_ids),
                            SessionEvent.session_start >= week_start,
                            SessionEvent.session_end.isnot(None),
                        )
                        .first()
                    )

                    # Last week stats
                    last_week = (
                        db.query(
                            func.count(SessionEvent.id).label("total"),
                        )
                        .filter(
                            SessionEvent.charger_id.in_(nearby_ids),
                            SessionEvent.session_start >= prev_week_start,
                            SessionEvent.session_start < week_start,
                            SessionEvent.session_end.isnot(None),
                        )
                        .first()
                    )

                    this_total = this_week.total or 0
                    last_total = last_week.total or 0
                    change = this_total - last_total
                    change_str = f"+{change}" if change >= 0 else str(change)

                    # Daily breakdown
                    avg_per_day = round(this_total / 7, 1) if this_total else 0

                    portal_url = "https://merchant.nerava.network/insights"

                    html = f"""
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
                        <div style="background: #0a0a0a; padding: 24px; border-radius: 12px 12px 0 0;">
                            <h1 style="color: white; margin: 0; font-size: 20px;">Nerava Weekly Report</h1>
                            <p style="color: #a3a3a3; margin: 4px 0 0;">for {merchant.name}</p>
                        </div>
                        <div style="padding: 24px; border: 1px solid #e5e5e5; border-top: none; border-radius: 0 0 12px 12px;">
                            <h2 style="font-size: 16px; color: #171717; margin-bottom: 16px;">This Week's Highlights</h2>
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr>
                                    <td style="padding: 12px; background: #f5f5f5; border-radius: 8px; text-align: center; width: 50%;">
                                        <div style="font-size: 28px; font-weight: 700; color: #171717;">{this_total}</div>
                                        <div style="font-size: 13px; color: #737373;">EV Sessions</div>
                                    </td>
                                    <td style="width: 12px;"></td>
                                    <td style="padding: 12px; background: #f5f5f5; border-radius: 8px; text-align: center; width: 50%;">
                                        <div style="font-size: 28px; font-weight: 700; color: #171717;">{avg_per_day}</div>
                                        <div style="font-size: 13px; color: #737373;">Avg / Day</div>
                                    </td>
                                </tr>
                            </table>
                            <p style="margin-top: 16px; font-size: 14px; color: #525252;">
                                {change_str} vs last week
                            </p>
                            <a href="{portal_url}" style="display: block; text-align: center; background: #171717; color: white; padding: 12px; border-radius: 8px; text-decoration: none; font-weight: 500; margin-top: 24px;">
                                View your dashboard
                            </a>
                        </div>
                    </div>
                    """

                    email_sender.send(
                        to=owner.email,
                        subject=f"Your Nerava Weekly Report — {this_total} EV sessions",
                        html=html,
                    )
                    logger.info(f"Sent weekly report to {owner.email} for merchant {merchant.id}")

                except Exception as e:
                    logger.error(f"Failed to send report for merchant {merchant.id}: {e}")

        finally:
            db.close()


weekly_merchant_report_worker = WeeklyMerchantReportWorker()
