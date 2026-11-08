import asyncio
from hydrogram import filters
from hydrogram.errors import FloodWait
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.database.files import save_file
from bot.modules.decorators import verify_user
from bot.modules.telegram import get_file_properties
from bot.modules.static import MediaLinksText, FileLinksText, get_human_size
from bot.modules import personal_library as library

@TelegramBot.on_message(
    filters.private
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
    if not Telegram.CHANNEL_ID or Telegram.CHANNEL_ID == 0:
        # If user forwards a file from a channel, inform them to use /link
        if msg.forward_from_chat and msg.forward_from_message_id:
            return await msg.reply(
                f"ℹ️ **Channel post detected!**\nUse the link command to index this file without duplication:\n`/link https://t.me/c/{str(msg.forward_from_chat.id).replace('-100', '')}/{msg.forward_from_message_id}`",
                quote=True
            )
        return await msg.reply(
            "ℹ️ **Direct DM uploads disabled.**\nNo storage bin channel is needed! Simply post files to your channel and use:\n• `/link <channel_message_link>`\n• `/batch <category> <start_link> <end_link>`",
            quote=True
        )

    status_msg = None
    try:
        user_id = msg.from_user.id
        async with library.user_lock(user_id):
            folder = await library.active_folder(user_id)
            folder_id = folder['_id'] if folder else None
            if folder_id and await library.folder_count(user_id, folder_id) >= library.MAX_FOLDER_FILES:
                return await msg.reply('This index is full. Send /index to finish it before uploading more files.', quote=True)
            if not folder_id:
                status_msg = await msg.reply('⏳ *Processing file and generating links...*', quote=True)
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
        
        buttons = [
            [
                InlineKeyboardButton('📥 Download', url=dl_link),
                InlineKeyboardButton('▶️ Stream', url=stream_link)
            ],
            [
                InlineKeyboardButton('🗑️ Revoke', callback_data=f"rm_{code}")
            ]
        ]
        
        if (msg.document and 'video' in (msg.document.mime_type or '')) or msg.video:
            caption = MediaLinksText % {
                'file_name': file_name,
                'file_size': human_size,
                'duration': duration_formatted,
                'category': 'Direct Upload',
                'dl_link': dl_link,
                'stream_link': stream_link
            }
        else:
            caption = FileLinksText % {
                'file_name': file_name,
                'file_size': human_size,
                'duration': duration_formatted,
                'category': 'Direct Upload',
                'dl_link': dl_link
            }

        await status_msg.edit_text(
            text=caption,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True
        )

    except Exception:
        if status_msg is not None:
            await status_msg.edit_text('❌ Could not save this file. Please send it again.')
        else:
            await msg.reply('❌ Could not add this file to your index. Please send it again.', quote=True)
