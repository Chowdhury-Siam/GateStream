"""Start personal ZIP downloads from the Telegram index completion message."""
import asyncio
import logging
from html import escape
from urllib.parse import quote

from hydrogram.enums import ChatType, ParseMode
from hydrogram.errors import MessageNotModified
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Server
from bot.modules import personal_library as library
from bot.modules.archive import archive_filename

logger = logging.getLogger('bot')
_jobs = {}


async def select_zip(query):
    # Allow the buttons already sent by older bot versions to recover a link.
    _, _, selection = query.data.partition(':')
    mode, _, folder_id = selection.partition(':')
    if (mode != 'stream' or not library.valid_token(folder_id)
            or not query.message or query.message.chat.type != ChatType.PRIVATE):
        return await query.answer('Open the folder from /catalog to get a ZIP link.', show_alert=True)

    key = (query.from_user.id, folder_id)
    try:
        async with asyncio.timeout(8):
            folder = await library.folder_by_id(folder_id, query.from_user.id)
    except Exception as error:
        logger.warning('ZIP folder lookup failed (%s).', type(error).__name__)
        return await query.answer('Could not open the folder. Please try again.', show_alert=True)
    if not folder:
        return await query.answer('This folder is unavailable for your account.', show_alert=True)
    if key in _jobs:
        return await query.answer('This ZIP is already starting.')

    task = asyncio.create_task(_deliver_zip(query.message, folder))
    _jobs[key] = task
    task.add_done_callback(lambda _: _jobs.pop(key, None))
    await query.answer()


async def _zip_reply(folder):
    from bot.server import catalog

    folder_id, user_id = folder['_id'], folder['user_id']
    try:
        async with asyncio.timeout(25):
            files = await library.folder_files(user_id, folder_id)
            if not files:
                raise ValueError('This folder has no downloadable files.')
            name = archive_filename(folder['name'])
            token = await catalog.create_archive(files, name, folder_id=folder_id, user_id=user_id)

        url = f'{Server.BASE_URL}/index/archive/{token}/{quote(name, safe="")}'
        count = len(files)
        text = (f'<b>ZIP download</b>\n<b>{escape(name)}</b> · {count} {"file" if count == 1 else "files"}\n\n'
                f'Copy link\n<code>{escape(url)}</code>')
        button = InlineKeyboardMarkup([[InlineKeyboardButton('Download', url=url)]])
        return text, button
    except ValueError as error:
        return f'Could not start the ZIP. {escape(str(error))}', None
    except Exception as error:
        logger.warning('Telegram ZIP request failed (%s).', type(error).__name__)
        return 'Folder saved. Open it from /catalog to get a new ZIP link.', None


async def reply_zip(message, folder):
    text, button = await _zip_reply(folder)
    await message.reply(text, quote=True, parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True, reply_markup=button)


async def _deliver_zip(message, folder):
    text, button = await _zip_reply(folder)
    kwargs = dict(parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=button)
    try:
        await message.edit_text(text, **kwargs)
    except MessageNotModified:
        pass
    except Exception:
        try:
            await message.reply_text(text, quote=False, **kwargs)
        except Exception as error:
            logger.warning('Could not deliver the Telegram ZIP response (%s).', type(error).__name__)
