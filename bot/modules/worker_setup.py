"""Authorize and store the owner account for worker repair."""
import asyncio
import hmac
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from secrets import token_bytes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hydrogram import Client, raw
from hydrogram.errors import FloodWait, PasswordHashInvalid, PhoneCodeInvalid, SessionPasswordNeeded

from bot.config import Telegram
from bot.database import db

SETUP_AGE = 5 * 60


def _owner_key():
    return {'_id': Telegram.OWNER_ID}


def _cipher():
    context = f'GateStream worker repair v1:{Telegram.OWNER_ID}'.encode()
    return AESGCM(hmac.digest(Telegram.BOT_TOKEN.encode(), context, 'sha256'))


async def _save_credentials(api_id, api_hash, session):
    nonce = token_bytes(12)
    payload = json.dumps([api_id, api_hash, session]).encode()
    ciphertext = _cipher().encrypt(nonce, payload, None)
    await db.db['worker_repair'].update_one(
        _owner_key(), {'$set': {'credentials': nonce + ciphertext}}, upsert=True)


def repair_credentials(checkpoint):
    encrypted = checkpoint.get('credentials')
    if encrypted is not None:
        return json.loads(_cipher().decrypt(encrypted[:12], encrypted[12:], None))
    if Telegram.REPAIR_API_ID > 0 and Telegram.REPAIR_API_HASH and Telegram.REPAIR_SESSION:
        return Telegram.REPAIR_API_ID, Telegram.REPAIR_API_HASH, Telegram.REPAIR_SESSION
    return None


@dataclass(repr=False)
class PendingLogin:
    browser: str
    client: Client
    phone: str
    code_hash: str = ''
    step: str = 'code'
    attempts: int = 0


class WorkerSetup:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.pending = None
        self.expiry = None
        self.retry_at = 0

    def step(self, browser):
        if self.pending and self.pending.browser == browser:
            return self.pending.step
        return 'start'

    async def _clear(self):
        pending, self.pending = self.pending, None
        expiry, self.expiry = self.expiry, None
        if expiry and expiry is not asyncio.current_task():
            expiry.cancel()
        if pending:
            client = pending.client
            with suppress(Exception):
                async with asyncio.timeout(5):
                    if client.is_connected:
                        await client.disconnect()
                    else:
                        # A timed-out connect can create a transport before Hydrogram sets is_connected.
                        try:
                            if client.session:
                                await client.session.stop()
                        finally:
                            await client.storage.close()

    async def close(self):
        async with self.lock:
            await self._clear()

    async def _expire(self):
        await asyncio.sleep(SETUP_AGE)
        await self.close()

    async def submit(self, browser, form):
        async with self.lock:
            action = form.get('action')
            step = self.step(browser)
            if self.pending and self.pending.browser != browser:
                return step, 'A setup is already open in another browser. Wait five minutes or finish it there.'
            if action == 'cancel':
                await self._clear()
                return 'start', 'Setup canceled.'
            if action != step:
                return step, 'This step expired or was already submitted. Use the form below.'
            if time.monotonic() < self.retry_at:
                return step, 'Telegram paused sign-in. Wait and try again later.'
            try:
                async with asyncio.timeout(20):
                    if step == 'start':
                        return await self._start(browser, form)
                    return await self._verify(form)
            except FloodWait as error:
                self.retry_at = time.monotonic() + error.value
                await self._clear()
                return 'start', f'Telegram paused sign-in. Wait at least {error.value} seconds before trying again.'
            except (PhoneCodeInvalid, PasswordHashInvalid):
                if self.pending and self.pending.attempts < 5:
                    return step, 'Telegram rejected that entry. Check it and try again.'
                await self._clear()
                return 'start', 'Too many attempts. Request a new code.'
            except Exception:
                await self._clear()
                return 'start', 'Setup could not finish. Check the details and try again.'
            except asyncio.CancelledError:
                await self._clear()
                raise

    async def _start(self, browser, form):
        api_id = form.get('api_id', '').strip()
        api_hash = form.get('api_hash', '').strip()
        phone = form.get('phone', '').strip()
        if (not re.fullmatch(r'[0-9]{1,10}', api_id) or not 0 < int(api_id) <= 2**31 - 1
                or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash)
                or not re.fullmatch(r'\+[1-9][0-9]{6,14}', phone)):
            return 'start', 'Enter a valid API ID, API hash, and phone number with country code.'
        client = Client('worker_repair_setup', api_id=int(api_id), api_hash=api_hash,
                        in_memory=True, no_updates=True, sleep_threshold=0)
        self.pending = PendingLogin(browser, client, phone)
        await client.connect()
        self.pending.code_hash = (await client.send_code(phone)).phone_code_hash
        self.expiry = asyncio.create_task(self._expire())
        return 'code', ''

    async def _verify(self, form):
        pending = self.pending
        client = pending.client
        if pending.step == 'code':
            code = form.get('code', '').strip()
            if not re.fullmatch(r'[0-9]{3,12}', code):
                return 'code', 'Enter the login code Telegram sent.'
            pending.attempts += 1
            try:
                user = await client.sign_in(pending.phone, pending.code_hash, code)
            except SessionPasswordNeeded:
                pending.step = 'password'
                pending.attempts = 0
                pending.phone = pending.code_hash = ''
                return 'password', ''
        else:
            password = form.get('password', '')
            if not password:
                return 'password', 'Enter your Telegram two-step password.'
            pending.attempts += 1
            user = await client.check_password(password)

        if getattr(user, 'id', None) != Telegram.OWNER_ID or getattr(user, 'is_bot', True):
            if getattr(user, 'id', None):
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await client.invoke(raw.functions.auth.LogOut(), retries=1, sleep_threshold=0)
            await self._clear()
            return 'start', 'Sign in with the Telegram account that matches OWNER_ID.'

        session = await client.export_session_string()
        await _save_credentials(client.api_id, client.api_hash, session)
        await self._clear()
        return 'done', ''


setup = WorkerSetup()
