import asyncio
from html import escape
from hydrogram import enums, filters
from hydrogram.errors import FloodWait
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.database.files import save_file
from bot.modules.decorators import verify_user
from bot.modules.telegram import get_file_properties
from bot.modules.static import get_human_size
from bot.modules.access import group_id, is_owner
from bot.modules import personal_library as library

async def is_reply_to_bot(_, client, message):
    replied = message.reply_to_message
    author = getattr(replied, 'from_user', None)
    return bool(author and client.me and author.id == client.me.id)


group_upload = filters.group & filters.reply & filters.create(is_reply_to_bot)


@TelegramBot.on_message(
    (filters.private | group_upload)
    & (
        filters.document
        | filters.video
        | filters.video_note
        | filters.audio
        | filters.voice
        | filters.photo
    )
)
@verify_user
async def handle_user_file(_, msg: Message):
    document = getattr(msg, 'document', None)
    private_name = (getattr(document, 'file_name', '') or '').lower()
    if private_name.endswith('.pickle') or (private_name.endswith('.json') and private_name.startswith(('token', 'credentials'))):
        return await msg.reply('Private credential files must not be uploaded as media.', quote=False)
    if not Telegram.CHANNEL_ID:
        # If user forwards a file from a channel, inform them to use /link
        if is_owner(msg.from_user.id) and msg.forward_from_chat and msg.forward_from_message_id:
            return await msg.reply(
                f"ℹ️ **Channel post detected!**\nUse the link command to index this file without duplication:\n`/link https://t.me/c/{str(msg.forward_from_chat.id).replace('-100', '')}/{msg.forward_from_message_id}`",
                quote=True
            )
        return await msg.reply(
            'Uploads require a storage channel. Ask the owner to configure TELEGRAM_CHANNEL_ID.',
            quote=True
        )

    status_msg = None
    try:
        user_id = msg.from_user.id
        async with library.user_lock(user_id):
            folder = await library.active_folder(user_id, group_id(msg))
            folder_id = folder['_id'] if folder else None
            if folder_id and await library.folder_count(user_id, folder_id) >= library.MAX_FOLDER_FILES:
                return await msg.reply('This index is full. Send /index to finish it before uploading more files.', quote=True)
            if not folder_id:
                status_msg = await msg.reply('Saving your file…', quote=True)
            try:
                forwarded = await msg.copy(chat_id=Telegram.CHANNEL_ID)
            except FloodWait as error:
                if not 0 <= error.value <= 60:
                    raise
                await asyncio.sleep(error.value)
                forwarded = await msg.copy(chat_id=Telegram.CHANNEL_ID)
            file_name, file_size, mime_type, duration, duration_formatted = get_file_properties(forwarded, prefer_caption=False)
            if not file_name or not file_size:
                raise ValueError('The copied message did not contain a downloadable file.')
            doc = await save_file(channel_id=Telegram.CHANNEL_ID, message_id=forwarded.id,
                file_name=file_name, file_size=file_size, mime_type=mime_type, user_id=user_id,
                category='direct_files', duration=duration, duration_formatted=duration_formatted,
                personal=True, personal_index_id=folder_id)
        if folder_id:
            return

        code = doc['code']
        human_size = get_human_size(file_size)
        dl_link = f"{Server.BASE_URL}/dl/{code}"
        stream_link = f"{Server.BASE_URL}/stream/{code}"
        
        actions = [InlineKeyboardButton('Download', url=dl_link)]
        if mime_type.startswith(('video/', 'audio/')):
            actions.append(InlineKeyboardButton('Stream', url=stream_link))
        buttons = [actions, [InlineKeyboardButton('Revoke link', callback_data=f'rm_{code}')]]
        details = human_size
        if duration:
            details += f' · Duration {escape(duration_formatted)}'
        caption = f'<b>File ready</b>\n{escape(file_name)}\n{details}\n\n<code>{escape(dl_link)}</code>'
        await status_msg.edit_text(text=caption, parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

    except Exception:
        if status_msg is not None:
            await status_msg.edit_text('❌ Could not save this file. Please send it again.')
        else:
            await msg.reply('❌ Could not add this file to your index. Please send it again.', quote=True)
