from hydrogram import Client 
from hydrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from typing import Union, Callable
from functools import wraps
from bot.config import Telegram
from bot.modules.static import UserNotInAllowedList, AdminOnlyText

def verify_user(func: Callable):
    @wraps(func)
    async def decorator(client: Client, update: Union[Message, CallbackQuery], *args, **kwargs):
        user_id = str(update.from_user.id if update.from_user else update.chat.id)
        numeric_id = update.from_user.id if update.from_user else update.chat.id

        # Admins always allowed
        if numeric_id in Telegram.ADMIN_IDS:
            return await func(client, update, *args, **kwargs)

        if not Telegram.ALLOWED_USER_IDS or user_id in Telegram.ALLOWED_USER_IDS:
            return await func(client, update, *args, **kwargs)
        elif isinstance(update, CallbackQuery):
            return await update.answer(UserNotInAllowedList, show_alert=True)
        elif isinstance(update, Message):
            return await update.reply(
                text=UserNotInAllowedList,
                quote=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Deploy GateStream', url='https://github.com/Chowdhury-Siam/FTL')]])
            )
        
    return decorator

def verify_admin(func: Callable):
    @wraps(func)
    async def decorator(client: Client, update: Union[Message, CallbackQuery], *args, **kwargs):
        numeric_id = update.from_user.id if update.from_user else (update.chat.id if update.chat else None)
        
        if numeric_id in Telegram.ADMIN_IDS:
            return await func(client, update, *args, **kwargs)
        elif isinstance(update, CallbackQuery):
            return await update.answer(AdminOnlyText, show_alert=True)
        elif isinstance(update, Message):
            return await update.reply(text=AdminOnlyText, quote=True)
        
    return decorator
