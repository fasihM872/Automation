"""Web dashboard for the promotion sender."""
import csv
import html
import imaplib
import email as email_parser
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values, load_dotenv
from flask import Flask, redirect, render_template, request, Response, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

import config
import db
from content import build_message
from main import Business, load_leads, load_sent, validate_setup
from senders import EmailSender, normalize_phone


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "local-dashboard")

UPLOAD_DIR = config.DATA_DIR / "uploads"
IMAGE_DIR = config.DATA_DIR / "email_images"
RUN_DIR = config.DATA_DIR / "run_queue"
TRACKING_LOG = config.DATA_DIR / "email_tracking.csv"
OPEN_LOG = config.DATA_DIR / "email_opens.csv"
IGNORED_RESPONSES_LOG = config.DATA_DIR / "ignored_responses.csv"
RESPONSES_CACHE_FILE = config.DATA_DIR / "responses_cache.json"
ALLOWED_LEAD_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DAILY_EMAIL_LIMIT = 5
LEGACY_CAMPAIGN_RE = re.compile(r"\b(frz|frzenergy|solar)\b", re.IGNORECASE)
LEAD_TABLE_LIMIT = 100
SENT_FIELDNAMES = [
    "sent_at",
    "niche",
    "name",
    "email",
    "phone",
    "template_name",
    "template_url",
    "email_status",
    "whatsapp_status",
]
TRACKING_FIELDNAMES = [
    "tracking_id",
    "sent_at",
    "niche",
    "name",
    "email",
    "phone",
    "template_name",
    "template_url",
    "subject",
]
OPEN_FIELDNAMES = ["tracking_id", "opened_at", "ip", "user_agent"]
IGNORED_RESPONSE_FIELDNAMES = ["response_id", "ignored_at", "from_email", "subject"]
MAX_RESPONSES = 50
RESPONSE_SCAN_LIMIT = 120
_RESPONSES_CACHE = {"rows": None, "error": "", "fetched_at": None}
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
    b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _command_env():
    env = os.environ.copy()
    env.update({key: value for key, value in dotenv_values(config.BASE_DIR / ".env").items() if value is not None})
    return env


def _env_value(name, default=""):
    return _command_env().get(name, default)


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(config.BASE_DIR))
    except ValueError:
        return str(path)


def _is_relative_to(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _read_sent_rows():
    try:
        db_rows = db.get_sent_rows()
        if db_rows:
            return [{**row, "_db_id": row.get("id", "")} for row in db_rows]
    except Exception:
        pass
    if not config.SENT_LOG.exists():
        return []
    with open(config.SENT_LOG, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_csv_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv_rows(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _ignored_response_ids():
    return {row.get("response_id", "") for row in _read_csv_rows(IGNORED_RESPONSES_LOG) if row.get("response_id")}


def _ignore_response(response_id, from_email="", subject=""):
    response_id = (response_id or "").strip()
    if not response_id:
        return False
    rows = _read_csv_rows(IGNORED_RESPONSES_LOG)
    if any(row.get("response_id") == response_id for row in rows):
        return True
    rows.append(
        {
            "response_id": response_id,
            "ignored_at": datetime.now().isoformat(timespec="seconds"),
            "from_email": from_email,
            "subject": subject,
        }
    )
    _write_csv_rows(IGNORED_RESPONSES_LOG, rows, IGNORED_RESPONSE_FIELDNAMES)
    _clear_responses_cache()
    return True


def _clear_responses_cache():
    _RESPONSES_CACHE.update({"rows": None, "error": "", "fetched_at": None})
    RESPONSES_CACHE_FILE.unlink(missing_ok=True)


def _store_responses_cache(rows, error, fetched_at):
    _RESPONSES_CACHE.update({"rows": rows, "error": error, "fetched_at": fetched_at})
    RESPONSES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESPONSES_CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "rows": rows,
                "error": error,
                "fetched_at": fetched_at.isoformat(timespec="seconds") if fetched_at else "",
            },
            fh,
            ensure_ascii=False,
        )


def _cached_responses():
    fetched_at = _RESPONSES_CACHE.get("fetched_at")
    rows = _RESPONSES_CACHE.get("rows")
    if rows is None and RESPONSES_CACHE_FILE.exists():
        try:
            with open(RESPONSES_CACHE_FILE, encoding="utf-8") as fh:
                payload = json.load(fh)
            rows = payload.get("rows") or []
            fetched_at_raw = payload.get("fetched_at") or ""
            fetched_at = datetime.fromisoformat(fetched_at_raw) if fetched_at_raw else None
            _RESPONSES_CACHE.update(
                {
                    "rows": rows,
                    "error": payload.get("error", ""),
                    "fetched_at": fetched_at,
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
    if rows is None or not fetched_at:
        return None
    return rows, _RESPONSES_CACHE.get("error", ""), fetched_at


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
    sent_rows = _read_sent_rows()
    sent_ids_by_key = {}
    for index, row in enumerate(sent_rows):
        key = (row.get("niche", ""), row.get("email", ""), row.get("phone", ""), row.get("template_url", ""))
        sent_ids_by_key.setdefault(key, index)

    tracking_rows = _read_csv_rows(TRACKING_LOG)
    open_rows = _read_csv_rows(OPEN_LOG)
    opens_by_id = {}
    for row in open_rows:
        opens_by_id.setdefault(row.get("tracking_id", ""), []).append(row)

    rows = []
    tracked_keys = set()
    for tracking_index, row in enumerate(tracking_rows):
        tracking_id = row.get("tracking_id", "")
        opens = opens_by_id.get(tracking_id, [])
        key = (row.get("niche", ""), row.get("email", ""), row.get("phone", ""))
        sent_key = (*key, row.get("template_url", ""))
        tracked_keys.add(key)
        rows.append(
            {
                **row,
                "tracking_row_id": tracking_index,
                "sent_row_id": sent_ids_by_key.get(sent_key, ""),
                "tracking": "enabled",
                "open_count": len(opens),
                "first_opened_at": opens[0].get("opened_at", "") if opens else "",
                "last_opened_at": opens[-1].get("opened_at", "") if opens else "",
                "opened": bool(opens),
            }
        )

    for sent_index, row in enumerate(sent_rows):
        key = (row.get("niche", ""), row.get("email", ""), row.get("phone", ""))
        if row.get("email_status") != "sent" or key in tracked_keys:
            continue
        rows.append(
            {
                "tracking_row_id": "",
                "sent_row_id": sent_index,
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


def _delete_sent_row(row_id):
    sent_rows = _read_sent_rows()
    try:
        index = int(row_id)
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= len(sent_rows):
        return None

    removed = sent_rows.pop(index)
    if removed.get("_db_id"):
        db.delete_sent_email(removed["_db_id"])
        _delete_related_tracking(removed)
        return removed
    _write_csv_rows(config.SENT_LOG, sent_rows, SENT_FIELDNAMES)
    _delete_related_tracking(removed)
    return removed


def _delete_related_tracking(sent_row):
    tracking_rows = _read_csv_rows(TRACKING_LOG)
    if not tracking_rows:
        return

    removed_tracking_ids = []
    kept_tracking = []
    target = (
        sent_row.get("niche", ""),
        sent_row.get("email", ""),
        sent_row.get("phone", ""),
        sent_row.get("template_url", ""),
    )
    for row in tracking_rows:
        key = (row.get("niche", ""), row.get("email", ""), row.get("phone", ""), row.get("template_url", ""))
        if key == target:
            if row.get("tracking_id"):
                removed_tracking_ids.append(row["tracking_id"])
        else:
            kept_tracking.append(row)
    _write_csv_rows(TRACKING_LOG, kept_tracking, TRACKING_FIELDNAMES)

    if removed_tracking_ids:
        open_rows = [row for row in _read_csv_rows(OPEN_LOG) if row.get("tracking_id") not in removed_tracking_ids]
        _write_csv_rows(OPEN_LOG, open_rows, OPEN_FIELDNAMES)


def _sent_email_rows():
    rows = [
        {**row, "row_id": index}
        for index, row in enumerate(_read_sent_rows())
        if row.get("email_status") == "sent" and row.get("email")
    ]
    rows.reverse()
    return rows


def _sent_contacts_by_email():
    contacts = {}
    for index, row in enumerate(_read_sent_rows()):
        if row.get("email_status") != "sent" or not row.get("email"):
            continue
        email = row["email"].strip().lower()
        contacts[email] = {**row, "row_id": index}
    return contacts


def _sent_response_contact_count():
    count = 0
    for row in _read_sent_rows():
        email = row.get("email", "").strip().lower()
        if row.get("email_status") == "sent" and email:
            count += 1
    return count


def _is_reply_message(message):
    subject = _decode_header(message.get("Subject", "")).strip().lower()
    return bool(message.get("In-Reply-To") or message.get("References") or subject.startswith("re:"))


def _own_email_addresses():
    emails = set()
    for name in ("SMTP_USERNAME", "SENDER_EMAIL", "REPLY_TO", "IMAP_USERNAME"):
        value = (_env_value(name) or "").strip().lower()
        _, parsed = parseaddr(value)
        if parsed:
            emails.add(parsed.lower())
        elif "@" in value:
            emails.add(value)
    return emails


def _decode_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, ValueError):
        return value


def _message_text(message):
    html_body = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain" and text.strip():
                return text.strip()
            if content_type == "text/html" and text.strip() and not html_body:
                html_body = text
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace").strip()
            except LookupError:
                return payload.decode("utf-8", errors="replace").strip()

    if html_body:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html_body)
        text = re.sub(r"(?s)<br\s*/?>", "\n", text)
        text = re.sub(r"(?s)</p\s*>", "\n\n", text)
        text = re.sub(r"(?s)<.*?>", "", text)
        return html.unescape(text).strip()
    return ""


def _is_legacy_campaign_text(*parts):
    return any(LEGACY_CAMPAIGN_RE.search(part or "") for part in parts)


def _response_since_date(contacts):
    dates = []
    for row in contacts.values():
        try:
            dates.append(datetime.fromisoformat(row.get("sent_at", "")).date())
        except ValueError:
            pass
    if not dates:
        return ""
    return min(dates).strftime("%d-%b-%Y")


def _fetch_responses(force_refresh=False):
    cached = None if force_refresh else _cached_responses()
    if cached:
        rows, error, fetched_at = cached
        return rows, error, fetched_at, True

    host = _env_value("IMAP_HOST").strip() if _env_value("IMAP_HOST") else None
    smtp_host = _env_value("SMTP_HOST", "").strip()
    if not host:
        if "zoho" in smtp_host.lower():
            host = "imap.zoho.com"
        elif "gmail" in smtp_host.lower():
            host = "imap.gmail.com"
        elif smtp_host.lower().startswith("smtp."):
            host = smtp_host.lower().replace("smtp.", "imap.", 1)
        else:
            host = "imap.gmail.com"

    provider = "Gmail"
    if "zoho" in host.lower():
        provider = "Zoho Mail"
    elif "gmail" in host.lower():
        provider = "Gmail"
    else:
        match = re.search(r"imap\.(?:mail\.)?([^.]+)\.", host.lower())
        if match:
            provider = match.group(1).capitalize()
        else:
            provider = "IMAP"

    if not force_refresh:
        return [], f"Click Refresh Inbox to check {provider} for new replies.", None, True

    contacts = _sent_contacts_by_email()
    if not contacts:
        if _sent_response_contact_count() == 0:
            return [], "No recipient replies yet. Sent emails from your own sender address are ignored.", None, False
        return [], "No sent recipient contacts found yet.", None, False

    username = _env_value("IMAP_USERNAME") or _env_value("SMTP_USERNAME")
    password = _env_value("IMAP_PASSWORD") or _env_value("SMTP_PASSWORD")
    own_emails = _own_email_addresses()
    ignored_ids = _ignored_response_ids()
    if "gmail.com" in host.lower():
        password = "".join(password.split())
    if not host or not username or not password:
        return [], f"IMAP is not configured. Add IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD or reuse your {provider} SMTP values.", None, False

    try:
        with imaplib.IMAP4_SSL(host, 993, timeout=15) as mailbox:
            mailbox.login(username, password)
            mailbox.select("INBOX")
            criteria = ["ALL"]
            since = _response_since_date(contacts)
            if since:
                criteria = ["SINCE", since]
            status, data = mailbox.uid("search", None, *criteria)
            if status != "OK":
                return [], "Could not search inbox responses.", None, False

            uids = data[0].split()
            responses = []
            for uid in reversed(uids[-RESPONSE_SCAN_LIMIT:]):
                response_id = uid.decode("ascii", errors="ignore")
                if response_id in ignored_ids:
                    continue
                status, fetched = mailbox.uid(
                    "fetch",
                    uid,
                    "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE IN-REPLY-TO REFERENCES)])",
                )
                if status != "OK" or not fetched:
                    continue
                raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
                if not raw:
                    continue
                header_message = email_parser.message_from_bytes(raw)
                from_name, from_email = parseaddr(header_message.get("From", ""))
                from_email = from_email.strip().lower()
                if from_email in own_emails and not _is_reply_message(header_message):
                    continue
                if from_email not in contacts:
                    continue
                status, fetched = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
                if not raw:
                    continue
                message = email_parser.message_from_bytes(raw)
                subject = _decode_header(header_message.get("Subject", "No subject"))
                sent_at = header_message.get("Date", "")
                try:
                    sent_at = parsedate_to_datetime(sent_at).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError, AttributeError):
                    pass
                contact = contacts[from_email]
                body = _message_text(message)
                if _is_legacy_campaign_text(
                    subject,
                    body,
                    contact.get("niche", ""),
                    contact.get("template_name", ""),
                    contact.get("template_url", ""),
                ):
                    continue
                responses.append(
                    {
                        "response_id": response_id,
                        "from_name": _decode_header(from_name) or contact.get("name") or from_email,
                        "from_email": from_email,
                        "subject": subject,
                        "received_at": sent_at,
                        "body": body,
                        "preview": body[:260],
                        "contact": contact,
                    }
                )
                if len(responses) >= MAX_RESPONSES:
                    break
            fetched_at = datetime.now()
            _store_responses_cache(responses, "", fetched_at)
            return responses, "", fetched_at, False
    except imaplib.IMAP4.error as exc:
        return [], f"IMAP login or fetch failed: {exc}", None, False
    except OSError as exc:
        return [], f"Could not connect to IMAP server: {exc}", None, False


def _send_response_reply(to_email, subject, body):
    sender_name = _env_value("SENDER_NAME", "muSharp")
    sender_email = _env_value("SENDER_EMAIL") or _env_value("SMTP_USERNAME", "")
    reply_to = _env_value("REPLY_TO") or sender_email
    html_body = "<br>".join(html.escape(line) for line in body.splitlines())
    text_body = body
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    with EmailSender(
        host=_env_value("SMTP_HOST"),
        port=_env_value("SMTP_PORT", "587"),
        username=_env_value("SMTP_USERNAME"),
        password=_env_value("SMTP_PASSWORD"),
        sender_name=sender_name,
        sender_email=sender_email,
        reply_to=reply_to,
    ) as email_client:
        email_client.send(to_email, subject, html_body, text_body)


def _campaign_upload_dir(niche_name):
    return UPLOAD_DIR / secure_filename(niche_name)


def _allowed_upload_dirs(niche_name):
    return [UPLOAD_DIR.resolve(), _campaign_upload_dir(niche_name).resolve()]


def _campaign_image_dir(niche_name):
    return IMAGE_DIR / secure_filename(niche_name)


def _lead_key(niche_name, lead):
    return (niche_name, lead.email, lead.phone)


def _business_from_db(row):
    business = Business(
        name=row.get("name") or "there",
        email=row.get("email", ""),
        phone=row.get("phone", ""),
        address=row.get("address", ""),
    )
    business.db_id = row.get("id", "")
    return business


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


def _write_run_text_file(prefix, text):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        encoding="utf-8",
        dir=RUN_DIR,
        prefix=prefix,
        suffix=".html",
    ) as fh:
        fh.write(text)
        return Path(fh.name)


def _stash_message_draft():
    draft = {
        "email_subject": request.form.get("email_subject", ""),
        "email_intro": request.form.get("email_intro", ""),
        "bcc_email": request.form.get("bcc_email", ""),
    }
    if "recipient_email" in request.form:
        draft["recipient_email"] = request.form.get("recipient_email", "")
    session["message_draft"] = draft


def _pop_message_draft():
    return session.pop("message_draft", None)


def _niche_email_template_path(niche_name):
    candidates = [
        f"{niche_name}.html",
        f"{niche_name.replace('_', '-')}.html",
        f"{niche_name.replace('_', '')}.html",
    ]
    aliases = {
        "dentists": ["dentist.html", "dental.html"],
        "plumber": ["plumbers.html", "plumbing.html"],
        "hospital": ["hospitals.html", "healthcare.html"],
        "care_homes": ["care-home.html", "carehomes.html", "care_home.html"],
        "pharmacy": ["pharmacies.html"],
    }
    candidates.extend(aliases.get(niche_name, []))
    template_dirs = [config.BASE_DIR / "email_templates"]
    legacy_root = config.BASE_DIR / "email-template"
    if legacy_root.exists():
        template_dirs.append(legacy_root)
        template_dirs.extend(path for path in legacy_root.iterdir() if path.is_dir())
    for directory in template_dirs:
        for name in candidates:
            path = directory / name
            if path.exists() and path.is_file():
                return path
    return None


def _load_niche_email_template(niche_name):
    path = _niche_email_template_path(niche_name)
    if not path:
        return "", ""
    return path.read_text(encoding="utf-8"), path.name


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
    seen = set()
    for upload_dir in (UPLOAD_DIR, _campaign_upload_dir(niche_name)):
        if not upload_dir.exists():
            continue
        for path in sorted(upload_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.suffix.lower() in ALLOWED_LEAD_EXTENSIONS:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
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
        files = [
            path
            for path in sorted(image_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
            if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
        ]
        if files:
            if len(files) == 1:
                latest = files[0]
                images.append(
                    {
                        "label": f"Uploaded: {latest.name}",
                        "value": _rel(latest),
                        "uploaded": True,
                        "name": latest.name,
                        "url": url_for("project_asset", filename=_rel(latest).replace("\\", "/")),
                    }
                )
            else:
                value = ",".join(_rel(p) for p in files)
                images.append(
                    {
                        "label": f"Uploaded: {len(files)} images",
                        "value": value,
                        "uploaded": True,
                        "name": f"{len(files)} images",
                        "url": url_for("project_asset", filename=_rel(files[0]).replace("\\", "/")),
                    }
                )
    return images


LIMIT_OFFSETS_FILE = config.DATA_DIR / "limit_offsets.json"

def _get_limit_offset(niche_name):
    if not LIMIT_OFFSETS_FILE.exists():
        return 0
    try:
        data = json.loads(LIMIT_OFFSETS_FILE.read_text(encoding="utf-8"))
        today = date.today().isoformat()
        return data.get(niche_name, {}).get(today, 0)
    except Exception:
        return 0

def _set_limit_offset(niche_name, offset):
    data = {}
    if LIMIT_OFFSETS_FILE.exists():
        try:
            data = json.loads(LIMIT_OFFSETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if niche_name not in data:
        data[niche_name] = {}
    today = date.today().isoformat()
    data[niche_name][today] = offset
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIMIT_OFFSETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clear_uploaded_images(niche_name):
    image_dir = _campaign_image_dir(niche_name)
    if not image_dir.exists():
        return
    for path in image_dir.iterdir():
        if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS:
            path.unlink(missing_ok=True)


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
        for upload_dir in (UPLOAD_DIR, _campaign_upload_dir(niche_name)):
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


def _is_uploaded_sheet(niche_name, sheet):
    try:
        resolved = Path(sheet).resolve()
    except OSError:
        return False
    return any(_is_relative_to(resolved, directory) for directory in _allowed_upload_dirs(niche_name))


def _resolve_image(niche_name, requested_image=None):
    if not requested_image or requested_image == "__default__":
        image_dir = _campaign_image_dir(niche_name)
        if image_dir.exists():
            files = [
                path
                for path in sorted(image_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
                if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
            ]
            if files:
                return ",".join(_rel(p) for p in files)
        return None
    if requested_image == "__none__":
        return ""

    resolved_paths = []
    # Support multiple images comma-separated
    parts = [p.strip() for p in requested_image.split(",") if p.strip()]
    for part in parts:
        candidate = Path(part)
        if not candidate.is_absolute():
            candidate = config.BASE_DIR / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(_campaign_image_dir(niche_name).resolve())
            if resolved.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS and resolved.exists():
                resolved_paths.append(_rel(resolved))
        except (OSError, ValueError):
            pass

    if resolved_paths:
        return ",".join(resolved_paths)
    return None


def _message_config(niche_name, niche_cfg, email_subject=None, email_intro=None, preview_image=None):
    copied = {
        **niche_cfg,
        "templates": [dict(template) for template in niche_cfg.get("templates", [])],
    }
    if email_subject is not None:
        copied["email_subject"] = email_subject
    if email_intro is not None:
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
    # Get the first image path from the comma-separated list for visual preview on the web dashboard
    first = raw.split(",")[0].strip()
    path = Path(first)
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


def _build_dashboard(niche_name, requested_sheet=None, email_subject=None, email_intro=None, preview_image=None, recipient_email=None, bcc_email=None):
    load_dotenv(config.BASE_DIR / ".env", override=True)
    base_niche_cfg = config.NICHES[niche_name]
    sheet = _resolve_sheet(niche_name, base_niche_cfg, requested_sheet)
    use_db = db.has_leads(niche_name)
    db_lead_rows = db.get_leads(niche_name) if use_db else []
    leads = [_business_from_db(row) for row in db_lead_rows] if use_db else list(load_leads(sheet)) if Path(sheet).exists() else []
    sent_keys = {
        (niche_name, row.get("email", ""), row.get("phone", ""))
        for row in db_lead_rows
        if row.get("status") == "sent"
    } if use_db else load_sent(config.SENT_LOG)
    sent_rows = _read_sent_rows()
    sent_today_count = db.sent_today_count(niche_name, date.today().isoformat()) if use_db else _sent_today_count(niche_name, sent_rows)
    offset = _get_limit_offset(niche_name)
    remaining_today = max(DAILY_EMAIL_LIMIT - max(sent_today_count - offset, 0), 0)
    pending_leads = (
        [_business_from_db(row) for row in db_lead_rows if row.get("status") != "sent"]
        if use_db
        else [lead for lead in leads if _lead_key(niche_name, lead) not in sent_keys]
    )
    daily_queue = pending_leads[:remaining_today]
    current_business = daily_queue[0] if daily_queue else None
    if current_business and recipient_email is not None:
        current_business.email = recipient_email
    base_templates = base_niche_cfg.get("templates", [])
    current_template = base_templates[0] if base_templates else None
    template_html, template_filename = _load_niche_email_template(niche_name)
    draft_subject = email_subject or _draft_text(base_niche_cfg.get("email_subject", ""), current_business, current_template, niche_name)
    draft_intro = email_intro if email_intro is not None else template_html
    niche_cfg = _message_config(niche_name, base_niche_cfg, draft_subject, draft_intro, preview_image)
    templates = niche_cfg.get("templates", [])

    sender_name = os.getenv("SENDER_NAME", "muSharp")
    sender_email = os.getenv("SENDER_EMAIL", os.getenv("SMTP_USERNAME", ""))
    sample_business = current_business
    sample_message = None
    if sample_business and templates:
        sample_message = build_message(sample_business, templates[0], niche_cfg, niche_name, sender_name, sender_email)

    visible_leads = leads[:LEAD_TABLE_LIMIT]
    enriched_leads = []
    for lead in visible_leads:
        enriched_leads.append(
            {
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "normalized_phone": normalize_phone(lead.phone, config.DEFAULT_COUNTRY_CODE) or lead.phone,
                "whatsapp_url": _whatsapp_web_url(lead.phone, sample_message.whatsapp_text)
                if sample_message and current_business and lead.email == current_business.email and lead.phone == current_business.phone
                else "",
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
        "selected_image": _resolve_image(niche_name, preview_image) or "__default__",
        "email_subject": niche_cfg.get("email_subject", ""),
        "email_intro": niche_cfg.get("email_intro", ""),
        "bcc_email": bcc_email or "",
        "email_template_filename": template_filename,
        "current_business": current_business,
        "daily_queue": daily_queue,
        "daily_limit": DAILY_EMAIL_LIMIT,
        "sent_today_count": max(sent_today_count - offset, 0),
        "remaining_today": remaining_today,
        "leads": enriched_leads,
        "visible_lead_count": len(enriched_leads),
        "lead_table_limit": LEAD_TABLE_LIMIT,
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
    draft = _pop_message_draft() or {}
    return render_template(
        "dashboard.html",
        **_build_dashboard(
            niche_name,
            request.args.get("sheet"),
            draft.get("email_subject"),
            draft.get("email_intro"),
            request.args.get("image"),
            draft.get("recipient_email"),
            draft.get("bcc_email"),
        ),
        run_result=None,
        upload_result=None,
    )


@app.post("/upload")
def upload_leads():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE
    _stash_message_draft()

    uploaded = request.files.get("lead_file")
    if not uploaded or not uploaded.filename:
        return render_template(
            "dashboard.html",
            **_build_dashboard(
                niche_name,
                request.form.get("sheet"),
                request.form.get("email_subject"),
                request.form.get("email_intro"),
                None,
                request.form.get("recipient_email"),
                request.form.get("bcc_email"),
            ),
            run_result=None,
            upload_result={"ok": False, "message": "Choose a CSV or Excel lead file first."},
        )

    original_name = secure_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_LEAD_EXTENSIONS:
        return render_template(
            "dashboard.html",
            **_build_dashboard(
                niche_name,
                request.form.get("sheet"),
                request.form.get("email_subject"),
                request.form.get("email_intro"),
                None,
                request.form.get("recipient_email"),
                request.form.get("bcc_email"),
            ),
            run_result=None,
            upload_result={"ok": False, "message": "Lead files must be .csv, .xlsx, or .xlsm."},
        )

    upload_dir = _campaign_upload_dir(niche_name)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_path = upload_dir / f"{Path(original_name).stem}-{stamp}{suffix}"
    uploaded.save(saved_path)

    try:
        leads = list(load_leads(saved_path))
        lead_count = len(leads)
        import_result = db.import_leads(niche_name, saved_path.name, leads)
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        return render_template(
            "dashboard.html",
            **_build_dashboard(
                niche_name,
                request.form.get("sheet"),
                request.form.get("email_subject"),
                request.form.get("email_intro"),
                None,
                request.form.get("recipient_email"),
                request.form.get("bcc_email"),
            ),
            run_result=None,
            upload_result={"ok": False, "message": f"Could not read that file: {exc}"},
        )

    message = f"{lead_count} leads loaded ({import_result['added']} new, {import_result['updated']} updated)"
    return redirect(url_for("dashboard", niche=niche_name, sheet=_rel(saved_path), uploaded=message))


@app.post("/upload-image")
def upload_image():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE
    _stash_message_draft()

    uploaded_files = [file for file in request.files.getlist("email_image") if file and file.filename]
    if not uploaded_files:
        return render_template(
            "dashboard.html",
            **_build_dashboard(
                niche_name,
                request.form.get("sheet"),
                request.form.get("email_subject"),
                request.form.get("email_intro"),
                None,
                request.form.get("recipient_email"),
                request.form.get("bcc_email"),
            ),
            run_result=None,
            upload_result={"ok": False, "message": "Choose an email image first."},
        )

    image_dir = _campaign_image_dir(niche_name)
    image_dir.mkdir(parents=True, exist_ok=True)
    _clear_uploaded_images(niche_name)
    saved_paths = []
    for index, uploaded in enumerate(uploaded_files, start=1):
        original_name = secure_filename(uploaded.filename)
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            return render_template(
                "dashboard.html",
                **_build_dashboard(
                    niche_name,
                    request.form.get("sheet"),
                    request.form.get("email_subject"),
                    request.form.get("email_intro"),
                    None,
                    request.form.get("recipient_email"),
                    request.form.get("bcc_email"),
                ),
                run_result=None,
                upload_result={"ok": False, "message": "Images must be .jpg, .jpeg, .png, .webp, or .gif."},
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        saved_path = image_dir / f"{Path(original_name).stem}-{stamp}-{index}{suffix}"
        uploaded.save(saved_path)
        saved_paths.append(saved_path)

    # Join all saved paths as a comma-separated string for URL parameters
    selected_images = ",".join(_rel(p) for p in saved_paths)
    return redirect(
        url_for(
            "dashboard",
            niche=niche_name,
            sheet=request.form.get("sheet"),
            image=selected_images,
            image_uploaded=f"{len(saved_paths)} image{'s' if len(saved_paths) != 1 else ''}",
        )
    )


@app.post("/reset-limit")
def reset_limit():
    niche_name = request.form.get("niche") or config.ACTIVE_NICHE
    if niche_name not in config.NICHES:
        niche_name = config.ACTIVE_NICHE

    use_db = db.has_leads(niche_name)
    sent_rows = _read_sent_rows()
    sent_today_count = db.sent_today_count(niche_name, date.today().isoformat()) if use_db else _sent_today_count(niche_name, sent_rows)

    _set_limit_offset(niche_name, sent_today_count)

    return redirect(
        url_for(
            "dashboard",
            niche=niche_name,
            sheet=request.form.get("sheet"),
            image=request.form.get("image"),
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
        resolved = path.resolve()
        if not any(_is_relative_to(resolved, directory) for directory in _allowed_upload_dirs(niche_name)):
            raise ValueError
    except (OSError, ValueError):
        return render_template(
            "dashboard.html",
            **_build_dashboard(niche_name, sheet),
            run_result=None,
            upload_result={"ok": False, "message": "Only uploaded lead files can be deleted."},
        )

    deleted_name = path.name
    db.delete_pending_leads_by_source(niche_name, deleted_name)
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
    use_db = db.has_leads(niche_name)
    db_pending = db.get_pending_leads(niche_name, 1) if use_db else []
    leads = [_business_from_db(row) for row in db_pending] if use_db else list(load_leads(sheet)) if sheet.exists() else []
    sent_keys = set() if use_db else load_sent(config.SENT_LOG)
    sent_rows = _read_sent_rows()
    today_count = db.sent_today_count(niche_name, date.today().isoformat()) if use_db else _sent_today_count(niche_name, sent_rows)
    offset = _get_limit_offset(niche_name)
    remaining_today = max(DAILY_EMAIL_LIMIT - max(today_count - offset, 0), 0)
    pending_leads = leads if use_db else [lead for lead in leads if _lead_key(niche_name, lead) not in sent_keys]
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

    original_email = current_business.email
    recipient_email = request.form.get("recipient_email", "").strip()
    if recipient_email and recipient_email != current_business.email:
        if use_db and getattr(current_business, "db_id", ""):
            try:
                db.update_lead_email(current_business.db_id, recipient_email)
            except Exception as exc:
                return render_template(
                    "dashboard.html",
                    **_build_dashboard(
                        niche_name,
                        request.form.get("sheet"),
                        request.form.get("email_subject"),
                        request.form.get("email_intro"),
                        None,
                        original_email,
                        request.form.get("bcc_email"),
                    ),
                    run_result={"ok": False, "output": f"Could not update recipient email: {exc}"},
                    upload_result=None,
                )
        current_business.email = recipient_email

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
    bcc_email = request.form.get("bcc_email", "").strip()
    email_intro = request.form.get("email_intro", "").strip() or _load_niche_email_template(niche_name)[0].strip()
    preview_image = request.form.get("preview_image", "__default__")
    resolved_image = _resolve_image(niche_name, preview_image)
    temp_files = []
    if email_subject:
        args.extend(["--email-subject", email_subject])
    if bcc_email:
        args.extend(["--bcc", bcc_email])
    if email_intro:
        email_intro_file = _write_run_text_file("email-intro-", email_intro)
        temp_files.append(email_intro_file)
        args.extend(["--email-intro-file", str(email_intro_file)])
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

    try:
        completed = subprocess.run(
            args,
            cwd=config.BASE_DIR,
            env=_command_env(),
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
    finally:
        for path in temp_files:
            path.unlink(missing_ok=True)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip()).strip()
    if use_db and completed.returncode == 0 and mode == "send":
        template = _message_config(niche_name, config.NICHES[niche_name], email_subject, email_intro, preview_image)["templates"][0]
        db.mark_sent(
            niche_name,
            current_business,
            template.get("name", ""),
            template.get("url", ""),
            email_status="sent" if not request.form.get("no_email") else "disabled",
            whatsapp_status="disabled" if request.form.get("no_whatsapp") else "sent",
            sent_at=datetime.now().isoformat(timespec="seconds"),
        )
    refresh_subject = None if completed.returncode == 0 and mode == "send" else email_subject
    refresh_recipient_email = None if completed.returncode == 0 and mode == "send" else current_business.email
    refresh_bcc_email = None if completed.returncode == 0 and mode == "send" else bcc_email
    return render_template(
        "dashboard.html",
        **_build_dashboard(
            niche_name,
            request.form.get("sheet"),
            refresh_subject,
            email_intro,
            preview_image,
            refresh_recipient_email,
            refresh_bcc_email,
        ),
        run_result={"ok": completed.returncode == 0, "output": output or "Command completed with no output."},
        upload_result=None,
    )


@app.route("/assets/<path:filename>")
def project_asset(filename):
    assets_dir = config.BASE_DIR / "assets"
    if (assets_dir / filename).exists() and (assets_dir / filename).is_file():
        return send_from_directory(assets_dir, filename)
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


@app.post("/results/delete")
def delete_result():
    removed = _delete_sent_row(request.form.get("sent_row_id", ""))
    if removed:
        return redirect(url_for("results", deleted=removed.get("name") or removed.get("email") or "record"))
    rows = _result_rows()
    return render_template(
        "results.html",
        rows=rows,
        opened_count=sum(1 for row in rows if row["opened"]),
        tracked_count=sum(1 for row in rows if row["tracking"] == "enabled"),
        total_sent=len(rows),
        tracking_base_url=_tracking_base_url(),
        tracking_is_public=_tracking_is_public(),
        run_result={"ok": False, "output": "Could not delete that results row."},
    )


@app.get("/follow-ups")
def follow_ups():
    return render_template("follow_ups.html", rows=_sent_email_rows(), run_result=None)


@app.get("/responses")
def responses():
    rows, error, fetched_at, cached = _fetch_responses(force_refresh=request.args.get("refresh") == "1")
    return render_template(
        "responses.html",
        rows=rows,
        fetched_at=fetched_at,
        cached=cached,
        run_result={"ok": False, "output": error} if error else None,
    )


@app.post("/responses/send")
def send_response():
    to_email = request.form.get("to_email", "").strip()
    subject = request.form.get("subject", "").strip() or "Following up"
    body = request.form.get("body", "").strip()
    if not to_email or not body:
        rows, _, fetched_at, cached = _fetch_responses()
        return render_template(
            "responses.html",
            rows=rows,
            fetched_at=fetched_at,
            cached=cached,
            run_result={"ok": False, "output": "Write a reply before sending."},
        )
    try:
        _send_response_reply(to_email, subject, body)
        _clear_responses_cache()
        rows, _, fetched_at, cached = _fetch_responses(force_refresh=True)
        return render_template(
            "responses.html",
            rows=rows,
            fetched_at=fetched_at,
            cached=cached,
            run_result={"ok": True, "output": f"Reply sent to {to_email}."},
        )
    except Exception as exc:
        rows, _, fetched_at, cached = _fetch_responses()
        return render_template(
            "responses.html",
            rows=rows,
            fetched_at=fetched_at,
            cached=cached,
            run_result={"ok": False, "output": str(exc)},
        )


@app.post("/responses/delete")
def delete_response():
    response_id = request.form.get("response_id", "").strip()
    from_email = request.form.get("from_email", "").strip()
    subject = request.form.get("subject", "").strip()
    if _ignore_response(response_id, from_email, subject):
        return redirect(url_for("responses", deleted=from_email or "response"))
    rows, _, fetched_at, cached = _fetch_responses()
    return render_template(
        "responses.html",
        rows=rows,
        fetched_at=fetched_at,
        cached=cached,
        run_result={"ok": False, "output": "Could not delete that response from the app."},
    )


@app.post("/follow-ups/delete")
def delete_follow_up():
    removed = _delete_sent_row(request.form.get("row_id", ""))
    return render_template(
        "follow_ups.html",
        rows=_sent_email_rows(),
        run_result={
            "ok": bool(removed),
            "output": (
                f"Deleted {removed.get('name') or removed.get('email') or 'that row'} from follow-ups and results."
                if removed
                else "Could not delete that follow-up row."
            ),
        },
    )


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
        "--template-url",
        row.get("template_url", ""),
        "--tracking-base-url",
        _tracking_base_url(),
    ]

    completed = subprocess.run(
        args,
        cwd=config.BASE_DIR,
        env=_command_env(),
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
