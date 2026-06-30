import unittest

import config
import app as app_module
from app import app
from content import build_message
from main import Business, load_leads
from senders import EmailSender, normalize_phone


class CoreTests(unittest.TestCase):
    def test_normalize_local_uk_phone(self):
        self.assertEqual(normalize_phone("01782 664895", "44"), "+441782664895")
        self.assertEqual(normalize_phone("07870 727289", "44"), "+447870727289")
        self.assertEqual(normalize_phone("+44 1234 567890", "44"), "+441234567890")
        self.assertEqual(normalize_phone("01189 514710; 01217 885710", "44"), "+441189514710")

    def test_load_current_dentist_lead_file(self):
        leads = list(load_leads(config.DATA_DIR / "leads_dentists.csv"))
        self.assertEqual(leads, [])

    def test_build_dentist_message(self):
        niche = config.NICHES["dentists"]
        business = Business("Sample Clinic", "sample@example.com", "03136620237")
        message = build_message(
            business,
            niche["templates"][0],
            niche,
            "dentists",
            "muSharp",
            "sender@example.com",
        )
        self.assertIn("website preview", message.subject)
        self.assertIn("Sample Clinic", message.html_body)
        self.assertNotIn("Regards", message.html_body)
        self.assertIn("your-demo-host.example/dentist-1", message.whatsapp_text)

    def test_new_gym_and_barber_niches_are_available(self):
        self.assertIn("gym", config.NICHES)
        self.assertIn("barber", config.NICHES)
        self.assertTrue((config.DATA_DIR / "leads_gym.csv").exists())
        self.assertTrue((config.DATA_DIR / "leads_barber.csv").exists())
        self.assertIn("gym.html", app.test_client().get("/?niche=gym").get_data(as_text=True))
        self.assertIn("barber.html", app.test_client().get("/?niche=barber").get_data(as_text=True))

    def test_manual_lead_can_be_added_from_dashboard(self):
        import db

        niche = "manual_test"
        original_niches = dict(config.NICHES)
        original_active = config.ACTIVE_NICHE
        config.NICHES[niche] = {
            **config.NICHES["dentists"],
            "sheet": config.DATA_DIR / "manual_test.csv",
        }
        app_module.config.NICHES = config.NICHES
        try:
            db.delete_pending_leads_by_source(niche, "manual")
            response = app.test_client().post(
                "/add-lead",
                data={
                    "niche": niche,
                    "manual_name": "Manual Gym",
                    "manual_email": "manual@example.com",
                    "manual_phone": "01782 664895",
                },
                follow_redirects=True,
            )
            body = response.get_data(as_text=True)
            self.assertIn("Manual Gym", body)
            self.assertIn("manual@example.com", body)
        finally:
            db.delete_pending_leads_by_source(niche, "manual")
            config.NICHES.clear()
            config.NICHES.update(original_niches)
            config.ACTIVE_NICHE = original_active
            app_module.config.NICHES = config.NICHES

    def test_pending_lead_can_be_deleted_from_dashboard(self):
        import db

        niche = "delete_lead_test"
        original_niches = dict(config.NICHES)
        config.NICHES[niche] = {
            **config.NICHES["dentists"],
            "sheet": config.DATA_DIR / "delete_lead_test.csv",
        }
        app_module.config.NICHES = config.NICHES
        try:
            db.delete_pending_leads_by_source(niche, "manual")
            app.test_client().post(
                "/add-lead",
                data={
                    "niche": niche,
                    "manual_name": "Delete Me",
                    "manual_email": "delete@example.com",
                    "manual_phone": "01782 664895",
                },
            )
            lead_id = db.get_pending_leads(niche, 1)[0]["id"]
            response = app.test_client().post(
                "/delete-lead",
                data={"niche": niche, "lead_id": lead_id},
                follow_redirects=True,
            )
            body = response.get_data(as_text=True)
            self.assertNotIn("delete@example.com", body)
            self.assertEqual(db.get_pending_leads(niche, 1), [])
        finally:
            db.delete_pending_leads_by_source(niche, "manual")
            config.NICHES.clear()
            config.NICHES.update(original_niches)
            app_module.config.NICHES = config.NICHES

    def test_pasted_template_sample_business_name_is_dynamic(self):
        niche = {
            **config.NICHES["dentists"],
            "email_intro": (
                "<title>A Professional Website for {business_name} Dental Clinic</title>"
                "<p>We came across ur business <strong>musharp</strong>.</p>"
                '<p>Designed by <a href="https://musharp.com">muSharp</a></p>'
            ),
        }
        business = Business("Bright Smile Care", "sample@example.com", "03136620237")
        message = build_message(
            business,
            niche["templates"][0],
            niche,
            "dentists",
            "muSharp",
            "sender@example.com",
        )
        self.assertIn("A Professional Website for Bright Smile Care Dental Clinic", message.html_body)
        self.assertIn("<strong>Bright Smile Care</strong>", message.html_body)
        self.assertIn("https://musharp.com", message.html_body)
        self.assertIn(">muSharp</a>", message.html_body)

    def test_dashboard_loads_niche_template_file(self):
        template_path = config.BASE_DIR / "email_templates" / "dentists.html"
        original = template_path.read_text(encoding="utf-8") if template_path.exists() else None
        template_path.write_text("<html><body>Niche file for musharp</body></html>", encoding="utf-8")
        try:
            body = app.test_client().get("/?niche=dentists").get_data(as_text=True)
        finally:
            if original is None:
                template_path.unlink(missing_ok=True)
            else:
                template_path.write_text(original, encoding="utf-8")
        self.assertIn("Niche file for musharp", body)
        self.assertIn("dentists.html", body)

    def test_response_ignore_list(self):
        original_log = app_module.IGNORED_RESPONSES_LOG
        app_module.IGNORED_RESPONSES_LOG = config.DATA_DIR / "ignored_responses-test.csv"
        app_module.IGNORED_RESPONSES_LOG.unlink(missing_ok=True)
        try:
            self.assertTrue(app_module._ignore_response("123", "lead@example.com", "Subject"))
            self.assertIn("123", app_module._ignored_response_ids())
            self.assertTrue(app_module._ignore_response("123", "lead@example.com", "Subject"))
            self.assertEqual(len(app_module._read_csv_rows(app_module.IGNORED_RESPONSES_LOG)), 1)
        finally:
            app_module.IGNORED_RESPONSES_LOG.unlink(missing_ok=True)
            app_module.IGNORED_RESPONSES_LOG = original_log

    def test_response_preview_strips_quoted_campaign_css(self):
        raw_body = (
            "Thank you for contacting CDS. This email is monitored throughout the day.\n\n"
            "A Professional Website for Bedford Heights Hospital\n"
            "/* Reset and general styles */\n"
            "body, table, td, a {\n"
            "-webkit-text-size-adjust: 100%;\n"
            "}\n"
            "Attached is a preview for your new website. Get it live for $200.\n"
        )

        cleaned = app_module._clean_response_body(raw_body)

        self.assertEqual(
            cleaned,
            "Thank you for contacting CDS. This email is monitored throughout the day.",
        )

    def test_cached_responses_are_cleaned_before_display(self):
        original_cache_file = app_module.RESPONSES_CACHE_FILE
        original_memory_cache = dict(app_module._RESPONSES_CACHE)
        app_module._RESPONSES_CACHE.update({"rows": None, "error": "", "fetched_at": None})
        try:
            import json
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                app_module.RESPONSES_CACHE_FILE = Path(tmpdir) / "responses_cache.json"
                payload = {
                    "rows": [
                        {
                            "body": "Thanks for your email.\n\nAttached is a preview for your new website.",
                            "preview": "Attached is a preview for your new website.",
                        }
                    ],
                    "error": "",
                    "fetched_at": "2026-06-30T11:29:43",
                }
                app_module.RESPONSES_CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")

                rows, _, _ = app_module._cached_responses()

                self.assertEqual(rows[0]["body"], "Thanks for your email.")
                self.assertEqual(rows[0]["preview"], "Thanks for your email.")
        finally:
            app_module.RESPONSES_CACHE_FILE = original_cache_file
            app_module._RESPONSES_CACHE.clear()
            app_module._RESPONSES_CACHE.update(original_memory_cache)

    def test_email_sender_bcc_is_envelope_only(self):
        class DummyServer:
            def sendmail(self, sender, recipients, body):
                self.sender = sender
                self.recipients = recipients
                self.body = body

        sender = EmailSender.__new__(EmailSender)
        sender.sender_name = "muSharp"
        sender.sender_email = "sender@example.com"
        sender.reply_to = "reply@example.com"
        sender._server = DummyServer()

        sender.send(
            "lead@example.com",
            "Subject",
            "<p>Hello</p>",
            "Hello",
            bcc="boss@example.com, Archive <archive@example.com>",
        )

        self.assertEqual(
            sender._server.recipients,
            ["lead@example.com", "boss@example.com", "archive@example.com"],
        )
        self.assertNotIn("Bcc:", sender._server.body)
        self.assertEqual(
            EmailSender._parse_recipients("boss@example.com; Archive <archive@example.com>"),
            ["boss@example.com", "archive@example.com"],
        )

    def test_email_sender_can_use_business_name_as_from_display(self):
        class DummyServer:
            def sendmail(self, sender, recipients, body):
                self.sender = sender
                self.recipients = recipients
                self.body = body

        sender = EmailSender.__new__(EmailSender)
        sender.sender_name = "Musharp Automation"
        sender.sender_email = "sender@example.com"
        sender.reply_to = "reply@example.com"
        sender._server = DummyServer()

        sender.send(
            "lead@example.com",
            "Subject",
            "<p>Hello</p>",
            "Hello",
            from_name="Pitkerro Ltd",
        )

        self.assertIn("From: Pitkerro Ltd <sender@example.com>", sender._server.body)
        self.assertEqual(sender._server.sender, "sender@example.com")

    def test_inline_image_cid_resolution(self):
        from content import build_message
        from main import Business
        
        business = Business(name="Test Clinic", email="test@clinic.com", phone="1234")
        template = {"name": "Test Niche", "url": "https://test.com", "preview_image": ""}
        niche_cfg = {
            "email_subject": "Test subject",
            "email_intro": '<p>Check out our chatbot: <img src="cid:dentist_chatbot"></p>',
            "whatsapp_message": "Hello",
            "templates": [template]
        }
        
        message = build_message(
            business,
            template,
            niche_cfg,
            "dentists",
            "muSharp",
            "sender@example.com"
        )
        self.assertTrue(any(cid == "dentist_chatbot" for cid, _ in message.inline_images))

    def test_multiple_attachments_resolution(self):
        from content import build_message
        from main import Business
        import tempfile
        from pathlib import Path
        
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = Path(tmpdir) / "image1.png"
            file2 = Path(tmpdir) / "image2.jpg"
            file1.touch()
            file2.touch()
            
            business = Business(name="Test Clinic", email="test@clinic.com", phone="1234")
            template = {
                "name": "Test Niche",
                "url": "https://test.com",
                "preview_image": f"{file1.resolve()},{file2.resolve()}"
            }
            niche_cfg = {
                "email_subject": "Test subject",
                "email_intro": "Hello",
                "whatsapp_message": "Hello",
                "templates": [template]
            }
            
            message = build_message(
                business,
                template,
                niche_cfg,
                "dentists",
                "muSharp",
                "sender@example.com"
            )
            
            self.assertEqual(len(message.attachments), 2)
            self.assertIn(str(file1.resolve()), message.attachments)
            self.assertIn(str(file2.resolve()), message.attachments)

    def test_available_images_lists_each_niche_image(self):
        import app
        import tempfile
        from pathlib import Path

        original_image_dir = app.IMAGE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            app.IMAGE_DIR = Path(tmpdir)
            niche_dir = app.IMAGE_DIR / "plumber"
            niche_dir.mkdir(parents=True)
            first = niche_dir / "first.png"
            second = niche_dir / "second.jpg"
            first.touch()
            second.touch()
            try:
                with app.app.test_request_context("/"):
                    images = app._available_images("plumber")
            finally:
                app.IMAGE_DIR = original_image_dir

        uploaded = [image for image in images if image.get("uploaded")]
        self.assertEqual({image["name"] for image in uploaded}, {"first.png", "second.jpg"})
        self.assertEqual(len(uploaded), 2)

    def test_upload_image_keeps_existing_niche_images(self):
        import app
        import tempfile
        from pathlib import Path
        from io import BytesIO

        original_image_dir = app.IMAGE_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            app.IMAGE_DIR = Path(tmpdir)
            niche_dir = app.IMAGE_DIR / "plumber"
            niche_dir.mkdir(parents=True)
            existing = niche_dir / "existing.png"
            existing.write_bytes(b"old")
            try:
                client = app.app.test_client()
                response = client.post(
                    "/upload-image",
                    data={
                        "niche": "plumber",
                        "sheet": "",
                        "email_image": (BytesIO(b"new"), "new.png"),
                    },
                    content_type="multipart/form-data",
                    follow_redirects=False,
                )
                saved_names = {path.name for path in niche_dir.iterdir()}
            finally:
                app.IMAGE_DIR = original_image_dir

        self.assertEqual(response.status_code, 302)
        self.assertIn("existing.png", saved_names)
        self.assertTrue(any(name.startswith("new-") and name.endswith(".png") for name in saved_names))

    def test_limit_offset_logic(self):
        import app
        import tempfile
        import json
        from pathlib import Path
        
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_offset_file = Path(tmpdir) / "limit_offsets.json"
            original_file = app.LIMIT_OFFSETS_FILE
            app.LIMIT_OFFSETS_FILE = temp_offset_file
            try:
                self.assertEqual(app._get_limit_offset("test_niche"), 0)
                app._set_limit_offset("test_niche", 5)
                self.assertEqual(app._get_limit_offset("test_niche"), 5)
                self.assertTrue(temp_offset_file.exists())
                data = json.loads(temp_offset_file.read_text(encoding="utf-8"))
                self.assertIn("test_niche", data)
            finally:
                app.LIMIT_OFFSETS_FILE = original_file


if __name__ == "__main__":
    unittest.main()
