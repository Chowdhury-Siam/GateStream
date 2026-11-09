import asyncio
import logging

from hydrogram import enums
from hydrogram.types import (
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from bot.config import Telegram
from bot.modules.access import group_id, is_owner

logger = logging.getLogger('bot')


async def register_commands(client):
    common = [BotCommand('start', 'Open GateStream'), BotCommand('index', 'Start or finish a ZIP folder'),
              BotCommand('help', 'Show the menu')]
    private = [*common, BotCommand('catalog', 'Open your personal library')]
    owner = [*private, BotCommand('auth', 'Authorize a user or group'),
             BotCommand('unauth', 'Remove authorization'), BotCommand('stats', 'View server statistics'),
             BotCommand('workers', 'Check worker channel membership'),
             BotCommand('workersetup', 'Connect the repair account'),
             BotCommand('workerrepair', 'Add missing workers to a channel'),
             BotCommand('link', 'Link a channel file')]
    for commands, scope in ((common, BotCommandScopeDefault()), (private, BotCommandScopeAllPrivateChats()),
                            (owner, BotCommandScopeChat(Telegram.OWNER_ID))):
        try:
            async with asyncio.timeout(5):
                await client.set_bot_commands(commands, scope=scope)
        except Exception as error:
            logger.warning('Could not set Telegram commands (%s).', type(error).__name__)


async def send_menu(message, user_id):
    in_group = group_id(message) is not None
    upload = 'Reply to this message with a file' if in_group else 'Send me a file'
    text = (
        'GateStream\n\n'
        f'{upload} to get a download link.\n'
        'Use Index to collect files into one ZIP. Send /index My folder to finish with a custom name.'
    )
    if in_group:
        text += '\nGroup access works in this chat. Personal library sign-in requires a user grant.'
    else:
        text += '\nUse Library to view your saved files and ZIP folders.'
    buttons = [[InlineKeyboardButton('Index', callback_data='menu:index')]]
    if not in_group:
        buttons[0].append(InlineKeyboardButton('Library', callback_data='menu:library'))
    buttons.append([InlineKeyboardButton('My ID', callback_data='menu:id')])
    if is_owner(user_id):
        buttons[-1].append(InlineKeyboardButton('Manage access', callback_data='menu:access'))
        if not in_group:
            buttons.append([InlineKeyboardButton('Set up worker repair', callback_data='menu:workersetup')])
    await message.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED,
                        reply_markup=InlineKeyboardMarkup(buttons))
