from functools import wraps

from hydrogram.types import CallbackQuery

from bot.modules.access import group_id, has_access, is_file_topic, is_owner


def _access_guard(owner_only, *, file_topic_only=False):
    def decorate(func):
        @wraps(func)
        async def checked(client, update, *args, **kwargs):
            callback = isinstance(update, CallbackQuery) or hasattr(update, 'answer')
            message = getattr(update, 'message', None) if callback else update
            user = getattr(update, 'from_user', None)
            if not user or getattr(user, 'is_bot', False) or (not callback and getattr(message, 'sender_chat', None)):
                return
            chat_id = group_id(message)
            if file_topic_only and chat_id is not None and not await is_file_topic(message):
                return
            allowed = is_owner(user.id) if owner_only else await has_access(user.id, chat_id)
            if allowed:
                return await func(client, update, *args, **kwargs)
            if callback:
                return await update.answer('This action is restricted to the owner.' if owner_only
                                           else 'The owner has not granted you access.', show_alert=True)
            if owner_only or getattr(message, 'command', None):
                await message.reply('Only the owner can use this command.' if owner_only
                                    else 'The owner has not granted you access. Your Telegram ID is '
                                         f'{user.id}.', quote=True)
        return checked
    return decorate


verify_user = _access_guard(False)
verify_file_user = _access_guard(False, file_topic_only=True)
verify_admin = _access_guard(True)
