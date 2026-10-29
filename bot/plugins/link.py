import aiohttp
from bot.modules.metadata import Classifier
from hydrogram import filters
from hydrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.clients import TelegramBot
from bot.config import Server
from bot.database.files import save_file
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.parser import parse_telegram_link
from bot.modules.telegram import get_message, get_file_properties, is_media_message
from bot.modules.static import MediaLinksText, FileLinksText, InvalidLinkText, get_human_size

@TelegramBot.on_message(filters.command(['link', 'movie', 'anime', 'series', 'link_anime', 'link_movie', 'link_series']) & filters.private)
@verify_user
@verify_admin
async def link_command(_, msg: Message):
    cmd_name = msg.command[0].lower() if msg.command else 'link'
    
    # 1. Determine explicit category from command name
    target_category = None
    if cmd_name in ('anime', 'link_anime'):
        target_category = 'anime'
    elif cmd_name in ('series', 'link_series'):
        target_category = 'webseries'
    elif cmd_name in ('movie', 'link_movie'):
        target_category = 'movies'

    # Check if a link and/or category was provided in command arguments
    channel_id = None
    message_id = None
    
    # Look for telegram link and category keywords in args
    telegram_link = None
    for arg in msg.command[1:]:
        if 't.me/' in arg:
            telegram_link = arg
        elif arg.lower() in ('anime', 'animes'):
            target_category = 'anime'
        elif arg.lower() in ('series', 'webseries', 'tv'):
            target_category = 'webseries'
        elif arg.lower() in ('movie', 'movies'):
            target_category = 'movies'

    if telegram_link:
        parsed = parse_telegram_link(telegram_link)
        if not parsed:
            return await msg.reply(InvalidLinkText, quote=True)
        channel_id, message_id = parsed
    elif msg.reply_to_message:
        reply = msg.reply_to_message
        if reply.forward_from_chat and reply.forward_from_message_id:
            channel_id = reply.forward_from_chat.id
            message_id = reply.forward_from_message_id
        elif is_media_message(reply):
            return await msg.reply("⚠️ Please forward the message from your channel or provide the post link:\n`/link <category> <post_link>`", quote=True)
        else:
            return await msg.reply(InvalidLinkText, quote=True)
    else:
        return await msg.reply(
            "🎬 **Usage:**\n"
            "• `/link <telegram_link>`\n"
            "• `/link anime <telegram_link>` *(Anime Movie/OVA)*\n"
            "• `/link movie <telegram_link>` *(Movie)*\n"
            "• `/link series <telegram_link>` *(Web Series Single)*\n"
            "*(Or reply to any forwarded channel post with `/link`)*",
            quote=True
        )

    status_msg = await msg.reply('Fetching the channel message and identifying its media category…', quote=True)

    try:
        target_msg = await get_message(channel_id, message_id, force_refresh=True)
        if not target_msg or not is_media_message(target_msg):
            return await status_msg.edit_text("❌ Could not find a valid media file in that channel message.\nMake sure the bot is an **Admin** in that channel!")

        file_name, file_size, mime_type, duration, duration_formatted = get_file_properties(target_msg)
        numeric_chat_id = target_msg.chat.id if target_msg.chat else channel_id
        metadata = None
        if not target_category:
            if mime_type.startswith('video/') or file_name.lower().endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov', '.ts')):
                async with aiohttp.ClientSession() as session:
                    metadata = await Classifier(session).classify(file_name)
                target_category = metadata['category']
            else:
                target_category = 'direct_files'
        
        # Save permanently to target collection
        doc = await save_file(
            channel_id=numeric_chat_id,
            message_id=message_id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            user_id=msg.from_user.id,
            category=target_category,
            duration=duration,
            duration_formatted=duration_formatted,
            metadata=metadata
        )
        
        code = doc['code']
        human_size = get_human_size(file_size)
        dl_link = f"{Server.BASE_URL}/dl/{code}"
        stream_link = f"{Server.BASE_URL}/stream/{code}"
        api_link = f"{Server.BASE_URL}/api/file/{code}"
        
        buttons = [
            [
                InlineKeyboardButton('📥 Download', url=dl_link),
                InlineKeyboardButton('▶️ Stream', url=stream_link)
            ],
            [
                InlineKeyboardButton('🗑️ Revoke', callback_data=f"rm_{code}")
            ]
        ]
        
        category_labels = {
            'anime': 'Anime (Movie / OVA / Single)',
            'movies': 'Movies / Single File',
            'webseries': 'Web Series (Single Episode)'
        }
        cat_display = category_labels.get(target_category, target_category.upper())

        caption = MediaLinksText % {
            'file_name': file_name,
            'file_size': human_size,
            'duration': duration_formatted,
            'category': cat_display,
            'dl_link': dl_link,
            'stream_link': stream_link
        }
        caption += f"\n⚡ **API:** `{api_link}`"
        
        await status_msg.edit_text(
            text=caption,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing link: `{e}`")
