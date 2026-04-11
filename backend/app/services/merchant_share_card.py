"""
Merchant Share Card Service

Generates deterministic PNG social cards for merchant sharing.
"""
import logging
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.domain import DomainMerchant, MerchantRedemption

logger = logging.getLogger(__name__)


def _get_font_path() -> Optional[Path]:
    """Get path to bundled Inter font file"""
    # Try ui-mobile/assets/fonts/Inter-Regular.ttf
    ui_mobile_path = Path(__file__).parent.parent.parent.parent / "ui-mobile" / "assets" / "fonts" / "Inter-Regular.ttf"
    if ui_mobile_path.exists():
        return ui_mobile_path
    
    # Fallback: try static/fonts
    static_path = Path(__file__).parent.parent / "static" / "fonts" / "Inter-Regular.ttf"
    if static_path.exists():
        return static_path
    
    return None


def generate_share_card(
    db: Session,
    merchant_id: str,
    days: int = 7
) -> bytes:
    """
    Generate a deterministic 1200x630 PNG social card for merchant.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        days: Number of days to look back (default: 7)
        
    Returns:
        PNG image bytes
        
    Raises:
        ValueError: If merchant not found
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow is required for share card generation. Install with: pip install Pillow")
    
    # Get merchant
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise ValueError(f"Merchant {merchant_id} not found")
    
    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Get redemption stats
    redemptions = db.query(MerchantRedemption).filter(
        and_(
            MerchantRedemption.merchant_id == merchant_id,
            MerchantRedemption.created_at >= start_date,
            MerchantRedemption.created_at <= end_date
        )
    ).all()
    
    redemption_count = len(redemptions)
    total_discount_cents = sum(r.discount_cents for r in redemptions)
    total_discount_dollars = total_discount_cents / 100.0
    
    # Create image (1200x630 for social cards)
    width, height = 1200, 630
    img = Image.new('RGB', (width, height), color='#1e40af')  # Nerava blue background
    draw = ImageDraw.Draw(img)
    
    # Load font
    font_path = _get_font_path()
    if font_path and font_path.exists():
        try:
            # Use bundled font
            title_font = ImageFont.truetype(str(font_path), 48)
            subtitle_font = ImageFont.truetype(str(font_path), 32)
            body_font = ImageFont.truetype(str(font_path), 24)
        except Exception as e:
            logger.warning(f"Failed to load bundled font: {e}, using default")
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()
            body_font = ImageFont.load_default()
    else:
        # Fallback to default font
        logger.warning("Inter-Regular.ttf not found, using default font")
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    
    # Draw content
    y_offset = 80
    
    # Merchant name
    merchant_name = merchant.name[:50]  # Truncate if too long
    draw.text((60, y_offset), merchant_name, fill='white', font=title_font)
    y_offset += 80
    
    # Date range
    date_str = f"Last {days} days"
    draw.text((60, y_offset), date_str, fill='#cbd5e1', font=subtitle_font)
    y_offset += 100
    
    # Stats
    stats = [
        ("Redemptions", f"{redemption_count}"),
        ("Total Discount", f"${total_discount_dollars:.2f}")
    ]
    
    for label, value in stats:
        draw.text((60, y_offset), f"{label}: {value}", fill='white', font=body_font)
        y_offset += 50
    
    # Share line
    y_offset += 40
    share_line = f"We supported {redemption_count} off-peak rewards this week with Nerava."
    draw.text((60, y_offset), share_line, fill='#cbd5e1', font=body_font)
    
    # Nerava branding (bottom right)
    branding = "Nerava"
    bbox = draw.textbbox((0, 0), branding, font=subtitle_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text((width - text_width - 60, height - text_height - 40), branding, fill='white', font=subtitle_font)
    
    # Convert to bytes
    output = BytesIO()
    img.save(output, format='PNG')
    output.seek(0)
    
    logger.info(f"Generated share card for merchant {merchant_id}: {redemption_count} redemptions, ${total_discount_dollars:.2f} discount")
    
    return output.read()
