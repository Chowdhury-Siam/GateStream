import asyncio
import logging
import tempfile

from hydrogram import enums, filters
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.clients import TelegramBot
from bot.config import Drive
from bot.modules.decorators import verify_admin, verify_user
from bot.modules.drive import DriveTransferError, download_file, load_credentials, upload_file
from bot.modules.activity import activity

logger = logging.getLogger('bot')
_job_lock = asyncio.Lock()


@TelegramBot.on_message(filters.command(['drive', 'gdrive']) & filters.private)
@verify_user
@verify_admin
async def drive_command(_, message):
    if not Drive.ENABLED:
        return await message.reply('Google Drive transfers are disabled. The owner can enable DRIVE_ENABLED on a host that permits this workload.', quote=True)
    parts = (message.text or message.caption or '').split(maxsplit=1)
    if len(parts) != 2:
        return await message.reply('Usage: /drive <direct-download-url>', quote=True, parse_mode=enums.ParseMode.DISABLED)
    if _job_lock.locked():
        return await message.reply('A Drive transfer is already running. Try again when it finishes.', quote=True)

    async with _job_lock:
        status = await message.reply('GateStream · Checking Drive credentials…', quote=True)
        try:
            credentials = await asyncio.to_thread(load_credentials, Drive.TOKEN_PATH, Drive.TOKEN_PICKLE_B64)
            with tempfile.TemporaryDirectory(prefix='gatestream-drive-') as directory:
                await status.edit_text('GateStream · Downloading the file…')
                with activity.transfer('download') as ident:
                    path, name = await download_file(parts[1].strip(), directory, Drive.MAX_BYTES,
                        progress=lambda done, total: activity.progress(ident, done, total))
                    activity.progress(ident, path.stat().st_size, path.stat().st_size)
                await status.edit_text('GateStream · Uploading to Google Drive…')
                with activity.transfer('upload', path.stat().st_size) as ident:
                    task = asyncio.create_task(asyncio.to_thread(upload_file, path, name, credentials, Drive.FOLDER_ID,
                        progress=lambda done, total: activity.progress(ident, done, total)))
                    try:
                        result = await asyncio.shield(task)
                        activity.progress(ident, path.stat().st_size)
                    except asyncio.CancelledError:
                        await task
                        activity.progress(ident, path.stat().st_size)
                        activity.finish(ident)
                        raise
            link = result.get('webViewLink') or f"https://drive.google.com/file/d/{result['id']}/view"
            await status.edit_text(f"GateStream · Uploaded to Google Drive\n\n{name}\nFile sharing permissions were not changed.",
                parse_mode=enums.ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Open in Google Drive', url=link)]]))
        except DriveTransferError as error:
            await status.edit_text(str(error), parse_mode=enums.ParseMode.DISABLED)
        except Exception as error:
            # Signed download URLs and OAuth failures may contain secrets; don't print exception bodies.
            logger.error('Drive transfer failed (%s).', type(error).__name__)
            await status.edit_text('Drive transfer failed. Check the direct link, network connection, token and free disk space. Temporary downloads were removed.',
                parse_mode=enums.ParseMode.DISABLED)
