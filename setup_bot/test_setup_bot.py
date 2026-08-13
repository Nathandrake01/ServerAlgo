"""Unit tests for ServerAlgo setup_bot components (deploy_core & setup_agent logic)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Allow imports from setup_bot or parent
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deploy_core
from setup_agent import _is_base32


class TestDeployCorePureHelpers(unittest.TestCase):

    def test_generate_config_valid(self):
        cfg = deploy_core.generate_config("pr_0918", 2, "5000")
        self.assertIn("name: 'pr_0918'", cfg)
        self.assertIn("quantity_lots: 2", cfg)
        self.assertIn("host: 'http://127.0.0.1:5000'", cfg)

    def test_generate_config_unknown_strategy(self):
        with self.assertRaises(ValueError):
            deploy_core.generate_config("invalid_strat", 1, "5000")

    def test_entry_script_generation(self):
        # pr_0918 (standard runner)
        pr_code = deploy_core.entry_script("pr_0918")
        self.assertIn('run("pr_0918.live.yaml")', pr_code)
        self.assertIn("from engine.pr_runner import run", pr_code)

        # gamma (requires class import)
        gamma_code = deploy_core.entry_script("gamma")
        self.assertIn("from engine.gamma_engine import LongGammaRescue", gamma_code)
        self.assertIn('run("gamma.live.yaml", engine_cls=LongGammaRescue)', gamma_code)

        # delta (requires class import)
        delta_code = deploy_core.entry_script("delta")
        self.assertIn("from engine.delta_engine import DeltaShortStrangle", delta_code)
        self.assertIn('run("delta.live.yaml", engine_cls=DeltaShortStrangle)', delta_code)

    def test_env_overrides_fresh_vs_preserved(self):
        creds = {
            "BROKER_API_KEY": "my_apikey",
            "BROKER_API_SECRET": "my_secret",
            "ZERODHA_USER_ID": "AB1234",
            "ZERODHA_PASSWORD": "password123",
            "ZERODHA_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        }
        # Fresh installation
        ov1, user1, pass1 = deploy_core.env_overrides("zerodha", creds, "5000", "8765")
        self.assertEqual(ov1["BROKER_API_KEY"], "my_apikey")
        self.assertEqual(ov1["ZERODHA_USER_ID"], "AB1234")
        self.assertEqual(user1, "admin")
        self.assertTrue(len(ov1["APP_KEY"]) > 10)

        # Re-run (preserving existing crypto keys and user creds)
        preserve = {
            "APP_KEY": "existing_app_key",
            "FERNET_SALT": "existing_salt",
            "API_KEY_PEPPER": "existing_pepper",
            "OPENALGO_USER": "custom_admin",
            "OPENALGO_PASS": "custom_pass",
            "OPENALGO_API_KEY": "existing_api_key",
        }
        ov2, user2, pass2 = deploy_core.env_overrides("zerodha", creds, "5000", "8765", preserve=preserve)
        self.assertEqual(ov2["APP_KEY"], "existing_app_key")
        self.assertEqual(ov2["FERNET_SALT"], "existing_salt")
        self.assertEqual(ov2["API_KEY_PEPPER"], "existing_pepper")
        self.assertEqual(user2, "custom_admin")
        self.assertEqual(pass2, "custom_pass")
        self.assertEqual(ov2["OPENALGO_API_KEY"], "existing_api_key")

    def test_apply_env_overrides_in_place(self):
        sample_env = (
            "# Comment\n"
            "BROKER_API_KEY='old_key'\n"
            "UNTOUCHED_VAR='keep_me'\n"
        )
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(sample_env)
            f_path = f.name

        try:
            overrides = {
                "BROKER_API_KEY": "new_key",
                "NEW_ADDED_VAR": "added_val",
            }
            deploy_core.apply_env_overrides(f_path, overrides)
            content = Path(f_path).read_text(encoding="utf-8")
            self.assertIn("BROKER_API_KEY='new_key'", content)
            self.assertIn("UNTOUCHED_VAR='keep_me'", content)
            self.assertIn("NEW_ADDED_VAR='added_val'", content)
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)


    def test_kotak_env_overrides(self):
        creds = {
            "BROKER_API_KEY": "kotak_key",
            "BROKER_API_SECRET": "kotak_secret",
            "KOTAK_MOBILE": "9876543210",
            "KOTAK_MPIN": "123456",
            "KOTAK_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        }
        ov, user, _ = deploy_core.env_overrides("kotak", creds, "5000", "8765", public_ip="1.2.3.4")
        self.assertEqual(ov["VALID_BROKERS"], "kotak")
        self.assertEqual(ov["KOTAK_MOBILE"], "9876543210")
        self.assertEqual(ov["KOTAK_MPIN"], "123456")
        self.assertEqual(ov["KOTAK_TOTP_SECRET"], "JBSWY3DPEHPK3PXP")
        self.assertEqual(ov["REDIRECT_URL"], "http://1.2.3.4:5000/kotak/callback")
        self.assertEqual(ov["FLASK_HOST_IP"], "0.0.0.0")

    def test_get_public_ip_fallback(self):
        ip = deploy_core.get_public_ip()
        self.assertTrue(isinstance(ip, str) and len(ip) > 0)



class TestSetupAgentHelpers(unittest.TestCase):

    def test_is_base32_validation(self):
        # Valid base32 secrets
        self.assertTrue(_is_base32("JBSWY3DPEHPK3PXP"))
        self.assertTrue(_is_base32("jbswy3dpehpk3pxp"))
        self.assertTrue(_is_base32("JBSW Y3DP EHPK 3PXP"))

        # Invalid base32 strings
        self.assertFalse(_is_base32("1898989898989898"))  # 8 and 9 are invalid in Base32
        self.assertFalse(_is_base32("!!!INVALID!!!"))
        self.assertFalse(_is_base32(""))



if __name__ == "__main__":
    unittest.main()
