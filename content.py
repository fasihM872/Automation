"""Turn a business + an assigned template into a ready-to-send email and WhatsApp message."""
from dataclasses import dataclass, field
from pathlib import Path
import re
from html.parser import HTMLParser

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

import config

_env = Environment(
    loader=FileSystemLoader(str(config.BASE_DIR / "email_templates")),
    autoescape=select_autoescape(["html"]),
)

_PREVIEW_CID = "tplpreview"
_URL_RE = re.compile(r"(https?://[^\s<]+)")
_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")
_SAMPLE_BUSINESS_RE = re.compile(r"(?<![\w/@.-])musharp(?![\w@.-])")
_NICHE_THEMES = {
    "dentists": {
        "label": "Dental Website Preview",
        "heading": "A Website Built For Patient Trust",
        "accent": "#1877a8",
        "footer_name": "Fasih Jamal",
        "footer_role": "Business Manager",
    },
    "plumber": {
        "label": "Plumbing Website Preview",
        "heading": "A Fast Website For Local Service Calls",
        "accent": "#0d7c72",
        "footer_name": "Fasih Jamal",
        "footer_role": "Business Manager",
    },
    "hospital": {
        "label": "Healthcare Website Preview",
        "heading": "A Clear Website For Patient Access",
        "accent": "#285f96",
        "footer_name": "Fasih Jamal",
        "footer_role": "Business Manager",
    },
    "care_homes": {
        "label": "Care Home Website Preview",
        "heading": "A Warm Website For Family Enquiries",
        "accent": "#7b5c8d",
        "footer_name": "Fasih Jamal",
        "footer_role": "Business Manager",
    },
    "pharmacy": {
        "label": "Pharmacy Website Preview",
        "heading": "A Clean Website For Local Customers",
        "accent": "#2f7d5c",
        "footer_name": "Fasih Jamal",
        "footer_role": "Business Manager",
    },
}


@dataclass
class Message:
    subject: str
    html_body: str
    text_body: str                      # plain-text alternative part of the email
    whatsapp_text: str
    template_name: str
    template_url: str
    inline_images: list = field(default_factory=list)   # [(content_id, filepath)] embedded in the email
    attachments: list = field(default_factory=list)     # file paths attached to the email


def _fill(text, business, template, niche_name):
    replacements = {
        "{business_name}": business.name,
        "{niche}": niche_name,
        "{template_name}": template["name"],
        "{template_url}": template["url"],
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    return _SAMPLE_BUSINESS_RE.sub(business.name, text)


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


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")


def _looks_like_html(text):
    return bool(_TAG_RE.search(text or ""))


def _html_to_text(html):
    parser = _HTMLTextExtractor()
    parser.feed(html)
    text = " ".join(part if part == "\n" else part for part in parser.parts)
    return re.sub(r"\n\s+", "\n", text).strip()


def _resolve_preview(template):
    """
    Decide how to show the template screenshot in the email.
      - a URL (http/https)        -> not embedded in the body
      - a local file that exists  -> attached to the email (supports comma-separated list of multiple files)
      - missing / not found yet   -> no image (the email still sends fine)
    Returns (preview_src, inline_images, attachments).
    """
    raw = (template.get("preview_image") or "").strip()
    if not raw or raw.lower().startswith(("http://", "https://")):
        return "", [], []
    
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    attachments = []
    for part in parts:
        path = Path(part)
        if not path.is_absolute():
            path = config.BASE_DIR / path
        if path.exists():
            attachments.append(str(path))
    return "", [], attachments


def build_message(business, template, niche_cfg, niche_name, sender_name, sender_email, tracking_pixel_url=""):
    subject = _fill(niche_cfg["email_subject"], business, template, niche_name)
    intro = _fill(niche_cfg["email_intro"], business, template, niche_name)
    whatsapp_text = _fill(niche_cfg["whatsapp_message"], business, template, niche_name)
    intro_html = Markup(intro) if _looks_like_html(intro) else ""
    intro_paragraphs = [] if intro_html else [_linkify(p.strip()) for p in intro.split("\n\n") if p.strip()]

    preview_src, inline_images, attachments = _resolve_preview(template)
    html_body = _env.get_template("pitch_email.html").render(
        intro_html=intro_html,
        intro_paragraphs=intro_paragraphs,
        template_name=template["name"],
        template_url=template["url"],
        preview_src=preview_src,
        sender_name=sender_name,
        sender_email=sender_email,
        tracking_pixel_url=tracking_pixel_url,
    )

    # Automatically scan html_body for any inline image CIDs and resolve them from the assets directory
    cids = re.findall(r'src="cid:([^"]+)"', html_body)
    for cid in set(cids):
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            img_path = config.BASE_DIR / "assets" / f"{cid}{ext}"
            if img_path.exists():
                inline_images.append((cid, str(img_path)))
                break

    text_intro = _html_to_text(intro) if intro_html else intro
    text_body = text_intro

    return Message(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        whatsapp_text=whatsapp_text,
        template_name=template["name"],
        template_url=template["url"],
        inline_images=inline_images,
        attachments=attachments,
    )
