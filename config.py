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
DEFAULT_COUNTRY_CODE = "44"   # turns local UK numbers into +44... for WhatsApp.

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
    "dentists": {
        "sheet": DATA_DIR / "leads_dentists.csv",
        "templates": [
            {"name": "Clean Clinic",  "url": "https://your-demo-host.example/dentist-1", "preview_image": "assets/dentist-1.png"},
            {"name": "Modern Smile",  "url": "https://your-demo-host.example/dentist-2", "preview_image": "assets/dentist-2.png"},
            {"name": "Family Dental", "url": "https://your-demo-host.example/dentist-3", "preview_image": "assets/dentist-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for {niche} and made a quick demo for you: "
            "{template_url}\n\nIf you like it, reply here and I can build your site. "
            "Reply STOP to opt out."
        ),
    },
    "plumber": {
        "sheet": DATA_DIR / "leads_plumber.csv",
        "templates": [
            {"name": "Rapid Plumbing", "url": "https://your-demo-host.example/plumber-1", "preview_image": "assets/plumber-1.png"},
            {"name": "Local Pipe Pros", "url": "https://your-demo-host.example/plumber-2", "preview_image": "assets/plumber-2.png"},
            {"name": "Emergency Plumber", "url": "https://your-demo-host.example/plumber-3", "preview_image": "assets/plumber-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for plumbing businesses and made a quick demo: "
            "{template_url}\n\nIt can include emergency call buttons, service areas, reviews, "
            "and booking/contact forms. Reply STOP to opt out."
        ),
    },
    "hospital": {
        "sheet": DATA_DIR / "leads_hospital.csv",
        "templates": [
            {"name": "CarePoint Hospital", "url": "https://your-demo-host.example/hospital-1", "preview_image": "assets/hospital-1.png"},
            {"name": "Modern Medical Center", "url": "https://your-demo-host.example/hospital-2", "preview_image": "assets/hospital-2.png"},
            {"name": "City Health Clinic", "url": "https://your-demo-host.example/hospital-3", "preview_image": "assets/hospital-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for hospitals and clinics. Here is a demo: "
            "{template_url}\n\nIt can include departments, doctors, appointment requests, "
            "maps, timings, and contact details. Reply STOP to opt out."
        ),
    },
    "care_homes": {
        "sheet": DATA_DIR / "leads_care_homes.csv",
        "templates": [
            {"name": "Warm Haven Care", "url": "https://your-demo-host.example/care-home-1", "preview_image": "assets/care-home-1.png"},
            {"name": "Family Care Residence", "url": "https://your-demo-host.example/care-home-2", "preview_image": "assets/care-home-2.png"},
            {"name": "Senior Living Home", "url": "https://your-demo-host.example/care-home-3", "preview_image": "assets/care-home-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for care homes and senior living services. "
            "Here is a demo: {template_url}\n\nIt can include care services, rooms, gallery, "
            "family enquiries, and visit booking. Reply STOP to opt out."
        ),
    },
    "pharmacy": {
        "sheet": DATA_DIR / "leads_pharmacy.csv",
        "templates": [
            {"name": "Neighbourhood Pharmacy", "url": "https://your-demo-host.example/pharmacy-1", "preview_image": "assets/pharmacy-1.png"},
            {"name": "QuickMeds Pharmacy", "url": "https://your-demo-host.example/pharmacy-2", "preview_image": "assets/pharmacy-2.png"},
            {"name": "HealthPlus Chemist", "url": "https://your-demo-host.example/pharmacy-3", "preview_image": "assets/pharmacy-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for pharmacies and made a quick demo: "
            "{template_url}\n\nIt can include opening hours, services, prescription/refill info, "
            "delivery, and contact buttons. Reply STOP to opt out."
        ),
    },
    "gym": {
        "sheet": DATA_DIR / "leads_gym.csv",
        "templates": [
            {"name": "Fit Studio", "url": "https://your-demo-host.example/gym-1", "preview_image": "assets/gym-1.png"},
            {"name": "Strength Club", "url": "https://your-demo-host.example/gym-2", "preview_image": "assets/gym-2.png"},
            {"name": "Wellness Gym", "url": "https://your-demo-host.example/gym-3", "preview_image": "assets/gym-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for gyms and fitness studios. Here is a demo: "
            "{template_url}\n\nIt can include class schedules, memberships, trainer profiles, "
            "lead capture, and contact buttons. Reply STOP to opt out."
        ),
    },
    "barber": {
        "sheet": DATA_DIR / "leads_barber.csv",
        "templates": [
            {"name": "Sharp Cuts", "url": "https://your-demo-host.example/barber-1", "preview_image": "assets/barber-1.png"},
            {"name": "Modern Barber", "url": "https://your-demo-host.example/barber-2", "preview_image": "assets/barber-2.png"},
            {"name": "Classic Grooming", "url": "https://your-demo-host.example/barber-3", "preview_image": "assets/barber-3.png"},
        ],
        "email_subject": "A mobile-friendly website preview for {business_name}",
        "email_intro": (
            "Hi, I came across {business_name} and put together a few homepage designs "
            "for you - already built, not rough sketches.\n\n"
            "Every design also comes with a free AI chatbot built in, so customers can "
            "get quick answers around the clock.\n\n"
            "If one catches your eye, reply with the one you like and we can get started."
        ),
        "whatsapp_message": (
            "Hi {business_name}! I build websites for barber shops and grooming studios. "
            "Here is a demo: {template_url}\n\nIt can include services, pricing, gallery, "
            "booking/contact buttons, and opening hours. Reply STOP to opt out."
        ),
    },
}

ACTIVE_NICHE = "dentists"
