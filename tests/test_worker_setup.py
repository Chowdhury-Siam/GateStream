import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hydrogram.errors import SessionPasswordNeeded

with patch.dict(os.environ, {
    'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'test_hash',
    'TELEGRAM_BOT_TOKEN': '12345:test_token', 'TELEGRAM_BOT_USERNAME': 'TestGateStreamBot',
    'OWNER_ID': '12345', 'DATABASE_URL': 'mongodb://localhost:27017',
    'SPACE_ID': 'user/app', 'SPACE_HOST': 'user-app.hf.space',
}, clear=True):
    from bot.modules import personal_library as library, worker_setup
    from bot.server import instance, personal_library as routes


class WorkerSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_setup_saves_only_verified_owner_session_without_displaying_credentials(self):
        owner = library.Telegram.OWNER_ID
        session_rows = {}

        async def insert(row):
            session_rows[row['_id']] = row

        async def find(query):
            row = session_rows.get(query['_id'])
            if row and row['kind'] == query['kind'] and row['expires_at'] > query['expires_at']['$gt']:
                return row

        async def consume(query):
            row = await find(query)
            return session_rows.pop(query['_id']) if row else None

        async def delete(query):
            session_rows.pop(query['_id'], None)

        sessions = SimpleNamespace(insert_one=insert, find_one=find, find_one_and_delete=consume, delete_one=delete)
        repairs = SimpleNamespace(update_one=AsyncMock())
        store = {'library_sessions': sessions, 'worker_repair': repairs}
        setup = worker_setup.WorkerSetup()
        self.addAsyncCleanup(setup.close)
        client = SimpleNamespace(
            is_connected=True, api_id=12345, api_hash='a' * 32,
            connect=AsyncMock(return_value=False), disconnect=AsyncMock(), invoke=AsyncMock(),
            send_code=AsyncMock(return_value=SimpleNamespace(phone_code_hash='private-code-hash')),
            sign_in=AsyncMock(side_effect=[SimpleNamespace(id=owner + 1, is_bot=False), SessionPasswordNeeded()]),
            check_password=AsyncMock(return_value=SimpleNamespace(id=owner, is_bot=False)),
            export_session_string=AsyncMock(return_value='private-session-value'))
        browser = instance.test_client()
        path = '/index/worker-setup'
        signin = '/index/sign-in?setup=workers'
        msg = SimpleNamespace(reply=AsyncMock())
        phone, code, password = '+12345678901', '852964', 'private-password'

        async def csrf(response):
            html = await response.get_data(as_text=True)
            for secret in (phone, code, password, client.api_hash, client.export_session_string.return_value):
                self.assertNotIn(secret, html)
            self.assertIn('no-store', response.headers['Cache-Control'])
            return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

        with patch.object(library.db, 'db', store), patch.object(library.Server, 'BASE_URL', 'https://gate.test'), \
             patch.object(worker_setup, 'Client', return_value=client) as create_client, \
             patch.object(routes, 'setup', setup):
            self.assertEqual((await browser.get(path, scheme='https')).status_code, 403)
            await library.send_login_link(msg, user_id=owner + 1, worker_setup=True)
            msg.reply.assert_not_called()
            await library.send_login_link(msg, user_id=owner, worker_setup=True)
            url = msg.reply.call_args.kwargs['reply_markup'].inline_keyboard[0][0].url
            self.assertTrue(url.startswith('https://gate.test' + signin + '#'))
            token = url.split('#')[1]
            form = await browser.get(signin, scheme='https')
            form_token = await csrf(form)
            login = {'token': token, 'csrf': form_token}
            self.assertEqual((await browser.post('/index/sign-in', form=login, scheme='https')).status_code, 400)
            regular_token = await library._new_token(owner, 'login', library.LOGIN_AGE)
            wrong_login = {'token': regular_token, 'csrf': form_token}
            self.assertEqual((await browser.post(signin, form=wrong_login, scheme='https')).status_code, 400)
            sign_in = await browser.post(signin, form=login, scheme='https')
            self.assertEqual(sign_in.location, path)
            self.assertIn('Secure', sign_in.headers['Set-Cookie'])
            self.assertIn('HttpOnly', sign_in.headers['Set-Cookie'])
            self.assertEqual((await browser.post(signin, form=login, scheme='https')).status_code, 400)

            setup_page = await browser.get(path, scheme='https')
            self.assertIn("frame-ancestors 'none'", setup_page.headers['Content-Security-Policy'])
            form_token = await csrf(setup_page)
            data = {'action': 'start', 'api_id': '12345', 'api_hash': client.api_hash, 'phone': phone}
            self.assertEqual((await browser.post(path, form=data, scheme='https')).status_code, 400)
            create_client.assert_not_called()

            async def submit(values):
                return await browser.post(path, form={**values, 'csrf': form_token}, scheme='https')

            first_code_page = await submit(data)
            self.assertIn('Verify code', await first_code_page.get_data(as_text=True))
            await csrf(first_code_page)
            mismatch = await submit({'action': 'code', 'code': code})
            self.assertIn('matches OWNER_ID', await mismatch.get_data(as_text=True))
            repairs.update_one.assert_not_awaited()
            client.export_session_string.assert_not_awaited()
            client.invoke.assert_awaited_once()

            await submit(data)
            password_page = await submit({'action': 'code', 'code': code})
            self.assertIn('two-step password', await password_page.get_data(as_text=True))
            await csrf(password_page)
            success = await submit({'action': 'password', 'password': password})
            html = await success.get_data(as_text=True)
            self.assertIn('Worker repair is connected', html)
            for secret in (phone, code, password, client.api_hash, client.export_session_string.return_value):
                self.assertNotIn(secret, html)
            self.assertEqual((await browser.get(path, scheme='https')).status_code, 403)

        repairs.update_one.assert_awaited_once()
        key, update = repairs.update_one.await_args.args
        self.assertEqual(key, {'_id': owner})
        stored = update['$set']
        self.assertEqual(set(stored), {'credentials'})
        for secret in (phone, code, password, client.api_hash, client.export_session_string.return_value):
            self.assertNotIn(secret.encode(), stored['credentials'])
        self.assertEqual(worker_setup.repair_credentials(stored),
                         [12345, client.api_hash, client.export_session_string.return_value])
        self.assertEqual(client.disconnect.await_count, 2)
