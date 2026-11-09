import asyncio
import logging

from hydrogram import enums
from hydrogram.types import (
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from bot.config import Telegram
from bot.modules.access import file_topic_status, group_id, is_owner

logger = logging.getLogger('bot')


async def register_commands(client):
    common = [BotCommand('start', 'Open GateStream'), BotCommand('help', 'Show the menu')]
    private = [*common, BotCommand('index', 'Start or finish a ZIP folder'),
               BotCommand('catalog', 'Open your personal library')]
    owner = [*private, BotCommand('auth', 'Authorize a user or group'),
             BotCommand('unauth', 'Remove authorization'), BotCommand('stats', 'View server statistics'),
             BotCommand('workers', 'Test worker posting in the log'),
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
    owner = is_owner(user_id)
    topic_status = await file_topic_status(message) if in_group else 'active'
    text = 'GateStream\n\n'
    buttons = []
    if topic_status == 'active':
        upload = 'Post a file in this topic' if in_group else 'Send me a file'
        text += (f'{upload} to get a download link.\n'
                 'Use Index to collect files into one ZIP. Send /index My folder to finish with a custom name.')
        buttons.append([InlineKeyboardButton('Index', callback_data='menu:index')])
    elif owner:
        text += {
            'no_topic': 'Telegram did not identify a forum topic for this message. Open File To Link and send /filetolink as a new message.',
            'unbound': 'No File To Link topic is saved. If this is File To Link, send /filetolink here.',
            'different': 'This topic differs from the saved File To Link topic. If this is File To Link, send /filetolink here to rebind it.',
            'unavailable': 'Could not read the File To Link setting from MongoDB. Try /start again. If it persists, check /log in private.',
        }[topic_status]
    else:
        text += 'Group file tools are available in the File To Link topic.'
    if in_group:
        text += '\nPersonal library sign-in requires a user grant.'
    else:
        text += '\nUse Library to view your saved files and ZIP folders.'
        buttons[0].append(InlineKeyboardButton('Library', callback_data='menu:library'))
    buttons.append([InlineKeyboardButton('My ID', callback_data='menu:id')])
    if owner:
        buttons[-1].append(InlineKeyboardButton('Manage access', callback_data='menu:access'))
        if not in_group:
            buttons.append([InlineKeyboardButton('Set up worker repair', callback_data='menu:workersetup')])
    await message.reply(text, quote=True, parse_mode=enums.ParseMode.DISABLED,
                        reply_markup=InlineKeyboardMarkup(buttons))
