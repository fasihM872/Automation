"""Web dashboard for the promotion sender."""
import csv
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, Response, send_from_directory, url_for
from werkzeug.utils import secure_filename

import config
from content import build_message
from main import load_leads, load_sent, validate_setup
from senders import normalize_phone


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "local-dashboard")

UPLOAD_DIR = config.DATA_DIR / "uploads"
IMAGE_DIR = config.DATA_DIR / "email_images"
RUN_DIR = config.DATA_DIR / "run_queue"
TRACKING_LOG = config.DATA_DIR / "email_tracking.csv"
OPEN_LOG = config.DATA_DIR / "email_opens.csv"
ALLOWED_LEAD_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DAILY_EMAIL_LIMIT = 5
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
    b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(config.BASE_DIR))
    except ValueError:
        return str(path)


def _read_sent_rows():
    if not config.SENT_LOG.exists():
        return []
    with open(config.SENT_LOG, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_csv_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _append_open(tracking_id):
    OPEN_LOG.parent.mkdir(parents=True, exist_ok=True)
    exists = OPEN_LOG.exists()
    with open(OPEN_LOG, "a", newline="", encoding="utf-8") as fh:
        fieldnames = ["tracking_id", "opened_at", "ip", "user_agent"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "tracking_id": tracking_id,
                "opened_at": datetime.now().isoformat(timespec="seconds"),
                "ip": request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
                "user_agent": request.headers.get("User-Agent", ""),
            }
        )


def _tracking_base_url():
    configured = os.getenv("TRACKING_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return request.url_root.rstrip("/")


def _tracking_is_public():
    base_url = _tracking_base_url().lower()
    local_parts = ("127.0.0.1", "localhost", "0.0.0.0")
    return base_url.startswith("https://") and not any(part in base_url for part in local_parts)


def _result_rows():
    tracking_rows = _read_csv_rows(TRACKING_LOG)
    open_rows = _read_csv_rows(OPEN_LOG)
    opens_by_id = {}
    for row in open_rows:
        opens_by_id.setdefault(row.get("tracking_id", ""), []).append(row)

    rows = []
    tracked_keys = set()
    for row in tracking_rows:
        tracking_id = row.get("tracking_id", "")
        opens = opens_by_id.get(tracking_id, [])
        tracked_keys.add((row.get("niche", ""), row.get("email", ""), row.get("phone", "")))
        rows.append(
            {
                **row,
                "tracking": "enabled",
                "open_count": len(opens),
                "first_opened_at": opens[0].get("opened_at", "") if opens else "",
                "last_opened_at": opens[-1].get("opened_at", "") if opens else "",
                "opened": bool(opens),
            }
        )

    for row in _read_sent_rows():
        key = (row.get("niche", ""), row.get("email", ""), row.get("phone", ""))
        if row.get("email_status") != "sent" or key in tracked_keys:
            continue
        rows.append(
            {
                "tracking_id": "",
                "sent_at": row.get("sent_at", ""),
                "niche": row.get("niche", ""),
                "name": row.get("name", ""),
                "email": row.get("email", ""),
                "phone": row.get("phone", ""),
                "template_name": row.get("template_name", ""),
                "template_url": row.get("template_url", ""),
                "subject": "",
                "tracking": "not enabled",
                "open_count": 0,
                "first_opened_at": "",
                "last_opened_at": "",
                "opened": False,
            }
        )

    return sorted(rows, key=lambda item: item.get("sent_at", ""), reverse=True)


def _find_sent_row(row_id):
    for index, row in enumerate(_read_sent_rows()):
        if str(index) == str(row_id) and row.get("email_status") == "sent":
            return row
    return None


def _sent_email_rows():
    rows = [
        {**row, "row_id": index}
        for index, row in enumerate(_read_sent_rows())
        if row.get("email_status") == "sent" and row.get("email")
    ]
    rows.reverse()
    return rows


def _campaign_upload_dir(niche_name):
    return UPLOAD_DIR / secure_filename(niche_name)


def _campaign_image_dir(niche_name):
    return IMAGE_DIR / secure_filename(niche_name)


def _lead_key(niche_name, lead):
    return (niche_name, lead.email, lead.phone)


def _sent_today_count(niche_name, sent_rows):
    today = date.today().isoformat()
    count = 0
    for row in sent_rows:
        if row.get("niche") != niche_name or row.get("email_status") != "sent":
            continue
        if (row.get("sent_at") or "").startswith(today):
            count += 1
    return count


def _draft_text(text, business, template, niche_name):
    if not business or not template:
        return text
    try:
        return text.format(
            business_name=business.name,
            niche=niche_name,
            template_name=template["name"],
            template_url=template["url"],
        )
    except (KeyError, ValueError):
        return text


def _write_single_lead_sheet(niche_name, business):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(business.name) or "lead"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RUN_DIR / f"{niche_name}-{safe_name}-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["business name", "email", "phone", "address"])
        writer.writeheader()
        writer.writerow(
            {
                "business name": business.name,
                "email": business.email,
                "phone": business.phone,
                "address": business.address,
            }
        )
    return path


def _write_single_lead_row(niche_name, row, prefix="followup"):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(row.get("name", "")) or "lead"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RUN_DIR / f"{prefix}-{niche_name}-{safe_name}-{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["business name", "email", "phone"])
        writer.writeheader()
        writer.writerow(
            {
                "business name": row.get("name", ""),
                "email": row.get("email", ""),
                "phone": row.get("phone", ""),
            }
        )
    return path


def _available_sheets(niche_name, niche_cfg):
    sheets = [
        {
            "label": f"Default: {_rel(niche_cfg['sheet'])}",
            "value": _rel(niche_cfg["sheet"]),
            "uploaded": False,
            "name": Path(niche_cfg["sheet"]).name,
        }
    ]
    upload_dir = _campaign_upload_dir(niche_name)
    if upload_dir.exists():
        for path in sorted(upload_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.suffix.lower() in ALLOWED_LEAD_EXTENSIONS:
                sheets.append(
                    {
                        "label": f"Uploaded: {path.name}",
                        "value": _rel(path),
                        "uploaded": True,
                        "name": path.name,
                    }
                )
    return sheets


def _available_images(niche_name):
    images = [
        {"label": "Campaign default image", "value": "__default__", "uploaded": False, "name": "Campaign default"},
        {"label": "No image", "value": "__none__", "uploaded": False, "name": "No image"},
    ]
    image_dir = _campaign_image_dir(niche_name)
    if image_dir.exists():
        for path in sorted(image_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS:
                images.append(
                    {
                        "label": f"Uploaded: {path.name}",
                        "value": _rel(path),
                        "uploaded": True,
                        "name": path.name,
                        "url": url_for("project_asset", filename=_rel(path).replace("\\", "/")),
                    }
                )
    return images


def _resolve_sheet(niche_name, niche_cfg, requested_sheet=None):
    default_sheet = Path(niche_cfg["sheet"])
    if not requested_sheet:
        return default_sheet

    candidate = Path(requested_sheet)
    if not candidate.is_absolute():
        candidate = config.BASE_DIR / candidate
    try:
        resolved = candidate.resolve()
        allowed = [default_sheet.resolve()]
        upload_dir = _campaign_upload_dir(niche_name)
        if upload_dir.exists():
            allowed.extend(
                path.resolve()
                for path in upload_dir.iterdir()
                if path.suffix.lower() in ALLOWED_LEAD_EXTENSIONS
            )
        if resolved in allowed:
            return resolved
    except OSError:
        pass
    return default_sheet


def _resolve_image(niche_name, requested_image=None):
    if not requested_image or requested_image == "__default__":
        return None
    if requested_image == "__none__":
        return ""

    candidate = Path(requested_image)
    if not candidate.is_absolute():
        candidate = config.BASE_DIR / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(_campaign_image_dir(niche_name).resolve())
        if resolved.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS and resolved.exists():
            return _rel(resolved)
    except (OSError, ValueError):
        pass
    return None


def _message_config(niche_name, niche_cfg, email_subject=None, email_intro=None, preview_image=None):
    copied = {
        **niche_cfg,
        "templates": [dict(template) for template in niche_cfg.get("templates", [])],
    }
    if email_subject:
        copied["email_subject"] = email_subject
    if email_intro:
        copied["email_intro"] = email_intro
    image = _resolve_image(niche_name, preview_image)
    if image is not None:
        for template in copied["templates"]:
            template["preview_image"] = image
    return copied


def _setup_status(niche_name, niche_cfg, sheet):
    hidden_warning_parts = (
        "Preview image not found",
        "Twilio is not configured",
    )
    errors = [
        error
        for error in validate_setup(niche_name, niche_cfg, sheet)
        if not any(part in error for part in hidden_warning_parts)
    ]
    return {
        "errors": errors,
        "email_ready": all(os.getenv(name) for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")),
        "whatsapp_ready": all(
            os.getenv(name) for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM")
        ),
    }


def _preview_image_url(template):
    raw = (template.get("preview_image") or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith(("http://", "https://")):
        return raw
    path = Path(raw)
    if not path.is_absolute():
        path = config.BASE_DIR / path
    if path.exists():
        return url_for("project_asset", filename=_rel(path).replace("\\", "/"))
    return ""


def _whatsapp_web_url(phone, text):
    number = normalize_phone(phone, config.DEFAULT_COUNTRY_CODE)
    if not number:
        return ""
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(text)}"


def _build_dashboard(niche_name, requested_sheet=None, email_subject=None, email_intro=None, preview_image=None):
    load_dotenv(override=True)
    base_niche_cfg = config.NICHES[niche_name]
    sheet = _resolve_sheet(niche_name, base_niche_cfg, requested_sheet)
    leads = list(load_leads(sheet)) if Path(sheet).exists() else []
    sent_keys = load_sent(config.SENT_LOG)
    sent_rows = _read_sent_rows()
    sent_today_count = _sent_today_count(niche_name, sent_rows)
    remaining_today = max(DAILY_EMAIL_LIMIT - sent_today_count, 0)
    pending_leads = [lead for lead in leads if _lead_key(niche_name, lead) not in sent_keys]
    daily_queue = pending_leads[:remaining_today]
    current_business = daily_queue[0] if daily_queue else None
    base_templates = base_niche_cfg.get("templates", [])
    current_template = base_templates[0] if base_templates else None
    draft_subject = email_subject or _draft_text(base_niche_cfg.get("email_subject", ""), current_business, current_template, niche_name)
    draft_intro = email_intro or _draft_text(base_niche_cfg.get("email_intro", ""), current_business, current_template, niche_name)
    niche_cfg = _message_config(niche_name, base_niche_cfg, draft_subject, draft_intro, preview_image)
    templates = niche_cfg.get("templates", [])

    sender_name = os.getenv("SENDER_NAME", "FRZ Energy")
    sender_email = os.getenv("SENDER_EMAIL", os.getenv("SMTP_USERNAME", "info@frzenergy.store"))
    sample_business = current_business
    sample_message = None
    if sample_business and templates:
        sample_message = build_message(sample_business, templates[0], niche_cfg, niche_name, sender_name, sender_email)

    enriched_leads = []
    for lead in leads:
        lead_message = None
        if templates:
            lead_message = build_message(lead, templates[0], niche_cfg, niche_name, sender_name, sender_email)
        enriched_leads.append(
            {
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "normalized_phone": normalize_phone(lead.phone, config.DEFAULT_COUNTRY_CODE) or lead.phone,
                "whatsapp_url": _whatsapp_web_url(lead.phone, lead_message.whatsapp_text) if lead_message else "",
                "sent": (niche_name, lead.email, lead.phone) in sent_keys,
                "current": current_business
                and lead.email == current_business.email
                and lead.phone == current_business.phone,
                "queued": any(lead.email == item.email and lead.phone == item.phone for item in daily_queue),
            }
        )

    last_run = sent_rows[-1]["sent_at"] if sent_rows else ""
    if last_run:
        try:
            last_run = datetime.fromisoformat(last_run).strftime("%b %d, %Y %I:%M %p")
        except ValueError:
            pass

    return {
        "active_niche": niche_name,
        "niches": sorted(config.NICHES.keys()),
        "niche": niche_cfg,
        "sheet": _rel(sheet),
        "available_sheets": _available_sheets(niche_name, niche_cfg),
        "available_images": _available_images(niche_name),
        "selected_image": preview_image or "__default__",
        "email_subject": niche_cfg.get("email_subject", ""),
        "email_intro": niche_cfg.get("email_intro", ""),
        "current_business": current_business,
        "daily_queue": daily_queue,
        "daily_limit": DAILY_EMAIL_LIMIT,
        "sent_today_count": sent_today_count,
        "remaining_today": remaining_today,
        "leads": enriched_leads,
        "templates": templates,
        "template_previews": {template["name"]: _preview_image_url(template) for template in templates},
        "sample_message": sample_message,
        "sample_business": sample_business,
        "sent_rows": list(reversed(sent_rows[-8:])),
        "stats": {
            "lead_count": len(leads),
            "template_count": len(templates),
            "sent_count": sum(1 for lead in leads if (niche_name, lead.email, lead.phone) in sent_keys),
            "today_count": sent_today_count,
            "last_run": last_run or "Never",
        },
        "setup": _setup_status(niche_name, niche_cfg, sheet),
    }


@app.route("/")
def dashboard():
    niche_name = request.args.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE
    return render_template(
        "dashboard.html",
        **_build_dashboard(niche_name, request.args.get("sheet"), preview_image=request.args.get("image")),
        run_result=None,
        upload_result=None,
    )


@app.post("/upload")
def upload_leads():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    uploaded = request.files.get("lead_file")
    if not uploaded or not uploaded.filename:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result=None,
            upload_result={"ok": False, "message": "Choose a CSV or Excel lead file first."},
        )

    original_name = secure_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_LEAD_EXTENSIONS:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result=None,
            upload_result={"ok": False, "message": "Lead files must be .csv, .xlsx, or .xlsm."},
        )

    upload_dir = _campaign_upload_dir(niche_name)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_path = upload_dir / f"{Path(original_name).stem}-{stamp}{suffix}"
    uploaded.save(saved_path)

    try:
        lead_count = len(list(load_leads(saved_path)))
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result=None,
            upload_result={"ok": False, "message": f"Could not read that file: {exc}"},
        )

    return redirect(url_for("dashboard", niche=niche_name, sheet=_rel(saved_path), uploaded=lead_count))


@app.post("/upload-image")
def upload_image():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    uploaded = request.files.get("email_image")
    if not uploaded or not uploaded.filename:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result=None,
            upload_result={"ok": False, "message": "Choose an email image first."},
        )

    original_name = secure_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result=None,
            upload_result={"ok": False, "message": "Images must be .jpg, .jpeg, .png, .webp, or .gif."},
        )

    image_dir = _campaign_image_dir(niche_name)
    image_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_path = image_dir / f"{Path(original_name).stem}-{stamp}{suffix}"
    uploaded.save(saved_path)
    return redirect(
        url_for(
            "dashboard",
            niche=niche_name,
            sheet=request.form.get("sheet"),
            image=_rel(saved_path),
            image_uploaded=saved_path.name,
        )
    )


@app.post("/delete-upload")
def delete_upload():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    sheet = request.form.get("sheet", "")
    path = _resolve_sheet(niche_name, config.NICHES[niche_name], sheet)
    try:
        path.resolve().relative_to(_campaign_upload_dir(niche_name).resolve())
    except ValueError:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, sheet),
            run_result=None,
            upload_result={"ok": False, "message": "Only uploaded lead files can be deleted."},
        )

    deleted_name = path.name
    path.unlink(missing_ok=True)
    return redirect(url_for("dashboard", niche=niche_name, deleted=deleted_name))


@app.post("/run")
def run_campaign():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    mode = request.form.get("mode", "dry-run")
    args = [sys.executable, "main.py", "--niche", niche_name]
    sheet = _resolve_sheet(niche_name, config.NICHES[niche_name], request.form.get("sheet"))
    leads = list(load_leads(sheet)) if sheet.exists() else []
    sent_keys = load_sent(config.SENT_LOG)
    sent_rows = _read_sent_rows()
    remaining_today = max(DAILY_EMAIL_LIMIT - _sent_today_count(niche_name, sent_rows), 0)
    pending_leads = [lead for lead in leads if _lead_key(niche_name, lead) not in sent_keys]
    current_business = pending_leads[0] if remaining_today and pending_leads else None
    if not current_business:
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, request.form.get("sheet")),
            run_result={
                "ok": False,
                "output": "No email sent. This campaign either reached today's 5-email limit or has no unsent businesses left.",
            },
            upload_result=None,
        )

    single_sheet = _write_single_lead_sheet(niche_name, current_business)
    args.extend(["--sheet", str(single_sheet), "--limit", "1"])

    limit = request.form.get("limit", "").strip()
    if limit and mode != "send-one":
        args.extend(["--limit", limit])
    if request.form.get("preview"):
        args.append("--preview")
    if request.form.get("resend"):
        args.append("--resend")
    if request.form.get("no_email"):
        args.append("--no-email")
    if request.form.get("no_whatsapp"):
        args.append("--no-whatsapp")

    email_subject = request.form.get("email_subject", "").strip()
    email_intro = request.form.get("email_intro", "").strip()
    preview_image = request.form.get("preview_image", "__default__")
    resolved_image = _resolve_image(niche_name, preview_image)
    if email_subject:
        args.extend(["--email-subject", email_subject])
    if email_intro:
        args.extend(["--email-intro", email_intro])
    if resolved_image is not None:
        if resolved_image:
            args.extend(["--preview-image", resolved_image])
        else:
            args.append("--no-preview-image")

    args.extend(["--tracking-base-url", _tracking_base_url()])

    if mode == "send":
        args.append("--send")
    else:
        args.append("--dry-run")

    completed = subprocess.run(
        args,
        cwd=config.BASE_DIR,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip()).strip()
    return render_template(
        "dashboard.html",
        **_build_dashboard(niche_name, request.form.get("sheet"), email_subject, email_intro, preview_image),
        run_result={"ok": completed.returncode == 0, "output": output or "Command completed with no output."},
        upload_result=None,
    )


@app.route("/assets/<path:filename>")
def project_asset(filename):
    return send_from_directory(config.BASE_DIR, filename)


@app.get("/results")
def results():
    rows = _result_rows()
    return render_template(
        "results.html",
        rows=rows,
        opened_count=sum(1 for row in rows if row["opened"]),
        tracked_count=sum(1 for row in rows if row["tracking"] == "enabled"),
        total_sent=len(rows),
        tracking_base_url=_tracking_base_url(),
        tracking_is_public=_tracking_is_public(),
    )


@app.get("/follow-ups")
def follow_ups():
    return render_template("follow_ups.html", rows=_sent_email_rows(), run_result=None)


@app.post("/follow-ups/send")
def send_follow_up():
    row = _find_sent_row(request.form.get("row_id", ""))
    if not row:
        return render_template(
            "follow_ups.html",
            rows=_sent_email_rows(),
            run_result={"ok": False, "output": "Could not find that previously sent email."},
        )

    niche_name = row.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    sheet = _write_single_lead_row(niche_name, row)
    subject = f"Following up with {row.get('name') or 'you'}"
    intro = (
        "Hi {business_name},\n\n"
        "I just wanted to follow up on my previous email in case it got missed.\n\n"
        "Here is the website/demo link again:"
    )
    args = [
        sys.executable,
        "main.py",
        "--niche",
        niche_name,
        "--sheet",
        str(sheet),
        "--limit",
        "1",
        "--send",
        "--resend",
        "--no-whatsapp",
        "--email-subject",
        subject,
        "--email-intro",
        intro,
        "--tracking-base-url",
        _tracking_base_url(),
    ]

    completed = subprocess.run(
        args,
        cwd=config.BASE_DIR,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip()).strip()
    return render_template(
        "follow_ups.html",
        rows=_sent_email_rows(),
        run_result={"ok": completed.returncode == 0, "output": output or "Command completed with no output."},
    )


@app.get("/track/open/<tracking_id>.gif")
def track_open(tracking_id):
    if tracking_id:
        _append_open(tracking_id)
    return Response(
        PIXEL_GIF,
        mimetype="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
