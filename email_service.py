# ==============================================================================
# email_service.py  —  ReelKart Theatres  |  Booking confirmation emails
#
# Sends via SendGrid's HTTPS API (not raw SMTP) — simpler to set up and
# nothing to configure beyond an API key. If SENDGRID_API_KEY isn't set,
# sending is skipped gracefully (logged, not raised) so booking never fails
# because of email.
# ==============================================================================

import base64
import logging

from config import settings

logger = logging.getLogger(__name__)


def _build_confirmation_html(customer_id: str, info: dict) -> str:
    """A compact HTML email body — not the full receipt design, just a
    friendly summary. The full receipt is attached as a PDF.
    """
    seats = ", ".join(info.get("seats", []))
    name = info.get("first_name", "")
    return f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;background:#08080F;color:#F1F5F9;padding:32px;">
      <div style="max-width:520px;margin:0 auto;background:#161628;border-radius:16px;overflow:hidden;border:1px solid #2A2A50;">
        <div style="background:linear-gradient(135deg,#E03A50,#900020);padding:24px 28px;text-align:center;">
          <div style="font-size:20px;font-weight:800;">🎬 ReelKart Theatres</div>
          <div style="font-size:12px;opacity:.85;margin-top:4px;">Booking Confirmed</div>
        </div>
        <div style="padding:28px;">
          <p style="margin:0 0 16px;">Hi {name}, your tickets are confirmed! 🎉</p>
          <table style="width:100%;font-size:13px;border-collapse:collapse;">
            <tr><td style="color:#94A3B8;padding:6px 0;">Booking ID</td><td style="text-align:right;font-weight:700;color:#FBBF24;">{customer_id}</td></tr>
            <tr><td style="color:#94A3B8;padding:6px 0;">Movie</td><td style="text-align:right;font-weight:700;">{info.get('movie','')}</td></tr>
            <tr><td style="color:#94A3B8;padding:6px 0;">Cinema</td><td style="text-align:right;">{info.get('cinema_name','')}</td></tr>
            <tr><td style="color:#94A3B8;padding:6px 0;">Date &amp; time</td><td style="text-align:right;">{info.get('show_date','')} · {info.get('show_time','')}</td></tr>
            <tr><td style="color:#94A3B8;padding:6px 0;">Seats</td><td style="text-align:right;">{seats}</td></tr>
            <tr><td style="color:#94A3B8;padding:6px 0;">Total paid</td><td style="text-align:right;font-weight:800;color:#22C55E;">₹{info.get('total_amount',0):,}</td></tr>
          </table>
          <p style="margin:20px 0 0;font-size:12px;color:#4B5580;">
            Your full ticket with QR code is attached as a PDF — show it at the entrance.
          </p>
        </div>
      </div>
    </div>
    """


def send_booking_confirmation_email(
    to_email: str, customer_id: str, info: dict, pdf_bytes: bytes | None = None
) -> bool:
    """Send a booking confirmation email with the PDF ticket attached.
    Returns True if sent, False if skipped/failed — never raises, so a
    booking never fails because email delivery had a problem.
    """
    if not settings.SENDGRID_API_KEY:
        logger.warning(
            "SENDGRID_API_KEY not set — skipping confirmation email for booking %s. "
            "Set SENDGRID_API_KEY and SENDGRID_FROM_EMAIL in .env to enable this.",
            customer_id,
        )
        return False

    try:
        import sendgrid
        from sendgrid.helpers.mail import (
            Mail, Attachment, FileContent, FileName, FileType, Disposition, From,
        )

        sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=From(settings.SENDGRID_FROM_EMAIL, settings.SENDGRID_FROM_NAME),
            to_emails=to_email,
            subject=f"Your ReelKart Theatres booking is confirmed — {info.get('movie', '')}",
            html_content=_build_confirmation_html(customer_id, info),
        )

        if pdf_bytes:
            message.attachment = Attachment(
                FileContent(base64.b64encode(pdf_bytes).decode()),
                FileName(f"ReelKart_Ticket_{customer_id}.pdf"),
                FileType("application/pdf"),
                Disposition("attachment"),
            )

        response = sg.send(message)
        logger.info(
            "Confirmation email sent to %s for booking %s (SendGrid status %s)",
            to_email, customer_id, response.status_code,
        )
        return True

    except Exception as e:
        logger.error("Failed to send confirmation email for booking %s: %s", customer_id, e)
        return False
