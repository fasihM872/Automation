"""
Central configuration.

To run a NEW niche later:
  1. Add an entry to NICHES below (sheet path + 3 templates + the pitch wording).
  2. Set ACTIVE_NICHE to that key  (or pass  --niche <key>  on the command line).
Nothing else in the code needs to change.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# ── General sending behaviour ────────────────────────────────────────────────
SEND_DELAY_SECONDS = 8        # pause between each business (helps avoid spam filters / rate limits)
MAX_PER_RUN = None            # set to e.g. 50 to cap one run; None = no cap (also see --limit)
DEFAULT_COUNTRY_CODE = "92"   # turns local numbers into +<code><number> for WhatsApp. 92 = Pakistan.

# Sent-history log. Prevents double-sending if you re-run, and records who got which template.
SENT_LOG = DATA_DIR / "sent_log.csv"

# ── How spreadsheet columns are recognised ───────────────────────────────────
# Each logical field maps to header names to look for (case-insensitive, partial match).
# If your sheet uses different headers, just add them here.
COLUMN_MAP = {
    "name":    ["business name", "business", "company", "name"],
    "email":   ["email", "e-mail", "mail"],
    "phone":   ["whatsapp", "mobile", "phone", "number", "contact"],
    "address": ["address", "location", "addr"],
}

# ── Niches ───────────────────────────────────────────────────────────────────
# A "template" is one website demo you want to pitch. Host each demo somewhere public
# (GitHub Pages / Netlify / Cloudflare Pages / Vercel are free) and put its live URL in `url`.
# `preview_image` is optional — a screenshot of the template shown inside the email.
#   Put a file in  assets/  (e.g. "assets/dentist-1.png") and it's embedded in the email,
#   or use a full https:// URL. Leave "" for no image. Missing files are skipped silently.
#
# Placeholders you can use in email_subject / email_intro / whatsapp_message:
#   {business_name}  {niche}  {template_name}  {template_url}
NICHES = {
    "solar": {
        "sheet": DATA_DIR / "leads_solar.csv",
        "templates": [
            {
                "name": "FRZ Energy Solar Solutions",
                "url": "https://www.frzenergy.store",
                "preview_image": "assets/frz-energy-solar.jpeg",
            },
        ],
        "email_subject": "Top solar brands under one roof",
        "email_intro": (
            "Hi {business_name},\n\n"
            "FRZ Energy offers residential, commercial, and industrial solar solutions "
            "with trusted brands including GoodWe, Huawei, Solis, Sunwoda, Sofar, Jinko, "
            "Canadian Solar, Longi, and JA Solar.\n\n"
            "You can view the details here:"
        ),
        "whatsapp_message": (
            "Hi {business_name}, FRZ Energy offers residential, commercial, and industrial "
            "solar solutions with top brands under one roof.\n\n"
            "GoodWe, Huawei, Solis, Sunwoda, Sofar, Jinko, Canadian Solar, Longi, and JA Solar.\n\n"
            "Visit: {template_url}\nCall/WhatsApp: 0333-4541022\n\n"
            "Reply STOP to opt out."
        ),
    },
    "dentists": {
        "sheet": DATA_DIR / "leads_sample.xlsx",   # <-- replace with your real sheet, or pass --sheet
        "templates": [
            {"name": "Clean Clinic",  "url": "https://your-demo-host.example/dentist-1", "preview_image": "assets/dentist-1.png"},
            {"name": "Modern Smile",  "url": "https://your-demo-host.example/dentist-2", "preview_image": "assets/dentist-2.png"},
            {"name": "Family Dental", "url": "https://your-demo-host.example/dentist-3", "preview_image": "assets/dentist-3.png"},
        ],
        "email_subject": "A free website preview for {business_name}",
        "email_intro": (
            "Hi {business_name} team,\n\n"
            "I build websites for {niche} and put together a demo I think would suit your "
            "business — no cost and no obligation to look. Here it is:"
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for {niche} and made a quick demo for you: "
            "{template_url}\n\nIf you like it, reply here and I can build your site. "
            "Reply STOP to opt out."
        ),
    },
}

ACTIVE_NICHE = "solar"
