"""Configuration must not interpret documentation as live settings."""
import logging
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from test_config import CONFIG, SECRETS

TOKEN_A = '100001:' + 'Z' * 35
TOKEN_B = '100002:' + 'Y' * 35


class ConfigurationRegressionTests(unittest.TestCase):
    def load(self, files=None, environ=None):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                for name, value in (files or {'.env': ''}).items():
                    Path(name).write_bytes(value if isinstance(value, bytes) else value.encode())
                with patch.dict(os.environ, {**SECRETS, **(environ or {})}, clear=True):
                    return runpy.run_path(str(CONFIG))
            finally:
                os.chdir(previous)

    def test_comment_examples_cannot_override_explicit_port_or_workers(self):
        cfg = self.load({'.env': f'# PORT=7860\n# MULTI_BOT_TOKENS="{TOKEN_A}"\n\nBIND_ADDRESS=127.0.0.1\nPORT=40978\nMEDIA_CACHE_MAX_MB=512\nMULTI_BOT_TOKENS=""\n',
                         'start.sh': '#!/bin/sh\n# PORT=9999\nexport PORT=7860\npython -m bot\n'})
        cfg['validate_config']()
        self.assertEqual((cfg['Server'].BIND_ADDRESS, cfg['Server'].PORT), ('127.0.0.1', 40978))
        self.assertEqual(cfg['Resources'].MEDIA_CACHE_MAX_MB, 512)
        self.assertEqual(cfg['Telegram'].MULTI_BOT_TOKENS, [])

    def test_environment_and_empty_alias_overrides_and_last_assignment(self):
        cfg = self.load({'.env': f'PORT=40978\nMULTI_BOT_TOKENS={TOKEN_A}\nDATABASE_NAME=first\nDATABASE_NAME=last\n'},
                        {'PORT': '43123', 'BOT_TOKENS': ''})
        self.assertEqual(cfg['Server'].PORT, 43123)
        self.assertEqual(cfg['Telegram'].MULTI_BOT_TOKENS, [])
        self.assertEqual(cfg['Database'].DATABASE_NAME, 'last')

    def test_quotes_special_characters_bare_hash_and_inline_comments(self):
        cfg = self.load()
        read = cfg['read_settings']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'.env'
            path.write_text("ONE=' a # b=c $d ! : / \\ x '\nTWO=\"a # b=c $d !\"\nTHREE=abc#def=ghi\nFOUR=value # a comment\nEMPTY=\nUNASSIGNED\n")
            values=read(path)
        self.assertEqual(values['ONE'], ' a # b=c $d ! : / \\ x ')
        self.assertEqual(values['TWO'], 'a # b=c $d !')
        self.assertEqual(values['THREE'], 'abc#def=ghi')
        self.assertEqual(values['FOUR'], 'value')
        self.assertEqual(values['EMPTY'], '')
        self.assertNotIn('UNASSIGNED',values)

    def test_bom_crlf_and_lf(self):
        for newline in ('\n', '\r\n'):
            cfg=self.load({'.env': ('\ufeff# PORT=7860'+newline+'PORT="40978"'+newline).encode('utf-8')})
            self.assertEqual(cfg['Server'].PORT,40978)

    def test_optional_empty_and_required_empty_are_distinct(self):
        cfg=self.load({'.env':'PORT=40978\nMEDIA_CACHE_MAX_MB=0\n'}, {'PORT':''})
        self.assertEqual(cfg['Resources'].MEDIA_CACHE_MAX_MB,0)
        with self.assertRaisesRegex(ValueError,'PORT'):
            cfg['validate_config']()
        cfg=self.load(environ={'TELEGRAM_BOT_TOKEN':''})
        with self.assertRaisesRegex(ValueError,'TELEGRAM_BOT_TOKEN'):
            cfg['validate_config']()

    def test_invalid_values_are_sanitized_validation_errors(self):
        for key, value in [('PORT','invalid-private-value'),('PORT','0'),('MEDIA_CACHE_MAX_MB','-1'),
                           ('IO_WORKERS','0'),('BIND_ADDRESS','bad address'),('DATABASE_URL','private-value')]:
            cfg=self.load(environ={key:value})
            with self.assertRaises(ValueError) as caught:
                cfg['validate_config']()
            self.assertIn(key,str(caught.exception))
            self.assertNotIn('private-value',str(caught.exception))
        with self.assertRaisesRegex(ValueError,'line 1') as caught:
            self.load({'.env':'PASSWORD="private-value\n'})
        self.assertNotIn('private-value',str(caught.exception))

    def test_token_separators_placeholders_primary_and_duplicates(self):
        cfg=self.load({'.env': f'MULTI_BOT_TOKENS="{TOKEN_A}, {TOKEN_B};{TOKEN_A}\n {SECRETS["TELEGRAM_BOT_TOKEN"]} token1 YOUR_TOKEN placeholder"\n'})
        self.assertEqual(cfg['Telegram'].MULTI_BOT_TOKENS,[TOKEN_A,TOKEN_B])
        self.assertEqual(cfg['Telegram'].IGNORED_WORKER_TOKENS,3)

    def test_legacy_files_are_fallbacks_and_shell_is_never_executed(self):
        cfg=self.load({'config.env': 'PORT=41234\n', 'start.sh': f'#!/bin/sh\nexport PORT=7860\nexport MULTI_BOT_TOKENS="{TOKEN_A}\n{TOKEN_B}"\nexport IGNORED=$(touch stolen)\npython -m bot\n'})
        self.assertEqual(cfg['Server'].PORT,41234)
        self.assertEqual(cfg['Telegram'].MULTI_BOT_TOKENS,[TOKEN_A,TOKEN_B])
        self.assertEqual(cfg['setting']('IGNORED'),'')

    def test_cache_disabled_and_default_database_names_preserved(self):
        cfg=self.load(environ={'MEDIA_CACHE_MAX_MB':'0'})
        cfg['validate_config']()
        self.assertEqual(cfg['Resources'].MEDIA_CACHE_MAX_MB,0)
        self.assertEqual(cfg['Database'].DATABASE_NAME,'OmniArchiver')
        self.assertEqual(cfg['Database'].OMNI_MONGO_URI,'')

    def test_log_formatter_redacts_messages_and_tracebacks(self):
        cfg=self.load(environ={'MULTI_BOT_TOKENS':TOKEN_A,
                               'DATABASE_URL':'mongodb://name:password@host/db'})
        formatter=cfg['SanitizedFormatter']('%(message)s')
        record=logging.LogRecord('third-party',logging.ERROR,'',1,
            'failure %s %s %s', (TOKEN_A,'mongodb://name:password@host/db',SECRETS['TELEGRAM_API_HASH']),None)
        cfg['SanitizedFilter']().filter(record)
        for secret in (TOKEN_A,'password',SECRETS['TELEGRAM_API_HASH']):
            self.assertNotIn(secret,record.getMessage())
            self.assertNotIn(secret,str(record.args))
        rendered=formatter.format(record)
        for secret in (TOKEN_A,'password',SECRETS['TELEGRAM_API_HASH']):
            self.assertNotIn(secret,rendered)
        try:
            raise RuntimeError(TOKEN_A)
        except RuntimeError:
            import sys
            record=logging.LogRecord('third-party',logging.ERROR,'',1,'failure',(),sys.exc_info())
            self.assertNotIn(TOKEN_A,formatter.format(record))
