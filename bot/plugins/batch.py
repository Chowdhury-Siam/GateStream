import os
import io
import asyncio
from secrets import token_hex
from hydrogram import filters
from hydrogram.types import Message
from bot.clients import TelegramBot
from bot.config import Telegram, Server
from bot.database.files import save_batch
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.parser import parse_telegram_link
from bot.modules.telegram import get_message, get_file_properties, is_media_message
from bot.modules.static import InvalidBatchUsageText, get_human_size

@TelegramBot.on_message(filters.command(['batch', 'batch_anime', 'batch_series']) & filters.private)
@verify_user
@verify_admin
async def batch_command(_, msg: Message):
    cmd = msg.command

    # Extract Telegram links and optional category / custom title
    telegram_links = []
    other_args = []
    for arg in cmd[1:]:
        if 't.me/' in arg:
            telegram_links.append(arg)
        else:
            other_args.append(arg)

    if len(telegram_links) < 2:
        return await msg.reply(InvalidBatchUsageText, quote=True)

    start_link = telegram_links[0]
    end_link = telegram_links[1]

    category = 'anime'
    custom_title = None

    if cmd[0] == 'batch_anime':
        category = 'anime'
        custom_title = " ".join(other_args).strip() or None
    elif cmd[0] == 'batch_series':
        category = 'webseries'
        custom_title = " ".join(other_args).strip() or None
    else:
        if other_args:
            first = other_args[0].lower()
            if first in ('anime', 'animes'):
                category = 'anime'
                custom_title = " ".join(other_args[1:]).strip() or None
            elif first in ('series', 'webseries', 'tv'):
                category = 'webseries'
                custom_title = " ".join(other_args[1:]).strip() or None
            elif first in ('movie', 'movies'):
                category = 'movies'
                custom_title = " ".join(other_args[1:]).strip() or None
            else:
                custom_title = " ".join(other_args).strip() or None

    # Parse Telegram links
    parsed_start = parse_telegram_link(start_link)
    parsed_end = parse_telegram_link(end_link)

    if not parsed_start or not parsed_end:
        return await msg.reply("❌ Invalid start or end Telegram message link!", quote=True)

    chan_start, id_start = parsed_start
    chan_end, id_end = parsed_end

    if str(chan_start) != str(chan_end):
        return await msg.reply("❌ Both start and end links must belong to the **same channel**!", quote=True)

    channel_id = chan_start
    start_id = min(id_start, id_end)
    end_id = max(id_start, id_end)
    total_msgs = end_id - start_id + 1

    if total_msgs > 200:
        return await msg.reply("⚠️ Maximum batch limit is **200 messages** at once to prevent Telegram FloodWait.", quote=True)

    status_msg = await msg.reply(
        f"⏳ **Batch Indexing Started ({category.upper()})**\n\n"
        f"📍 Channel: `{channel_id}`\n"
        f"🔢 Range: `{start_id}` ➔ `{end_id}` ({total_msgs} messages)\n\n"
        f"Please wait...",
        quote=True
    )

    episodes_list = []
    numeric_chat_id = channel_id

    for idx, curr_id in enumerate(range(start_id, end_id + 1), start=1):
        try:
            target_msg = await get_message(channel_id, curr_id, force_refresh=True)
            if target_msg and is_media_message(target_msg):
                file_name, file_size, mime_type, duration, duration_formatted = get_file_properties(target_msg)
                numeric_chat_id = target_msg.chat.id if target_msg.chat else channel_id
                
                code = token_hex(Telegram.SECRET_CODE_LENGTH)
                dl_link = f"{Server.BASE_URL}/dl/{code}"
                stream_link = f"{Server.BASE_URL}/stream/{code}"

                episodes_list.append({
                    'code': code,
                    'message_id': curr_id,
                    'episode_num': len(episodes_list) + 1,
                    'file_name': file_name,
                    'file_size': file_size,
                    'size_formatted': get_human_size(file_size),
                    'mime_type': mime_type,
                    'duration': duration,
                    'duration_formatted': duration_formatted,
                    'dl_link': dl_link,
                    'stream_link': stream_link
                })

            # Update progress every 10 messages
            if idx % 10 == 0 or idx == total_msgs:
                try:
                    await status_msg.edit_text(
                        f"⏳ **Batch Indexing in Progress...**\n\n"
                        f"📁 Category: `{category.upper()}`\n"
                        f"🔄 Progress: `{idx}/{total_msgs}` messages scanned\n"
                        f"✅ Files Found: `{len(episodes_list)}`"
                    )
                except Exception:
                    pass

            await asyncio.sleep(0.3)  # Small delay to avoid FloodWait

        except Exception:
            continue

    if not episodes_list:
        return await status_msg.edit_text("❌ No media files were found in the specified range.")

    # Save the entire batch as a SINGLE unified document in MongoDB
    batch_doc = await save_batch(
        channel_id=numeric_chat_id,
        start_id=start_id,
        end_id=end_id,
        category=category,
        user_id=msg.from_user.id,
        episodes=episodes_list,
        title=custom_title
    )

    # Use the episodes from batch_doc in case it was already indexed
    final_episodes = batch_doc.get('episodes', episodes_list)

    batch_id = batch_doc.get('_id')
    api_url = f"{Server.BASE_URL}/api/batch/{batch_id}"
    total_batch_size = get_human_size(sum(ep.get('file_size', 0) for ep in final_episodes))
    display_title = batch_doc.get('title') or custom_title or f"{category.upper()} Batch ({len(final_episodes)} episodes)"

    # Format output with sleek, clean, modern UI cards
    header = (
        f"🍿 **{display_title}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 **Category:** `{category.upper()}`\n"
        f"📦 **Total Episodes:** `{len(final_episodes)}`  •  💾 **Total Size:** `{total_batch_size}`\n"
        f"⚡ **API Endpoint:**\n`{api_url}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    message_chunks = []
    current_chunk = header

    for idx, item in enumerate(final_episodes, start=1):
        human_size = get_human_size(item.get('file_size', 0))
        duration_str = item.get('duration_formatted', 'N/A')
        code = item['code']
        dl_link = f"{Server.BASE_URL}/dl/{code}"
        stream_link = f"{Server.BASE_URL}/stream/{code}"
        ep_num = item.get('episode_num', idx)

        # Clean display title
        clean_name = item.get('file_name', f'Episode {ep_num}')
        if clean_name.endswith(('.mkv', '.mp4', '.avi', '.webm', '.ts')):
            clean_name = clean_name.rsplit('.', 1)[0]
        
        entry = (
            f"🎬 **EP {ep_num:02d}**  •  ⏱️ `{duration_str}`  •  💾 `{human_size}`\n"
            f"📝 **{clean_name}**\n"
            f"▶️ [Stream Online]({stream_link})  •  📥 [Direct Download]({dl_link})\n\n"
        )

        # Telegram message limit is 4096 chars; split into chunks if approaching limit
        if len(current_chunk) + len(entry) > 3800:
            message_chunks.append(current_chunk.strip())
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk.strip():
        message_chunks.append(current_chunk.strip())

    # Edit the initial status message with the first batch chunk
    await status_msg.edit_text(message_chunks[0], disable_web_page_preview=True)

    # If there are additional chunks (for large batches), send as follow-up messages
    for follow_up in message_chunks[1:]:
        await msg.reply(follow_up, quote=False, disable_web_page_preview=True)
        await asyncio.sleep(0.5)
