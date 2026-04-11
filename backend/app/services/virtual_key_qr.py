"""
QR Code Generation Service for Virtual Key Pairing.

Generates Tesla-compatible QR codes for Virtual Key pairing and phone handoff flows.
"""

import io
import logging
import uuid
from typing import Optional

try:
    import qrcode
    import qrcode.image.pil  # noqa: F401 — validate PIL backend is available

    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False


logger = logging.getLogger(__name__)


class VirtualKeyQRService:
    """Generate Tesla-compatible QR codes for Virtual Key pairing."""

    def __init__(self):
        if not QRCODE_AVAILABLE:
            logger.warning("qrcode library not available. Install with: pip install qrcode[pil]")

    def generate_pairing_qr(self, provisioning_token: str, callback_url: str) -> bytes:
        """
        Generate QR code image for Tesla app scanning.

        QR data format (Tesla-specific):
        nerava://pair?token={provisioning_token}&callback={callback_url}

        Args:
            provisioning_token: Unique token for this pairing session
            callback_url: URL to call when pairing completes

        Returns:
            QR code image bytes (PNG format)

        Raises:
            ValueError: If qrcode library is not available
        """
        if not QRCODE_AVAILABLE:
            raise ValueError("qrcode library is required. Install with: pip install qrcode[pil]")

        # Build QR data URL
        qr_data = f"nerava://pair?token={provisioning_token}&callback={callback_url}"

        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        # Create PIL image
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert to bytes
        img_buffer = io.BytesIO()
        qr_img.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        logger.info(f"Generated pairing QR code for token {provisioning_token[:8]}...")

        return img_buffer.read()

    def generate_phone_handoff_qr(self, session_id: str, order_url: str) -> bytes:
        """
        Generate QR code for phone handoff (fallback flow).
        User scans with phone to continue order.

        QR data format:
        {order_url}?session={session_id}

        Args:
            session_id: Arrival session ID
            order_url: URL to continue order on phone

        Returns:
            QR code image bytes (PNG format)

        Raises:
            ValueError: If qrcode library is not available
        """
        if not QRCODE_AVAILABLE:
            raise ValueError("qrcode library is required. Install with: pip install qrcode[pil]")

        # Build QR data URL
        qr_data = f"{order_url}?session={session_id}"

        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        # Create PIL image
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert to bytes
        img_buffer = io.BytesIO()
        qr_img.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        logger.info(f"Generated phone handoff QR code for session {session_id[:8]}...")

        return img_buffer.read()

    def upload_qr_to_s3(self, qr_bytes: bytes, key_prefix: str = "virtual-keys/qr") -> str:
        """
        Upload QR code image to S3 and return public URL.

        Args:
            qr_bytes: QR code image bytes
            key_prefix: S3 key prefix

        Returns:
            S3 public URL for the QR code image

        Note:
            This is a placeholder implementation. In production, use boto3 to upload to S3.
        """
        # TODO: Implement real S3 upload using boto3
        # For now, return a mock URL
        # In production:
        # import boto3
        # s3_client = boto3.client('s3', ...)
        # s3_key = f"{key_prefix}/{uuid.uuid4()}.png"
        # s3_client.put_object(Bucket=settings.AWS_S3_BUCKET, Key=s3_key, Body=qr_bytes, ContentType='image/png')
        # return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{s3_key}"

        logger.warning("S3 upload not implemented - returning mock URL")
        s3_key = f"{key_prefix}/{uuid.uuid4()}.png"
        return f"https://mock-s3.example.com/{s3_key}"


# Singleton instance
_qr_service: Optional[VirtualKeyQRService] = None


def get_qr_service() -> VirtualKeyQRService:
    """Get singleton QR service instance."""
    global _qr_service
    if _qr_service is None:
        _qr_service = VirtualKeyQRService()
    return _qr_service
