"""Turn a business + an assigned template into a ready-to-send email and WhatsApp message."""
from dataclasses import dataclass, field
from pathlib import Path
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

import config

_env = Environment(
    loader=FileSystemLoader(str(config.BASE_DIR / "email_templates")),
    autoescape=select_autoescape(["html"]),
)

_PREVIEW_CID = "tplpreview"
_URL_RE = re.compile(r"(https?://[^\s<]+)")


@dataclass
class Message:
    subject: str
    html_body: str
    text_body: str                      # plain-text alternative part of the email
    whatsapp_text: str
    template_name: str
    template_url: str
    inline_images: list = field(default_factory=list)   # [(content_id, filepath)] embedded in the email


def _fill(text, business, template, niche_name):
    return text.format(
        business_name=business.name,
        niche=niche_name,
        template_name=template["name"],
        template_url=template["url"],
    )


def _linkify(text):
    parts = []
    last = 0
    for match in _URL_RE.finditer(text):
        parts.append(escape(text[last : match.start()]))
        url = match.group(0).rstrip(".,)")
        trailing = match.group(0)[len(url) :]
        safe_url = escape(url)
        parts.append(Markup(f'<a href="{safe_url}" target="_blank" rel="noopener">{safe_url}</a>'))
        parts.append(escape(trailing))
        last = match.end()
    parts.append(escape(text[last:]))
    return Markup("").join(parts)


def _resolve_preview(template):
    """
    Decide how to show the template screenshot in the email.
      - a URL (http/https)        -> used directly as the <img src>
      - a local file that exists  -> embedded inline via cid: (works in email clients)
      - missing / not found yet   -> no image (the email still sends fine)
    Returns (preview_src, inline_images).
    """
    raw = (template.get("preview_image") or "").strip()
    if not raw:
        return "", []
    if raw.lower().startswith(("http://", "https://")):
        return raw, []
    path = Path(raw)
    if not path.is_absolute():
        path = config.BASE_DIR / path
    if path.exists():
        return f"cid:{_PREVIEW_CID}", [(_PREVIEW_CID, str(path))]
    return "", []   # local path given but file isn't there yet — skip rather than break the email


def build_message(business, template, niche_cfg, niche_name, sender_name, sender_email, tracking_pixel_url=""):
    subject = _fill(niche_cfg["email_subject"], business, template, niche_name)
    intro = _fill(niche_cfg["email_intro"], business, template, niche_name)
    whatsapp_text = _fill(niche_cfg["whatsapp_message"], business, template, niche_name)

    preview_src, inline_images = _resolve_preview(template)

    html_body = _env.get_template("pitch_email.html").render(
        intro_paragraphs=[_linkify(p.strip()) for p in intro.split("\n\n") if p.strip()],
        template_name=template["name"],
        template_url=template["url"],
        preview_src=preview_src,
        sender_name=sender_name,
        sender_email=sender_email,
        tracking_pixel_url=tracking_pixel_url,
    )

    text_body = (
        f"{intro}\n\n"
        f"{template['name']}: {template['url']}\n\n"
        "If it looks like a fit, just reply to this email and I'll get your site built.\n\n"
        f"- {sender_name}\n{sender_email}\n\n"
        "Reply STOP and I won't contact you again."
    )

    return Message(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        whatsapp_text=whatsapp_text,
        template_name=template["name"],
        template_url=template["url"],
        inline_images=inline_images,
    )
