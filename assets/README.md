# Assets Folder

Put promotion images and email preview screenshots here.

Local files listed in `config.py` under `preview_image` are embedded inside
emails. WhatsApp media is different: Twilio needs a public HTTPS URL, so upload
the same image somewhere public and set `TWILIO_MEDIA_URL` in `.env`.
