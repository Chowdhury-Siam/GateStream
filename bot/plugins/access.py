from hydrogram import enums, filters

from bot.clients import TelegramBot
from bot.config import Telegram
from bot.modules.access import forum_topic, group_id, set_access, set_file_topic
from bot.modules.decorators import verify_admin

ACCESS_HELP = (
    'Manage access\n\n'
    '/auth USER_ID — allow a user\n'
    '/auth GROUP_ID — allow members in that group\n'
    'In a group, send /auth with no ID to allow that group. '
    'Reply to a member with /auth to allow that member directly.\n\n'
    'Use /unauth the same way to remove access.\n'
    'Send /filetolink inside the File To Link topic to enable file tools there.'
)


@TelegramBot.on_message(filters.command(['auth', 'unauth']) & (filters.private | filters.group) & filters.text)
@verify_admin
async def access_command(_, message):
    command, *args = message.command
    if args:
        try:
            target = int(args[0])
        except ValueError:
            target = 0
    elif message.reply_to_message:
        replied = message.reply_to_message
        user = replied.from_user
        target = user.id if user and not getattr(user, 'is_bot', False) and not getattr(replied, 'sender_chat', None) else 0
    else:
        target = group_id(message) or 0
    if len(args) > 1 or not -(2**63) <= target < 2**63 or not target:
        return await message.reply(ACCESS_HELP, quote=True, parse_mode=enums.ParseMode.DISABLED)
    if target == Telegram.OWNER_ID:
        return await message.reply('Owner access is set by OWNER_ID.', quote=True)

    granted = command.lower() == 'auth'
    try:
        await set_access(target, granted)
    except Exception:
        return await message.reply('Could not update access. Try again shortly.', quote=True)
    kind = 'Group' if target < 0 else 'User'
    action = 'authorized' if granted else 'authorization removed'
    await message.reply(f'{kind} {target}: {action}.', quote=True, parse_mode=enums.ParseMode.DISABLED)


@TelegramBot.on_message(filters.command('filetolink') & filters.group & filters.text)
@verify_admin
async def file_to_link_command(_, message):
    topic = forum_topic(message)
    if topic is None:
        return await message.reply('Telegram did not identify a forum topic for this message. Open File To Link and send /filetolink as a new message.', quote=True)
    try:
        await set_file_topic(topic)
    except Exception:
        return await message.reply('Could not save the File To Link topic. Try again shortly.', quote=True)
    await message.reply('File To Link is active in this topic. Authorized members can post files for automatic links or use /index to make a ZIP.',
                        quote=True, parse_mode=enums.ParseMode.DISABLED)
