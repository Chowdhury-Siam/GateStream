"""Owner-editable settings, persisted independently of host/bootstrap secrets."""
import base64
import hashlib
import json
import logging
import re

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from bot.config import Drive, Metadata, Telegram
from bot.database import db
from bot.modules.archive_cache import archive_cache

logger = logging.getLogger('bot')
# Only values whose consumers read them at runtime are editable here.
FIELDS = {
    'DRIVE_ENABLED': (Drive, 'ENABLED', 'Drive transfers'),
    'DRIVE_FOLDER_ID': (Drive, 'FOLDER_ID', 'Destination folder'),
    'DRIVE_INDEX_URL': (Drive, 'INDEX_URL', 'Drive index URL'),
    'DRIVE_MAX_MB': (Drive, 'MAX_BYTES', 'Mirror limit (MiB)'),
    'DRIVE_CLONE_MAX_FILES': (Drive, 'CLONE_MAX_FILES', 'Clone item limit'),
    'ZIP_CACHE_MAX_MB': (archive_cache, 'max_bytes', 'ZIP cache limit (MiB)'),
    'TELEGRAM_CHANNEL_ID': (Telegram, 'CHANNEL_ID', 'Private upload channel'),
    'MAL_CLIENT_ID': (Metadata, 'MAL_CLIENT_ID', 'MyAnimeList client ID'),
    'OMDB_API_KEY': (Metadata, 'OMDB_API_KEY', 'OMDb API key'),
}
SECRETS = {'MAL_CLIENT_ID', 'OMDB_API_KEY'}
MIB_FIELDS = {'DRIVE_MAX_MB', 'ZIP_CACHE_MAX_MB'}
MAX_TOKEN_BYTES = 65536
_document_id = 'bot_runtime'
_uploaded_json = None
_token_error = False
_overrides = set()


class SettingsError(ValueError):
    pass


def current(key):
    obj, attr, _ = FIELDS[key]
    value = getattr(obj, attr)
    return value // (1024 * 1024) if key in MIB_FIELDS else value


DEFAULTS = {key: current(key) for key in FIELDS}


def collection():
    if db.db is None:
        raise SettingsError('Settings database is unavailable. Try again shortly.')
    return db.db['settings']


def cipher():
    # The high-entropy bot token is already a required host secret. Domain-separate
    # this key; rotating that token requires re-entering stored private values.
    key = hashlib.sha256(b'GateStream/settings/v1\0' + Telegram.BOT_TOKEN.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def apply(key, value):
    obj, attr, _ = FIELDS[key]
    setattr(obj, attr, value * 1024 * 1024 if key in MIB_FIELDS else value)


def validate(key, value):
    if key not in FIELDS:
        raise SettingsError('This setting must be managed on the host.')
    if key == 'DRIVE_ENABLED':
        if type(value) is bool:
            return value
        if str(value).strip().lower() in ('true', 'on', 'yes', '1'):
            return True
        if str(value).strip().lower() in ('false', 'off', 'no', '0'):
            return False
        raise SettingsError('Choose On or Off.')
    if key in MIB_FIELDS or key == 'DRIVE_CLONE_MAX_FILES':
        if not re.fullmatch(r'[0-9]{1,8}', str(value)):
            raise SettingsError('Enter a positive whole number.')
        result = int(value)
        if not 1 <= result <= (10000 if key == 'DRIVE_CLONE_MAX_FILES' else 1048576):
            raise SettingsError('Use 1–10000 clone items, or 1–1048576 MiB for size limits.')
        return result
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise SettingsError('Use a single-line value of at most 2000 characters.')
    value = value.strip()
    if key == 'DRIVE_FOLDER_ID':
        if value == 'root':
            return ''
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', value):
            from bot.modules.drive import drive_source
            try:
                ident, resource = drive_source(value)
                if resource:
                    raise SettingsError('Use a destination shared directly with your Google account; enter its folder ID.')
                return ident
            except ValueError:
                raise SettingsError('Enter a Drive folder ID, folder URL, or root.') from None
            except Exception as error:
                if isinstance(error, SettingsError):
                    raise
                raise SettingsError('Enter a Drive folder ID, folder URL, or root.') from None
    elif key == 'DRIVE_INDEX_URL' and value:
        from bot.modules.drive import drive_index_link
        try:
            drive_index_link(value, 'check')
        except Exception:
            raise SettingsError('Use the HTTPS Bhadoo folder URL, including /0:/path/.') from None
    elif key == 'TELEGRAM_CHANNEL_ID':
        if value in ('', '0'):
            return 0
        if not re.fullmatch(r'-100[0-9]{5,15}', value):
            raise SettingsError('Enter the storage channel ID starting with -100, or 0 to disable DM uploads.')
        return int(value)
    return value


async def save(key, value=None, reset=False):
    if key not in FIELDS:
        raise SettingsError('This setting is not editable in Telegram.')
    value = DEFAULTS[key] if reset else validate(key, value)
    stored = {'encrypted': cipher().encrypt(value.encode()).decode()} if key in SECRETS else value
    update = {'$unset': {f'values.{key}': ''}} if reset else {'$set': {f'values.{key}': stored}}
    await collection().update_one({'_id': _document_id}, update, upsert=True)
    apply(key, value)  # A failed database write must not change the running bot.
    _overrides.discard(key) if reset else _overrides.add(key)


def parse_token(raw):
    try:
        if len(raw) > MAX_TOKEN_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('type', 'authorized_user') != 'authorized_user':
            raise ValueError()
        for key in ('client_id', 'client_secret', 'refresh_token'):
            if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 8192:
                raise ValueError()
        # Rebuild only supported authorized-user fields. Credentials fixes the OAuth
        # endpoint to Google's token endpoint; no arbitrary JSON token URI is used.
        safe = {key: data[key] for key in ('client_id', 'client_secret', 'refresh_token')}
        scopes = data.get('scopes')
        if isinstance(scopes, str):
            scopes = scopes.split()
        if scopes is not None:
            if not isinstance(scopes, list) or any(not isinstance(s, str) or len(s) > 200 for s in scopes) or len(scopes) > 32:
                raise ValueError()
            safe['scopes'] = scopes
        Credentials.from_authorized_user_info(safe)
        return json.dumps(safe, separators=(',', ':'))
    except (ValueError, TypeError, UnicodeError):
        raise SettingsError('Use an OAuth authorized-user token.json with client_id, client_secret and refresh_token. Client-secrets, service-account JSON and pickle files are not accepted in chat.') from None


async def store_token(raw):
    global _uploaded_json, _token_error
    text = parse_token(raw)
    encrypted = cipher().encrypt(text.encode()).decode()
    await collection().update_one({'_id': _document_id}, {'$set': {'drive_token': encrypted}}, upsert=True)
    _uploaded_json, _token_error = text, False


async def remove_token():
    global _uploaded_json, _token_error
    await collection().update_one({'_id': _document_id}, {'$unset': {'drive_token': ''}}, upsert=True)
    _uploaded_json, _token_error = None, False


def token_state():
    return 'Unavailable: upload again' if _token_error else 'Uploaded token.json' if _uploaded_json else 'Host token settings'


def uploaded_credentials():
    if _token_error:
        raise SettingsError('The stored Drive token cannot be decrypted. Upload token.json again in /bset → Private Files, or remove that uploaded token to use host credentials.')
    return Credentials.from_authorized_user_info(json.loads(_uploaded_json)) if _uploaded_json else None


async def load():
    global _uploaded_json, _token_error
    try:
        document = await collection().find_one({'_id': _document_id}) or {}
    except Exception as error:
        logger.warning('Bot settings could not be restored (%s).', type(error).__name__)
        return False
    if not isinstance(document, dict):
        return False
    _overrides.clear()
    for key, value in DEFAULTS.items():
        apply(key, value)
    values = document.get('values', {})
    for key, stored in (values.items() if isinstance(values, dict) else ()):
        if key not in FIELDS:
            continue
        try:
            value = cipher().decrypt(stored['encrypted']).decode() if key in SECRETS else stored
            apply(key, validate(key, str(value) if key == 'TELEGRAM_CHANNEL_ID' else value))
            _overrides.add(key)
        except Exception:
            logger.warning('A saved bot setting could not be restored (%s).', key)
    _uploaded_json, _token_error = None, False
    if document.get('drive_token'):
        try:
            _uploaded_json = parse_token(cipher().decrypt(document['drive_token']))
        except Exception:
            _token_error = True
            Drive.ENABLED = False
            logger.warning('Stored Drive credentials could not be decrypted; Drive is disabled until reconfigured.')
    return True
