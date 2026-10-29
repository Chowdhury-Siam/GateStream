import logging
from hydrogram import filters
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.modules.decorators import verify_user
from bot.modules.index_access import set_password

logger = logging.getLogger('bot')


@TelegramBot.on_message(filters.command(['setpassword', 'indexpassword']) & filters.private)
@verify_user
async def index_password_command(_, message):
    if not message.from_user or message.from_user.id != Telegram.OWNER_ID:
        return await message.reply('Only the bot owner can change the Link password.', quote=True)
    parts = (message.text or message.caption or '').split(maxsplit=1)
    if len(parts) != 2 or not 1 <= len(parts[1].strip()) <= 256:
        return await message.reply('Usage: /setpassword <new password>\nUse 1–256 characters. Spaces are allowed.', quote=True)
    try:
        await set_password(parts[1].strip())
    except Exception as error:
        logger.error('Link password update failed (%s).', type(error).__name__)
        return await message.reply('The password could not be saved. Please check the database and try again.', quote=False)
    await message.reply('Link password updated. Existing Link sessions have been signed out.', quote=False)
    try:
        await message.delete()
    except Exception:
        pass
