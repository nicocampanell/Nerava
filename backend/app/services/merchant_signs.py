"""
Merchant Sign PDF Generation Service
Generates printable PDF signs with QR codes for merchants.
"""

import io
import logging

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import qrcode
    import qrcode.image.pil  # noqa: F401 — validate PIL backend is available

    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

from ..models.domain import DomainMerchant

logger = logging.getLogger(__name__)


def generate_merchant_sign_pdf(merchant: DomainMerchant, qr_url: str) -> bytes:
    """
    Generate a printable PDF sign with merchant info and QR code.

    The sign includes:
    - Nerava branding/wordmark
    - Headline: "Scan to Earn"
    - Subtext: "Charge Anywhere. Spend Everywhere."
    - Merchant name
    - QR code that encodes qr_url

    Args:
        merchant: DomainMerchant instance
        qr_url: Full URL for the QR code (e.g., https://my.nerava.network/qr/{token})

    Returns:
        PDF bytes

    Raises:
        ValueError: If required libraries are not installed
    """
    if not REPORTLAB_AVAILABLE:
        raise ValueError(
            "reportlab is required for PDF generation. Install with: pip install reportlab"
        )
    if not QRCODE_AVAILABLE:
        raise ValueError(
            "qrcode is required for QR code generation. Install with: pip install qrcode[pil]"
        )

    # Create PDF in memory
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Background color (light gray)
    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(0, 0, width, height, fill=1)

    # Title/Header
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.setFont("Helvetica-Bold", 36)
    title = "Nerava"
    title_width = c.stringWidth(title, "Helvetica-Bold", 36)
    c.drawString((width - title_width) / 2, height - 100, title)

    # Headline
    c.setFont("Helvetica-Bold", 28)
    headline = "Scan to Earn"
    headline_width = c.stringWidth(headline, "Helvetica-Bold", 28)
    c.drawString((width - headline_width) / 2, height - 160, headline)

    # Subtext
    c.setFont("Helvetica", 18)
    subtext = "Charge Anywhere. Spend Everywhere."
    subtext_width = c.stringWidth(subtext, "Helvetica", 18)
    c.drawString((width - subtext_width) / 2, height - 200, subtext)

    # Merchant name
    c.setFont("Helvetica-Bold", 24)
    merchant_name = merchant.name or "Merchant"
    merchant_width = c.stringWidth(merchant_name, "Helvetica-Bold", 24)
    c.drawString((width - merchant_width) / 2, height - 260, merchant_name)

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)

    # Create PIL image from QR code
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Convert PIL image to format reportlab can use
    img_buffer = io.BytesIO()
    qr_img.save(img_buffer, format="PNG")
    img_buffer.seek(0)
    img_reader = ImageReader(img_buffer)

    # Draw QR code (centered, below merchant name)
    qr_size = 3 * inch  # 3 inches square
    qr_x = (width - qr_size) / 2
    qr_y = height - 260 - 80 - qr_size
    c.drawImage(img_reader, qr_x, qr_y, width=qr_size, height=qr_size)

    # Footer text
    c.setFont("Helvetica", 12)
    footer = "Present this QR code at checkout to redeem Nova rewards"
    footer_width = c.stringWidth(footer, "Helvetica", 12)
    c.drawString((width - footer_width) / 2, qr_y - 40, footer)

    # Save PDF
    c.save()
    buffer.seek(0)
    pdf_bytes = buffer.read()

    logger.info(f"Generated PDF sign for merchant {merchant.id} ({len(pdf_bytes)} bytes)")

    return pdf_bytes
