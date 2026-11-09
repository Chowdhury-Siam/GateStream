from hydrogram.types import CallbackQuery
from bot.config import Telegram
from bot.clients import TelegramBot
from hydrogram.enums import ChatType, ParseMode
from bot.modules.access import group_id, is_file_topic, is_owner
from bot.database.files import get_file, delete_file
from bot.modules.decorators import verify_user
from bot.modules.telegram import get_message
from bot.modules.index_zip import select_zip

@TelegramBot.on_callback_query()
@verify_user
async def manage_callback(bot, q: CallbackQuery):
    query = q.data or ''
    if query.startswith('menu:'):
        return await menu_callback(q, query.removeprefix('menu:'))
    if query.startswith('zip:'):
        return await select_zip(q)

    if query.startswith('rm_'):
        code = query[3:]
        doc = await get_file(code)
        
        if not doc:
            return await q.answer("File link does not exist or was already revoked.", show_alert=True)

        user_id = q.from_user.id
        owner_or_admin = is_owner(user_id) or (user_id == doc.get('user_id'))

        if not owner_or_admin:
            return await q.answer("❌ You are not authorized to revoke this link.", show_alert=True)

        # Delete database record
        await delete_file(code)

        # Invalidate Telegram message cache across all workers
        chan_id = doc.get('channel_id')
        msg_id = doc.get('message_id')
        if chan_id and msg_id:
            from bot.modules.telegram import invalidate_message_cache
            invalidate_message_cache(chan_id, msg_id)
        for ep in doc.get('episodes', []):
            ep_msg_id = ep.get('message_id')
            if chan_id and ep_msg_id:
                from bot.modules.telegram import invalidate_message_cache
                invalidate_message_cache(chan_id, ep_msg_id)
        
        # If it was a direct file uploaded to the bin channel, optionally delete channel message
        if doc.get('category') == 'direct_files' and doc.get('channel_id') == Telegram.CHANNEL_ID:
            try:
                msg = await get_message(doc['channel_id'], doc['message_id'])
                if msg:
                    await msg.delete()
            except Exception:
                pass

        try:
            await q.message.edit_text(f"🗑️ **Link Revoked**\nThe file `{doc.get('file_name', '')}` has been removed from the database.")
        except Exception:
            pass

        await q.answer("✅ Link permanently revoked!", show_alert=True)
    else:
        await q.answer("Invalid query.", show_alert=True)


async def menu_callback(query, action):
    message = query.message
    user_id = query.from_user.id
    if not message:
        return await query.answer('Open the menu with /start.', show_alert=True)
    if action == 'id':
        chat_id = group_id(message)
        text = f'Your Telegram ID: {user_id}'
        if chat_id:
            text += f'\nGroup ID: {chat_id}'
        return await query.answer(text, show_alert=True)
    if action == 'index':
        if group_id(message) is not None and not await is_file_topic(message):
            return await query.answer('Open Index in the File To Link topic.', show_alert=True)
        from bot.plugins.personal_index import personal_index
        await query.answer()
        return await personal_index(message, user_id=user_id)
    if action == 'library' and message.chat.type == ChatType.PRIVATE:
        from bot.modules.personal_library import send_login_link
        await query.answer()
        return await send_login_link(message, user_id=user_id)
    if action == 'workersetup' and message.chat.type == ChatType.PRIVATE and is_owner(user_id):
        from bot.modules.personal_library import send_login_link
        await query.answer()
        return await send_login_link(message, user_id=user_id, worker_setup=True)
    if action == 'access' and is_owner(user_id):
        from bot.plugins.access import ACCESS_HELP
        await query.answer()
        return await message.reply(ACCESS_HELP, quote=True, parse_mode=ParseMode.DISABLED)
    await query.answer('This action is unavailable here.', show_alert=True)
