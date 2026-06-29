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


if __name__ == "__main__":
    unittest.main()
