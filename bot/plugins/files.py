import asyncio
from html import escape

from hydrogram import enums, filters
from hydrogram.errors import FloodWait
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.database.files import get_group_upload, save_file
from bot.modules import personal_library as library
from bot.modules.access import forum_topic, is_owner
from bot.modules.decorators import verify_file_user
from bot.modules.static import get_human_size
from bot.modules.telegram import get_file_properties, is_media_message


UPLOAD_MEDIA = (filters.private | filters.group) & (
    filters.document | filters.video | filters.video_note | filters.audio | filters.voice | filters.photo
)


@TelegramBot.on_message(UPLOAD_MEDIA)
@verify_file_user
async def handle_user_file(_, msg: Message):
    await save_upload(msg)


@TelegramBot.on_message(filters.command('link') & filters.group & filters.text)
@verify_file_user
async def group_link_command(_, msg: Message):
    media = msg.reply_to_message
    uploader = getattr(media, 'from_user', None)
    if (not is_media_message(media) or not uploader or uploader.id != msg.from_user.id
            or forum_topic(media) != forum_topic(msg)):
        return await msg.reply('Files posted here get links automatically. To retry, reply to one of your files in this topic with /link.',
                               quote=True, parse_mode=enums.ParseMode.DISABLED)
    await save_upload(media, reply_to=msg)


async def save_upload(msg, *, reply_to=None):
    response = reply_to if reply_to is not None else msg
    topic = forum_topic(msg)
    chat_id = topic['chat_id'] if topic else None
    topic_id = topic['topic_id'] if topic else None
    source_key = f'{chat_id}:{msg.id}' if topic else None
    document = getattr(msg, 'document', None)
    private_name = (getattr(document, 'file_name', '') or '').lower()
    if private_name.endswith('.pickle') or (private_name.endswith('.json') and private_name.startswith(('token', 'credentials'))):
        return await response.reply('Private credential files must not be uploaded as media.', quote=False)
    if not Telegram.CHANNEL_ID:
        if not topic and is_owner(msg.from_user.id) and msg.forward_from_chat and msg.forward_from_message_id:
            return await response.reply(
                f"ℹ️ **Channel post detected!**\nUse the link command to index this file without duplication:\n`/link https://t.me/c/{str(msg.forward_from_chat.id).replace('-100', '')}/{msg.forward_from_message_id}`",
                quote=True
            )
        return await response.reply(
            'Uploads require a storage channel. Ask the owner to configure TELEGRAM_CHANNEL_ID.',
            quote=True
        )

    status_msg = None
    try:
        user_id = msg.from_user.id
        async with library.user_lock(user_id):
            existing = await get_group_upload(source_key, user_id) if source_key else None
            if existing:
                if reply_to is None:
                    return
                doc = existing
                file_name, file_size, mime_type = doc['file_name'], doc['file_size'], doc['mime_type']
                duration, duration_formatted = doc.get('duration', 0), doc.get('duration_formatted', '')
            else:
                folder = await library.active_folder(user_id, chat_id, topic_id=topic_id)
                folder_id = folder['_id'] if folder else None
                if folder_id and await library.folder_count(user_id, folder_id) >= library.MAX_FOLDER_FILES:
                    return await response.reply('This index is full. Send /index to finish it before uploading more files.', quote=True)
                if not folder_id:
                    status_msg = await response.reply('Saving your file…', quote=True)
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
                    personal=True, personal_index_id=folder_id, source_key=source_key)
                if folder_id and reply_to is None:
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
        kwargs = dict(parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
                      disable_web_page_preview=True)
        if status_msg is not None:
            await status_msg.edit_text(text=caption, **kwargs)
        else:
            await response.reply(caption, quote=True, **kwargs)
    except Exception:
        if status_msg is not None:
            await status_msg.edit_text('❌ Could not save this file. Please send it again.')
        else:
            await response.reply('❌ Could not save this file. Please send it again.', quote=True)
