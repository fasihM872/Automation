"""Sending channels: EmailSender (SMTP) and WhatsAppSender (Twilio, optional)."""
import os
import re
import smtplib
import ssl
from email.utils import getaddresses
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def normalize_phone(raw, default_cc):
    """
    Best-effort conversion of a local number to E.164 (+<country><number>).
    Heuristic — verify a few results against your real data before a big run.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        return "+" + digits if len(digits) >= 8 else None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_cc + digits[1:]
    elif not digits.startswith(default_cc):
        digits = default_cc + digits
    return "+" + digits if len(digits) >= 8 else None


class EmailSender:
    """Opens one SMTP connection for the whole run (use as a context manager)."""

    def __init__(self, host, port, username, password, sender_name, sender_email, reply_to=None):
        if not host or not username or not password:
            raise RuntimeError("SMTP not configured. Fill SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD in .env")
        self.host, self.port = host, int(port)
        if "gmail.com" in host.lower():
            password = "".join(password.split())
        self.username, self.password = username, password
        self.sender_name, self.sender_email = sender_name, sender_email
        self.reply_to = reply_to or sender_email
        self._server = None

    def __enter__(self):
        self._server = smtplib.SMTP(self.host, self.port, timeout=30)
        self._server.starttls(context=ssl.create_default_context())
        try:
            self._server.login(self.username, self.password)
        except smtplib.SMTPAuthenticationError as exc:
            if "gmail.com" in self.host.lower():
                raise RuntimeError(
                    "Gmail rejected the SMTP login. Use a Gmail App Password, not your normal "
                    "Google password: enable 2-Step Verification, create an App Password for Mail, "
                    "then put the 16-character password in SMTP_PASSWORD. Also make sure "
                    "SMTP_USERNAME, SENDER_EMAIL, and REPLY_TO are the same Gmail address."
                ) from exc
            raise RuntimeError(
                "SMTP login failed. Check SMTP_USERNAME, SMTP_PASSWORD, SMTP_HOST, and SMTP_PORT."
            ) from exc
        return self

    def __exit__(self, *exc):
        if self._server:
            try:
                self._server.quit()
            finally:
                self._server = None

    @staticmethod
    def _parse_recipients(value):
        if not value:
            return []
        if isinstance(value, str):
            value = [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
        return [email for _, email in getaddresses(value) if email]

    @staticmethod
    def _attach_inline_images(root, inline_images):
        for cid, path in inline_images:
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            subtype = {"jpg": "jpeg"}.get(ext, ext) or "png"
            with open(path, "rb") as fh:
                img = MIMEImage(fh.read(), _subtype=subtype)
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=os.path.basename(path))
            root.attach(img)

    @staticmethod
    def _attach_files(root, attachments):
        for path in attachments:
            with open(path, "rb") as fh:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(path))
            root.attach(part)

    def send(self, to_email, subject, html_body, text_body, inline_images=None, attachments=None, bcc=None):
        inline_images = inline_images or []
        attachments = attachments or []
        bcc = self._parse_recipients(bcc)

        # text + html alternatives
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(text_body, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))

        if inline_images:
            # multipart/related wraps the alternative part plus the embedded images
            msg = MIMEMultipart("related")
            msg.attach(alternative)
            self._attach_inline_images(msg, inline_images)
        else:
            msg = alternative

        if attachments:
            mixed = MIMEMultipart("mixed")
            mixed.attach(msg)
            self._attach_files(mixed, attachments)
            msg = mixed

        msg["Subject"] = subject
        msg["From"] = f"{self.sender_name} <{self.sender_email}>"
        msg["To"] = to_email
        msg["Reply-To"] = self.reply_to
        return self._server.sendmail(self.sender_email, [to_email, *bcc], msg.as_string())


class WhatsAppSender:
    """
    Sends WhatsApp messages through Twilio's API. Optional — only created if the
    TWILIO_* values are set in .env. See the README for the setup + template-approval rules.
    """

    def __init__(self, account_sid, auth_token, from_number, default_cc):
        self.from_number = from_number          # e.g. "whatsapp:+14155238886"
        self.default_cc = default_cc
        try:
            from twilio.rest import Client
        except ImportError as e:
            raise RuntimeError("The 'twilio' package is not installed. Run: pip install twilio") from e
        self._client = Client(account_sid, auth_token)

    def send(self, to_phone_raw, body, media_url=None):
        number = normalize_phone(to_phone_raw, self.default_cc)
        if not number:
            raise ValueError(f"Could not parse phone number: {to_phone_raw!r}")
        kwargs = {"from_": self.from_number, "to": f"whatsapp:{number}", "body": body}
        if media_url:
            kwargs["media_url"] = [media_url]
        self._client.messages.create(**kwargs)
        return number
