# Email Automations

Musharp Automation is a Python outreach tool for sending promotional email from
uploaded lead files, managing daily campaign queues, tracking sent mail results,
and sending follow-up emails.

The current active campaign is `solar`, displayed in the frontend as
`Website Automation`.

## Project Structure

```text
Web-Automation/
  assets/                  Promotion images for email previews
  data/                    Lead CSV/XLSX files and generated logs
  email_templates/         Jinja HTML email templates
  config.py                Campaign/niche configuration
  content.py               Builds email and WhatsApp message content
  senders.py               SMTP and Twilio delivery classes
  main.py                  Command-line runner
  requirements.txt         Python dependencies
  .env.example             Credential template
```

## Setup

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create a `.env` file from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Fill these values before real sending:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SENDER_NAME=FRZ Energy
SENDER_EMAIL=your-email@gmail.com
REPLY_TO=your-email@gmail.com

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_MEDIA_URL=
```

`TWILIO_MEDIA_URL` must be a public HTTPS URL. Twilio cannot send WhatsApp media
from a local file path.

## Current Lead

The current solar lead file is:

```text
data/leads_solar.csv
```

It contains:

```text
Fasih Jamal, fasihjamal30@gmail.com, 03136620237
```

The phone is normalized to:

```text
+923136620237
```

## Commands

Check setup:

```powershell
python main.py --check
```

Dry run without sending:

```powershell
python main.py
```

Write rendered email preview HTML:

```powershell
python main.py --preview
```

Run smoke tests:

```powershell
python -m unittest discover -s tests
```

Send both email and WhatsApp after `.env` is filled:

```powershell
python main.py --send
```

Send email only:

```powershell
python main.py --send --no-whatsapp
```

Send WhatsApp only:

```powershell
python main.py --send --no-email
```

Force resend even if the lead is already logged:

```powershell
python main.py --send --resend
```

## Lead File Format

CSV and XLSX are supported.

Recommended CSV columns:

```csv
business name,email,phone
Fasih Jamal,fasihjamal30@gmail.com,03136620237
```

Column names are matched loosely using `COLUMN_MAP` in `config.py`.

## Logs

Successful real sends are logged to:

```text
data/sent_log.csv
```

The log prevents duplicate sends unless `--resend` is used.

## Campaign Configuration

Campaigns live in `config.py` under `NICHES`.

The active campaign is:

```python
ACTIVE_NICHE = "solar"
```

The solar campaign uses:

```text
assets/frz-energy-solar.jpeg
https://www.frzenergy.store
```

## Safety Notes

- The app does not send anything unless you pass `--send`.
- Keep `.env` private. It is ignored by `.gitignore`.
- Use approved Twilio WhatsApp templates if your Twilio account requires them.
- Keep opt-out requests and remove those leads before future sends.
