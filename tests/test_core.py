import unittest

import config
import app as app_module
from app import app
from content import build_message
from main import Business, load_leads
from senders import EmailSender, normalize_phone


class CoreTests(unittest.TestCase):
    def test_normalize_local_pakistan_phone(self):
        self.assertEqual(normalize_phone("03136620237", "92"), "+923136620237")

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
