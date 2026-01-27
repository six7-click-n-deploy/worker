"""
Email Notification Service
Sends deployment notifications via SMTP (Gmail)
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import settings
from ..utils.logger import LogCategory, get_logger

logger = get_logger(__name__)


class EmailService:
    """Service for sending email notifications"""

    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
        self.from_name = settings.SMTP_FROM_NAME

    def send_email(self, to_emails: list[str], subject: str, body: str, html_body: str = None) -> bool:
        """
        Send email via Gmail SMTP

        Args:
            to_emails: List of recipient email addresses
            subject: Email subject
            body: Plain text email body
            html_body: Optional HTML email body

        Returns:
            bool: True if sent successfully
        """
        if not to_emails:
            logger.warning("No recipients provided", category=LogCategory.WARNING)
            return False

        if not self.user or not self.password:
            logger.warning("SMTP credentials not configured, skipping email", category=LogCategory.WARNING)
            return False

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = ", ".join(to_emails)

            # Attach plain text
            msg.attach(MIMEText(body, "plain"))

            # Attach HTML if provided
            if html_body:
                msg.attach(MIMEText(html_body, "html"))

            # Send via Gmail SMTP
            with smtplib.SMTP(self.host, self.port) as server:
                server.starttls()  # Upgrade to TLS
                server.login(self.user, self.password)
                server.send_message(msg)

            logger.success(
                f"Email sent to {len(to_emails)} recipient(s)", category=LogCategory.STATUS, recipients=to_emails
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}", category=LogCategory.ERROR, error=str(e))
            return False


# Singleton instance
email_service = EmailService()
