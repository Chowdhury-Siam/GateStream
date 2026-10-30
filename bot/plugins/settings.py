"""Private owner-only settings panel and reply-bound input capture."""
import asyncio
import time
from contextlib import suppress

from hydrogram import StopPropagation, enums, filters
from hydrogram.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from bot.clients import TelegramBot
from bot.config import Drive, Telegram
from bot.modules import runtime_settings as settings

_pending = {}
CLEARABLE = {'DRIVE_FOLDER_ID', 'DRIVE_INDEX_URL', 'MAL_CLIENT_ID', 'OMDB_API_KEY', 'TELEGRAM_CHANNEL_ID'}
DRIVE_KEYS = [key for key in settings.FIELDS if key.startswith('DRIVE_')]


def owner(message, user=None):
    user = user or message.from_user
    return bool(user and user.id == Telegram.OWNER_ID and message.chat.id == Telegram.OWNER_ID)


def button(label, action):
    return [[InlineKeyboardButton(label, callback_data='bset:' + action)]]


def value_text(key):
    value = settings.current(key)
    if key in settings.SECRETS:
        return 'Configured' if value else 'Not set'
    if key == 'DRIVE_ENABLED':
        return 'On' if value else 'Off'
    if key == 'DRIVE_FOLDER_ID' and not value:
        return 'My Drive root'
    return str(value) if value != '' else 'Not set'


def panel(page='home', notice=''):
    rows = []
    if page == 'home':
        text = '✦ BOT SETTINGS\nConfigure GateStream services and download options.'
        for label, target in [('⚙ CONFIG VARIABLES', 'config:0'), ('✦ MODULE SETTINGS', 'modules'),
                              ('☁ GOOGLE DRIVE', 'drive'), ('🔐 PRIVATE FILES', 'private'), ('📊 STATUS', 'status')]:
            rows += button(label, target)
    elif page.startswith('config:'):
        page_number = min(1, max(0, int(page.split(':')[1])))
        text = '⚙ CONFIG VARIABLES\nSelect a setting to edit. Secrets are hidden.\nHost bootstrap credentials and database names stay on the host.'
        keys = list(settings.FIELDS)
        for key in keys[page_number * 6:(page_number + 1) * 6]:
            rows += button(f'{key}: {value_text(key)[:40]}', 'field:' + key)
        rows += button('Next →' if page_number == 0 else '← Previous', 'config:' + str(1 - page_number))
    elif page == 'drive':
        text = ('☁ GOOGLE DRIVE\nMirror direct URLs or clone Drive files/folders.\n'
                f'Credentials: {settings.token_state()}\nEnable only on hosting that permits these transfers.')
        for key in DRIVE_KEYS:
            rows += button(f'{settings.FIELDS[key][2]}: {value_text(key)[:40]}', 'field:' + key)
        rows += button('🔐 Set Drive token', 'private')
        rows += button('✓ Check Drive connection', 'check')
        rows += button('Transfer commands', 'drivehelp')
    elif page == 'private':
        text = ('🔐 PRIVATE FILES\nDrive credentials: ' + settings.token_state() + '\n\n'
                'Upload an OAuth authorized-user token.json here. It is encrypted in MongoDB and never indexed as media. '
                'Host token.pickle settings remain supported. Uploaded credentials take precedence.')
        rows += button('Upload Drive token.json', 'edit:DRIVE_TOKEN')
        rows += button('Check Drive connection', 'check')
        rows += button('Remove uploaded token', 'remove')
    elif page == 'remove':
        text = 'Remove the uploaded Drive token?\nExisting host credentials, if configured, will be used again. Google authorization is not revoked.'
        rows += button('Remove uploaded token', 'removeconfirm')
        rows += button('Keep token', 'private')
    elif page == 'modules':
        text = '✦ MODULE SETTINGS\nChoose a GateStream module.'
        rows += button('☁ Google Drive', 'drive')
        rows += button('📦 ZIP cache limit', 'field:ZIP_CACHE_MAX_MB')
        rows += button('🎞 MyAnimeList client ID', 'field:MAL_CLIENT_ID')
        rows += button('🎬 OMDb API key', 'field:OMDB_API_KEY')
        rows += button('🔐 Private links password', 'edit:LINK_PASSWORD')
        rows += button('📁 Private upload channel', 'field:TELEGRAM_CHANNEL_ID')
    elif page.startswith('field:'):
        key = page.split(':')[1]
        if key not in settings.FIELDS:
            raise settings.SettingsError('Unknown setting.')
        text = f'{settings.FIELDS[key][2]}\n{key}\n\nCurrent: {value_text(key)}\nChanges apply immediately and survive restarts.'
        if key == 'DRIVE_ENABLED':
            rows += button('Turn off' if Drive.ENABLED else 'Turn on', 'toggle')
        else:
            rows += button('Edit value', 'edit:' + key)
        if key in CLEARABLE:
            rows += button('Clear value', 'clear:' + key)
        rows += button('Use host default', 'reset:' + key)
        rows += button('← Google Drive' if key in DRIVE_KEYS else '← Config Variables', 'drive' if key in DRIVE_KEYS else 'config:0')
    elif page == 'drivehelp':
        text = ('☁ DRIVE COMMANDS\n/mirror <direct-file-url>\n/clone <drive-file-or-folder-link>\n'
                '/drivestatus — progress\n/canceldrive — stop the current job\n\n'
                'Set the destination and token, check the connection, then enable Drive. '
                'The index URL must point to that same destination folder.')
        rows += button('← Google Drive', 'drive')
    elif page == 'status':
        from bot.plugins.drive import state_text
        text = ('📊 SETTINGS STATUS\nDrive: ' + value_text('DRIVE_ENABLED') + '\nCredential source: ' + settings.token_state() +
                f'\nZIP budget: {value_text("ZIP_CACHE_MAX_MB")} MiB\n\n{state_text()}\n\n'
                'Saved settings use the existing MongoDB settings collection. Host connection settings are unchanged.')
    else:
        raise settings.SettingsError('Unknown settings page.')
    if notice:
        text += '\n\n' + notice
    if page != 'home':
        rows += button('← Back to settings', 'home')
    rows += button('✕ CLOSE', 'close')
    return text, InlineKeyboardMarkup(rows)


async def show(message, page='home', notice='', edit=True):
    text, keyboard = panel(page, notice)
    method = message.edit_text if edit else message.reply
    options = {} if edit else {'quote': False}
    await method(text, reply_markup=keyboard, parse_mode=enums.ParseMode.DISABLED,
                 disable_web_page_preview=True, **options)


async def clear_pending(chat_id):
    pending = _pending.pop(chat_id, None)
    if pending:
        with suppress(Exception):
            await pending['prompt'].delete()


async def mutate(key, function, *args, **kwargs):
    if key.startswith('DRIVE_'):
        from bot.plugins.drive import _job_lock
        if _job_lock.locked():
            raise settings.SettingsError('A Drive job is running. Wait for it or use /canceldrive before changing Drive settings.')
        async with _job_lock:
            return await function(*args, **kwargs)
    return await function(*args, **kwargs)


async def prompt(message, key):
    await clear_pending(message.chat.id)
    if key == 'DRIVE_TOKEN':
        text = ('🔐 Reply with your OAuth token.json as a document (up to 64 KiB).\n'
                'Use a token you generated yourself. Pickle files and client-secrets JSON are not accepted in chat.\n'
                'The uploaded message will be deleted when possible. /cancelsettings to cancel.')
    elif key == 'LINK_PASSWORD':
        text = ('Reply with a new Private links password (1–256 characters).\n'
                'This signs out existing Link sessions and invalidates protected ZIP links.\n'
                'Your reply will be deleted when possible. /cancelsettings to cancel.')
    else:
        if key not in settings.FIELDS or key == 'DRIVE_ENABLED':
            raise settings.SettingsError('Choose a setting from the menu.')
        hints = {'DRIVE_FOLDER_ID':'Send the destination folder ID or folder URL; root selects My Drive.',
                 'DRIVE_INDEX_URL':'Send its HTTPS Bhadoo folder URL, e.g. https://index.example/0:/Mirror/.',
                 'TELEGRAM_CHANNEL_ID':'Send the -100 storage channel ID, or 0 to disable private media uploads.'}
        text = (f'Reply with {settings.FIELDS[key][2]}.\n' + hints.get(key, 'Send one value, without the variable name.') +
                '\n/default restores the host value. ' + ('/clear clears the value. ' if key in CLEARABLE else '') +
                '/cancelsettings to cancel. Replies are removed when possible.')
    sent = await message.reply(text, reply_markup=ForceReply(selective=True), parse_mode=enums.ParseMode.DISABLED)
    _pending[message.chat.id] = dict(key=key, prompt=sent, expires=time.monotonic() + 600)


@TelegramBot.on_message(filters.command(['bset', 'botsettings', 'dset', 'driveset', 'cancelsettings']) & filters.private, group=-20)
async def bot_settings(_, message):
    if not owner(message):
        await message.reply('Only the bot owner can manage settings.', quote=True)
        raise StopPropagation
    await clear_pending(message.chat.id)
    command = (message.text or '').split()[0].split('@')[0].lower()
    await show(message, 'drive' if command in ('/dset', '/driveset') else 'home', edit=False)
    raise StopPropagation


@TelegramBot.on_callback_query(filters.regex(r'^bset:'), group=-20)
async def settings_callback(_, query):
    if not query.message or not owner(query.message, query.from_user):
        await query.answer('Only the bot owner can manage settings in private chat.', show_alert=True)
        raise StopPropagation
    await query.answer()
    action = query.data[5:]
    message = query.message
    try:
        await clear_pending(message.chat.id)
        if action == 'close':
            await message.delete()
        elif action.startswith('edit:'):
            await prompt(message, action[5:])
        elif action == 'toggle':
            if not Drive.ENABLED:
                settings.uploaded_credentials()  # Fail closed if a stored token cannot be decrypted.
            await mutate('DRIVE_ENABLED', settings.save, 'DRIVE_ENABLED', not Drive.ENABLED)
            await show(message, 'drive', 'Drive setting saved.')
        elif action.startswith(('reset:', 'clear:')):
            verb, key = action.split(':', 1)
            if verb == 'clear' and key not in CLEARABLE:
                raise settings.SettingsError('This setting cannot be cleared.')
            await mutate(key, settings.save, key, '' if key != 'TELEGRAM_CHANNEL_ID' else '0', reset=verb == 'reset')
            await show(message, 'field:' + key, 'Saved.')
        elif action == 'removeconfirm':
            await mutate('DRIVE_TOKEN', settings.remove_token)
            await show(message, 'private', 'Uploaded token removed. Host credentials, if present, are used again.')
        elif action == 'check':
            from bot.plugins.drive import configured_credentials
            from bot.modules.drive import check_destination
            await message.edit_text('Checking Google Drive credentials and destination…', parse_mode=enums.ParseMode.DISABLED)
            def check():
                return check_destination(configured_credentials(), Drive.FOLDER_ID)
            await mutate('DRIVE_TOKEN', asyncio.to_thread, check)
            await show(message, 'drive', '✓ Google Drive is reachable and the destination allows file creation.')
        else:
            await show(message, action)
    except Exception as error:
        from bot.modules.drive import DriveTransferError
        notice = str(error) if isinstance(error, (settings.SettingsError, DriveTransferError)) else 'Settings could not be saved or checked. Try again; existing values were retained.'
        with suppress(Exception):
            await show(message, 'home', notice)
    raise StopPropagation


@TelegramBot.on_message(filters.private & (filters.text | filters.document), group=-10)
async def settings_input(client, message):
    if not owner(message):
        return
    pending = _pending.get(message.chat.id)
    if not pending:
        return
    text = message.text or ''
    if text.startswith('/') and text not in ('/default', '/clear'):
        return  # Ordinary commands keep working; /cancelsettings has its own handler.
    reply = message.reply_to_message
    if time.monotonic() >= pending['expires']:
        await clear_pending(message.chat.id)
        with suppress(Exception):
            await message.delete()
        await message.reply('That settings prompt expired. Open /bset and try again.', quote=False)
        raise StopPropagation
    if not reply or reply.id != pending['prompt'].id:
        with suppress(Exception):
            await message.delete()
        await message.reply('Reply directly to the settings prompt, or use /cancelsettings first.', quote=False)
        raise StopPropagation
    # Pop before I/O: a second queued reply cannot execute the same setting input.
    _pending.pop(message.chat.id, None)
    key = pending['key']
    notice = 'Saved. Changes apply immediately.'
    try:
        if key == 'DRIVE_TOKEN':
            document = message.document
            if not document or (document.file_name or '').lower() != 'token.json' or not 0 < (document.file_size or 0) <= settings.MAX_TOKEN_BYTES:
                raise settings.SettingsError('Reply with an OAuth token.json document of up to 64 KiB.')
            # Size is checked before download and again before parsing. Never write
            # owner credentials to the bin channel, media catalog, disk, or logs.
            data = await client.download_media(document, in_memory=True)
            try:
                raw = data.getvalue()
                await mutate(key, settings.store_token, raw)
            finally:
                data.close()
        elif key == 'LINK_PASSWORD':
            from bot.modules.index_access import set_password
            if not isinstance(message.text, str) or not 1 <= len(text.strip()) <= 256:
                raise settings.SettingsError('Use a password of 1–256 characters.')
            await set_password(text.strip())
        else:
            if text == '/clear' and key not in CLEARABLE:
                raise settings.SettingsError('This setting cannot be cleared.')
            if not isinstance(message.text, str):
                raise settings.SettingsError('Reply with a text value.')
            value = '' if text == '/clear' else text
            await mutate(key, settings.save, key, value, reset=text == '/default')
    except Exception as error:
        notice = str(error) if isinstance(error, settings.SettingsError) else 'The change could not be saved. Existing settings were retained; open the menu to retry.'
    finally:
        with suppress(Exception):
            await message.delete()
        with suppress(Exception):
            await pending['prompt'].delete()
    await show(message, 'private' if key == 'DRIVE_TOKEN' else 'drive' if key in DRIVE_KEYS else 'modules', notice, edit=False)
    raise StopPropagation
