from hydrogram.types import CallbackQuery
from bot.clients import TelegramBot
from bot.config import Telegram
from bot.database.files import get_file, delete_file
from bot.modules.decorators import verify_user
from bot.modules.telegram import get_message

@TelegramBot.on_callback_query()
@verify_user
async def manage_callback(bot, q: CallbackQuery):
    query = q.data

    if query.startswith('rm_'):
        code = query[3:]
        doc = await get_file(code)
        
        if not doc:
            return await q.answer("File link does not exist or was already revoked.", show_alert=True)

        user_id = q.from_user.id
        owner_or_admin = (user_id in Telegram.ADMIN_IDS) or (user_id == doc.get('user_id'))

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
