import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


CONFIG = Path(__file__).resolve().parents[1] / 'bot' / 'config.py'
SECRETS = {
    'TELEGRAM_API_ID': '12345',
    'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token',
    'OWNER_ID': '12345',
    'DATABASE_URL': 'mongodb://localhost:27017',
}


class ConfigTests(unittest.TestCase):
    def load_config(self, settings=None, env_file=None):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                if env_file is not None:
                    Path('.env').write_text(env_file, encoding='utf-8')
                with patch.dict(os.environ, settings or {}, clear=True):
                    return runpy.run_path(str(CONFIG))
            finally:
                os.chdir(previous)

    def test_spaces_host_and_port(self):
        config = self.load_config({**SECRETS, 'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space'})
        config['validate_config']()
        self.assertEqual(config['Server'].BASE_URL, 'https://user-app.hf.space')
        self.assertEqual(config['Server'].PORT, 7860)
        self.assertEqual(config['Server'].BIND_ADDRESS, '0.0.0.0')

    def test_local_defaults_and_no_embedded_upstream_credentials(self):
        config = self.load_config(SECRETS)
        self.assertEqual(config['Server'].PORT, 8080)
        self.assertEqual(config['Server'].BASE_URL, 'http://127.0.0.1:8080')
        self.assertEqual(config['Database'].OMNI_MONGO_URI, '')

    def test_environment_secrets_win_over_file(self):
        config = self.load_config(SECRETS, 'OWNER_ID=999\nTELEGRAM_BOT_TOKEN="file_token"\nDATABASE_NAME=LocalDB\n')
        self.assertEqual(config['Telegram'].OWNER_ID, 12345)
        self.assertEqual(config['Telegram'].BOT_TOKEN, SECRETS['TELEGRAM_BOT_TOKEN'])
        self.assertEqual(config['Database'].DATABASE_NAME, 'LocalDB')

    def test_custom_url_and_port(self):
        config = self.load_config({**SECRETS, 'BASE_URL': 'https://stream.example.com/', 'PORT': '9000'})
        config['validate_config']()
        self.assertEqual(config['Server'].BASE_URL, 'https://stream.example.com')
        self.assertEqual(config['Server'].PORT, 9000)

    def test_missing_credentials_fail_without_default_admin(self):
        config = self.load_config()
        self.assertEqual(config['Telegram'].ADMIN_IDS, {0})
        with self.assertRaisesRegex(ValueError, 'OWNER_ID'):
            config['validate_config']()

    def test_database_is_required_on_spaces(self):
        settings = {**SECRETS, 'SPACE_ID': 'user/app'}
        del settings['DATABASE_URL']
        config = self.load_config(settings)
        with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
            config['validate_config']()

    def test_invalid_port_or_url_fails(self):
        for setting, value, error in [('PORT', '70000', 'PORT'), ('BASE_URL', 'example.com', 'BASE_URL')]:
            with self.subTest(setting=setting):
                config = self.load_config({**SECRETS, setting: value})
                with self.assertRaisesRegex(ValueError, error):
                    config['validate_config']()


if __name__ == '__main__':
    unittest.main()
