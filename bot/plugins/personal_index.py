import logging

from hydrogram import enums, filters

from bot.clients import TelegramBot
from bot.config import Telegram
from bot.modules import personal_library as library
from bot.modules.access import group_id
from bot.modules.decorators import verify_user
from bot.modules.index_zip import reply_zip

logger = logging.getLogger('bot')


@TelegramBot.on_message(filters.command('index') & (filters.private | filters.group) & filters.text)
@verify_user
async def index_command(_, message):
    parts = message.text.split(maxsplit=1)
    await personal_index(message, parts[1] if len(parts) > 1 else '')


@TelegramBot.on_message(filters.command('catalog') & filters.private & filters.text)
@verify_user
async def catalog_command(_, message):
    await library.send_login_link(message)


async def personal_index(message, requested_name='', *, user_id=None):
    if not Telegram.CHANNEL_ID:
        return await message.reply('The bot needs a storage channel before it can collect uploads.', quote=True)
    user_id = user_id if user_id is not None else message.from_user.id
    chat_id = group_id(message)
    finished_folder = None
    try:
        name = library.folder_name(requested_name) if requested_name else None
        async with library.user_lock(user_id):
            folder = await library.active_folder(user_id, chat_id)
            if folder is None:
                await library.create_folder(user_id, name or library.folder_name(''), chat_id)
                upload = 'Send your files here.'
                if chat_id is not None:
                    upload += ' If Telegram privacy mode limits what the bot can see, reply to this message with your files.'
                text = (f'Index started. {upload}\n'
                        f'Add up to {library.MAX_FOLDER_FILES} files. Send /index again to get the ZIP link.\n'
                        'Use /index My folder to finish with a custom name.')
            else:
                count = await library.folder_count(user_id, folder['_id'])
                if not count:
                    await library.discard_empty_folder(folder)
                    text = 'Index closed. No files were added.'
                    if chat_id is not None:
                        text += ' If you sent files here, start a new /index and send them as replies to the bot’s index message.'
                else:
                    name = name or folder['name']
                    await library.finish_folder(folder, name)
                    finished_folder = {**folder, 'name': name}
    except ValueError as error:
        return await message.reply(str(error), quote=True, parse_mode=enums.ParseMode.DISABLED)
    except Exception as error:
        logger.error('Personal index update failed (%s).', type(error).__name__)
        return await message.reply('The index could not be updated. Please try /index again shortly.', quote=True)
    if finished_folder is not None:
        return await reply_zip(message, finished_folder)
    await message.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED, disable_web_page_preview=True)
