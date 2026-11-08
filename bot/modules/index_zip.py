"""Start personal ZIP downloads from the Telegram index completion message."""
import asyncio
import logging
from itertools import cycle
from urllib.parse import quote

from hydrogram.enums import ChatType, ParseMode
from hydrogram.errors import MessageNotModified
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import Server
from bot.modules import personal_library as library
from bot.modules.archive import archive_filename, archive_size
from bot.modules.static import get_human_size

logger = logging.getLogger('bot')
_jobs = {}
_PROGRESS_INTERVAL = 2


def zip_choices(folder_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('Prepare first', callback_data=f'zip:prepare:{folder_id}'),
        InlineKeyboardButton('Start immediately', callback_data=f'zip:stream:{folder_id}'),
    ]])


async def select_zip(query):
    _, _, selection = query.data.partition(':')
    mode, _, folder_id = selection.partition(':')
    if (mode not in ('prepare', 'stream') or not library.valid_token(folder_id)
            or not query.message or query.message.chat.type != ChatType.PRIVATE):
        return await query.answer('This ZIP choice is no longer available.', show_alert=True)

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
        return await query.answer('This ZIP is already being started or prepared.')

    task = asyncio.create_task(_deliver_zip(query.message, folder, mode))
    _jobs[key] = task
    task.add_done_callback(lambda _: _jobs.pop(key, None))
    await query.answer()


async def _show_progress(message, text):
    try:
        await message.edit_text(text, parse_mode=ParseMode.DISABLED, reply_markup=None)
    except Exception as error:
        logger.debug('ZIP progress could not be edited (%s).', type(error).__name__)


async def _wait_for_zip(message, cache, token, total):
    for frame in cycle(('◐', '◓', '◑', '◒')):
        status = cache.status(token, total)
        if status['state'] == 'ready':
            return True
        if status['state'] not in ('queued', 'preparing'):
            return False
        done = status['bytes']
        percent = min(99, 100 * done // total)
        filled = percent // 10
        bar = '█' * filled + '░' * (10 - filled)
        title = 'Waiting to prepare your ZIP' if status['state'] == 'queued' else 'Preparing your ZIP'
        await _show_progress(message, f'{frame} {title}\n{bar} {percent}%\n'
                                      f'{get_human_size(done)} / {get_human_size(total)}')
        await asyncio.sleep(_PROGRESS_INTERVAL)


async def _deliver_zip(message, folder, mode):
    from bot.server import catalog

    folder_id, user_id = folder['_id'], folder['user_id']
    options = zip_choices(folder_id)
    try:
        if mode == 'prepare':
            await _show_progress(message, '◐ Preparing your ZIP\n░░░░░░░░░░ 0%')
        async with asyncio.timeout(25):
            files = await library.folder_files(user_id, folder_id)
            if not files:
                raise ValueError('This folder has no downloadable files.')
            name = archive_filename(folder['name'])
            token = await catalog.create_archive(files, mode, name, folder_id=folder_id, user_id=user_id)

        ready = True
        if mode == 'prepare':
            async with asyncio.timeout(catalog.ARCHIVE_AGE):
                ready = await _wait_for_zip(message, catalog.archive_cache, token, archive_size(files))
        if ready:
            url = f'{Server.BASE_URL}/index/archive/{token}/{quote(name, safe="")}'
            title = 'Your ZIP is ready.' if mode == 'prepare' else 'Your download can start now while the ZIP is built.'
            text = f'{title}\n{name}\n{url}'
            options = InlineKeyboardMarkup([[InlineKeyboardButton('Download ZIP', url=url)]])
        else:
            text = 'ZIP preparation stopped before it finished. Choose a mode to try again.'
    except ValueError as error:
        text = f'Could not start the ZIP. {error}'
    except Exception as error:
        logger.warning('Telegram ZIP request failed (%s).', type(error).__name__)
        text = 'Could not finish the ZIP request. Choose a mode to try again.'

    kwargs = dict(parse_mode=ParseMode.DISABLED, reply_markup=options, disable_web_page_preview=True)
    try:
        await message.edit_text(text, **kwargs)
    except MessageNotModified:
        pass
    except Exception:
        try:
            await message.reply_text(text, quote=False, **kwargs)
        except Exception as error:
            logger.warning('Could not deliver the Telegram ZIP response (%s).', type(error).__name__)
