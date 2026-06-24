import unittest

import config
from content import build_message
from main import Business, load_leads
from senders import normalize_phone


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
            "Musharp Automation",
            "sender@example.com",
        )
        self.assertIn("website preview", message.subject)
        self.assertIn("Fasih Jamal", message.html_body)
        self.assertIn("Business Manager", message.html_body)
        self.assertIn("your-demo-host.example/dentist-1", message.whatsapp_text)


if __name__ == "__main__":
    unittest.main()
