"""
Merchant dashboard UI (server-rendered HTML)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services.merchant_analytics import merchant_offers, merchant_summary

router = APIRouter(tags=["merchant_ui"])


@router.get("/m/dashboard", response_class=HTMLResponse)
async def merchant_dashboard(
    merchant_id: int = Query(...),
    db: Session = Depends(get_db)
):
    """
    Server-rendered merchant dashboard page.
    Only available if DASHBOARD_ENABLE=true.
    """
    if not settings.dashboard_enable:
        raise HTTPException(status_code=404, detail="Dashboard disabled")
    
    # Get merchant info
    merchant_result = db.execute(text("""
        SELECT id, name, category FROM merchants WHERE id = :merchant_id
    """), {"merchant_id": merchant_id}).first()
    
    if not merchant_result:
        raise HTTPException(status_code=404, detail="Merchant not found")
    
    merchant_name = merchant_result[1] or f"Merchant {merchant_id}"
    
    # Get summary data
    summary = merchant_summary(db, merchant_id)
    
    # Get offers
    offers_data = merchant_offers(db, merchant_id)
    
    # Build hourly chart data
    top_hours = summary.get("top_hours", {})
    hours_data = [top_hours.get(h, 0) for h in range(24)]
    max_hour_value = max(hours_data) if hours_data and max(hours_data) > 0 else 1  # Avoid division by zero
    
    # Format last events
    last_events = summary.get("last_events", [])[:10]
    
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nerava Merchant Dashboard — {merchant_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            color: #333;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 30px;
        }}
        h1 {{
            color: #1a202c;
            margin-bottom: 10px;
            font-size: 28px;
        }}
        .subtitle {{
            color: #6b7280;
            margin-bottom: 30px;
        }}
        .kpis {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .kpi {{
            background: #f9fafb;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #3b82f6;
        }}
        .kpi-label {{
            color: #6b7280;
            font-size: 14px;
            margin-bottom: 8px;
        }}
        .kpi-value {{
            color: #1a202c;
            font-size: 32px;
            font-weight: 600;
        }}
        .chart-container {{
            margin: 30px 0;
            padding: 20px;
            background: #f9fafb;
            border-radius: 8px;
        }}
        .chart-title {{
            color: #1a202c;
            margin-bottom: 15px;
            font-size: 18px;
        }}
        .chart {{
            height: 200px;
            display: flex;
            align-items: flex-end;
            gap: 4px;
        }}
        .bar {{
            flex: 1;
            background: #3b82f6;
            min-height: 4px;
            border-radius: 2px 2px 0 0;
            position: relative;
        }}
        .bar:hover {{
            background: #2563eb;
            opacity: 0.9;
        }}
        .bar-label {{
            position: absolute;
            bottom: -20px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 11px;
            color: #6b7280;
        }}
        .events {{
            margin-top: 30px;
        }}
        .events-title {{
            color: #1a202c;
            margin-bottom: 15px;
            font-size: 18px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th {{
            background: #f9fafb;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
        }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #e5e7eb;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge.purchase {{
            background: #dbeafe;
            color: #1e40af;
        }}
        .badge.claimed {{
            background: #d1fae5;
            color: #065f46;
        }}
        .badge.unclaimed {{
            background: #fee2e2;
            color: #991b1b;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Nerava Merchant Dashboard</h1>
        <p class="subtitle">{merchant_name}</p>
        
        <div class="kpis">
            <div class="kpi">
                <div class="kpi-label">Verified Sessions</div>
                <div class="kpi-value">{summary.get('verified_sessions', 0)}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">Purchase Rewards</div>
                <div class="kpi-value">{summary.get('purchase_rewards', 0)}</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">Total Rewards Paid</div>
                <div class="kpi-value">${summary.get('total_rewards_paid', 0) / 100:.2f}</div>
            </div>
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Activity by Hour</div>
            <div class="chart" id="hourlyChart">
                {' '.join([f'<div class="bar" style="height: {int(h * 100 / max_hour_value)}%"><span class="bar-label">{h}</span></div>' for h in range(24)])}
            </div>
        </div>
        
        <div class="events">
            <div class="events-title">Recent Events</div>
            <table>
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Time</th>
                    </tr>
                </thead>
                <tbody>
                    {' '.join([f'''
                    <tr>
                        <td><span class="badge purchase">{evt.get('type', 'unknown')}</span></td>
                        <td>${evt.get('amount_cents', 0) / 100:.2f}</td>
                        <td><span class="badge {'claimed' if evt.get('claimed') else 'unclaimed'}">{'Claimed' if evt.get('claimed') else 'Unclaimed'}</span></td>
                        <td>{evt.get('created_at', 'N/A')[:19] if evt.get('created_at') else 'N/A'}</td>
                    </tr>
                    ''' for evt in last_events])}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
    """
    
    return HTMLResponse(content=html)

