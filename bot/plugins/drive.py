import asyncio
import logging
import tempfile
from contextlib import suppress
from threading import Event, Lock
from urllib.parse import urlsplit

from hydrogram import enums, filters
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.clients import TelegramBot
from bot.config import Drive
from bot.modules.decorators import verify_admin, verify_user
from bot.modules.drive import (DriveTransferError, download_file, load_credentials, upload_file,
                               clone_drive, drive_source, drive_index_link, FOLDER_MIME)
from bot.modules.activity import activity
from bot.modules.runtime_settings import uploaded_credentials, SettingsError
from bot.modules.static import get_human_size

def configured_credentials():
    return uploaded_credentials() or load_credentials(Drive.TOKEN_PATH, Drive.TOKEN_PICKLE_B64)


logger = logging.getLogger('bot')
_job_lock = asyncio.Lock()
_job_cancel = Event()
_job_state_lock = Lock()
_job_state = 'No Drive job is running.'


def set_state(stage, done=None, total=None, items=False):
    global _job_state
    if done is None:
        value = stage
    elif items:
        value = f'{stage}: {done}' + (f' / {total} items' if total else ' items found')
    else:
        value = f'{stage}: {get_human_size(done)}' + (f' / {get_human_size(total)}' if total else '')
    with _job_state_lock:
        _job_state = value


def state_text():
    with _job_state_lock:
        return _job_state


@TelegramBot.on_message(filters.command(['drivestatus', 'canceldrive']) & filters.private)
@verify_user
@verify_admin
async def drive_status(_, message):
    if (message.text or '').split()[0].split('@')[0].lower() == '/canceldrive' and _job_lock.locked():
        _job_cancel.set()
        return await message.reply('Cancelling the Drive job after the current network operation. Use /drivestatus to check.', quote=True)
    return await message.reply(state_text(), quote=True, parse_mode=enums.ParseMode.DISABLED)


@TelegramBot.on_message(filters.command(['drive', 'gdrive', 'mirror', 'clone']) & filters.private)
@verify_user
@verify_admin
async def drive_command(_, message):
    if not Drive.ENABLED:
        return await message.reply('Google Drive transfers are disabled. The owner can enable DRIVE_ENABLED on a host that permits this workload.', quote=True)
    parts = (message.text or message.caption or '').split(maxsplit=1)
    if len(parts) != 2:
        return await message.reply('Mirror a direct file: /mirror <direct-download-url>\nClone a Drive file/folder: /clone <drive-share-link>\n/drive and /gdrive also work.\nProgress: /drivestatus · Cancel: /canceldrive', quote=True, parse_mode=enums.ParseMode.DISABLED)
    source = parts[1].strip()
    try:
        cloning = parts[0].split('@')[0].lower() == '/clone' or urlsplit(source).hostname in ('drive.google.com', 'docs.google.com')
        if cloning:
            drive_source(source)
        drive_index_link(Drive.INDEX_URL, 'configuration-check')
    except (DriveTransferError, ValueError) as error:
        return await message.reply(str(error) if isinstance(error, DriveTransferError) else 'Use a valid source URL.', quote=True, parse_mode=enums.ParseMode.DISABLED)
    if _job_lock.locked():
        return await message.reply('A Drive job is running. Use /drivestatus or /canceldrive.', quote=True)

    async with _job_lock:
        _job_cancel.clear()
        set_state('Checking Drive credentials…')
        status = await message.reply('GateStream · ' + state_text(), quote=True)

        async def refresh():
            last = None
            while True:
                await asyncio.sleep(3)
                text = state_text()
                if text != last:
                    with suppress(Exception):
                        await status.edit_text('GateStream · ' + text + '\n/canceldrive to stop', parse_mode=enums.ParseMode.DISABLED)
                    last = text

        def check_cancel():
            if _job_cancel.is_set():
                raise DriveTransferError('Drive job cancelled.')

        async def blocking(function, *args, **kwargs):
            # A thread may own a temporary file or a Drive write. Wait for it before cleanup.
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                _job_cancel.set()
                with suppress(Exception):
                    await task
                raise

        updater = asyncio.create_task(refresh())
        result = None
        notified = False
        try:
            credentials = await blocking(configured_credentials)
            check_cancel()
            if cloning:
                set_state('Scanning Google Drive…')
                result = await blocking(clone_drive, source, credentials, Drive.FOLDER_ID,
                    Drive.CLONE_MAX_FILES, progress=lambda stage, done, total: set_state(stage, done, total, items=True),
                    cancelled=_job_cancel)
                name = result['name']
            else:
                with tempfile.TemporaryDirectory(prefix='gatestream-drive-') as directory:
                    set_state('Downloading to server…')
                    with activity.transfer('download') as ident:
                        def download_progress(done, total):
                            check_cancel()
                            activity.progress(ident, done, total)
                            set_state('Downloading', done, total)
                        path, name = await download_file(source, directory, Drive.MAX_BYTES, progress=download_progress)
                        activity.progress(ident, path.stat().st_size, path.stat().st_size)
                    check_cancel()
                    set_state('Uploading to Google Drive…')
                    with activity.transfer('upload', path.stat().st_size) as ident:
                        def upload_progress(done, total):
                            activity.progress(ident, done, total)
                            set_state('Uploading', done, total)
                        result = await blocking(upload_file, path, name, credentials, Drive.FOLDER_ID,
                            progress=upload_progress, cancelled=_job_cancel)
                        activity.progress(ident, path.stat().st_size)
            is_folder = result.get('mimeType') == FOLDER_MIME
            link = result.get('webViewLink') or (f"https://drive.google.com/drive/folders/{result['id']}" if is_folder else f"https://drive.google.com/file/d/{result['id']}/view")
            buttons = [[InlineKeyboardButton('Open in Google Drive', url=link)]]
            index = drive_index_link(Drive.INDEX_URL, name, is_folder)
            if index and len(index) <= 2000:
                buttons.append([InlineKeyboardButton('Browse folder' if is_folder else 'Download from index', url=index)])
            count = f" · {result['file_count']} files" if cloning else ''
            text = f"{'Cloned' if cloning else 'Uploaded'} to Google Drive{count}\n\n{name[:1000]}\nDestination folder sharing applies."
            set_state('Drive job completed.')
            # Stop refresh before the terminal message so progress cannot overwrite it.
            updater.cancel()
            with suppress(asyncio.CancelledError):
                await updater
            await status.edit_text(text, parse_mode=enums.ParseMode.DISABLED, reply_markup=InlineKeyboardMarkup(buttons))
            notified = True
        except (DriveTransferError, SettingsError) as error:
            set_state(str(error))
        except asyncio.CancelledError:
            set_state('Drive job interrupted. Check the destination before retrying.')
            raise
        except Exception as error:
            logger.error('Drive transfer failed (%s).', type(error).__name__)
            set_state('Drive job stopped. Check the source, token, quota and destination before retrying. Temporary downloads were removed.')
        finally:
            updater.cancel()
            with suppress(asyncio.CancelledError):
                await updater
            if not notified:
                with suppress(Exception):
                    await status.edit_text(state_text(), parse_mode=enums.ParseMode.DISABLED)
