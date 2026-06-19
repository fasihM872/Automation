import unittest

import config
from content import build_message
from main import Business, load_leads
from senders import normalize_phone


class CoreTests(unittest.TestCase):
    def test_normalize_local_pakistan_phone(self):
        self.assertEqual(normalize_phone("03136620237", "92"), "+923136620237")

    def test_load_current_solar_lead(self):
        leads = list(load_leads(config.DATA_DIR / "leads_solar.csv"))
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].email, "fasihjamal30@gmail.com")

    def test_build_solar_message(self):
        niche = config.NICHES["solar"]
        business = Business("Fasih Jamal", "fasihjamal30@gmail.com", "03136620237")
        message = build_message(
            business,
            niche["templates"][0],
            niche,
            "solar",
            "FRZ Energy",
            "info@frzenergy.store",
        )
        self.assertIn("Top solar brands", message.subject)
        self.assertIn("FRZ Energy", message.html_body)
        self.assertIn("https://www.frzenergy.store", message.whatsapp_text)
        self.assertTrue(message.inline_images)


if __name__ == "__main__":
    unittest.main()
