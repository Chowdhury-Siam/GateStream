"""A fresh interpreter must start from .env without manually exported settings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class EnvOnlyStartupTests(unittest.TestCase):
    def test_all_services_share_env_file_configuration(self):
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            settings='''# PORT=7860
# MULTI_BOT_TOKENS="token1 token2 token3"
TELEGRAM_API_ID=12345
TELEGRAM_API_HASH=test_hash
TELEGRAM_BOT_TOKEN=12345:test_token
OWNER_ID=12345
DATABASE_URL=mongodb://localhost:27017
BIND_ADDRESS=127.0.0.1
PORT=40978
MULTI_BOT_TOKENS=""
MEDIA_CACHE_MAX_MB=64
MOTOR_MAX_WORKERS=2
'''
            Path(directory,'.env').write_text(settings+f'LOG_FILE="{directory}/event.log"\n')
            code='''
import json
from bot.config import Server, Resources, Telegram, Database
from bot.modules import cache
from bot.server import server
from motor.frameworks import asyncio as framework
print(json.dumps([Server.PORT,server.config.port,server.config.host,
 cache.MAX_CACHE_BYTES,Telegram.MULTI_BOT_TOKENS,Database.DATABASE_NAME,framework.max_workers]))
'''
            result=subprocess.run([sys.executable,'-c',code],cwd=directory,
                env={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(source)},
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),
                [40978,40978,'127.0.0.1',64*1024*1024,[],'OmniArchiver',2])
