import os
import sys
import io
import asyncio
from hydrogram import filters
from hydrogram.types import Message
from bot.clients import TelegramBot
from bot.modules.decorators import verify_user, verify_admin

@TelegramBot.on_message(filters.command(['sh', 'shell', 'exec']) & filters.private)
@verify_user
@verify_admin
async def shell_command(_, msg: Message):
    """Executes a terminal shell command directly from Telegram and returns the output."""
    if len(msg.command) < 2:
        return await msg.reply(
            "💻 **Shell Terminal Command**\n\n"
            "**Usage:**\n"
            "• `/sh git pull origin main`\n"
            "• `/sh pip install -r requirements.txt`\n"
            "• `/sh ls -la`",
            quote=True
        )

    cmd = msg.text.split(maxsplit=1)[1]
    status_msg = await msg.reply(f"⏳ **Executing:** `{cmd}`", quote=True)

    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        output = (stdout.decode('utf-8', errors='replace') + stderr.decode('utf-8', errors='replace')).strip()

        if not output:
            output = "Command finished with exit code 0 (no output)."

        if len(output) > 3800:
            file_bytes = io.BytesIO(output.encode('utf-8'))
            file_bytes.name = "output.txt"
            await status_msg.delete()
            await msg.reply_document(
                document=file_bytes,
                caption=f"📄 **Output for:** `{cmd[:60]}`",
                quote=True
            )
        else:
            await status_msg.edit_text(f"💻 **Output:**\n```bash\n{output}\n```")

    except asyncio.TimeoutError:
        await status_msg.edit_text("❌ **Execution Timed Out** (limit: 120 seconds).")
    except Exception as e:
        await status_msg.edit_text(f"❌ **Error executing command:** `{e}`")

@TelegramBot.on_message(filters.command(['restart', 'reboot']) & filters.private)
@verify_user
@verify_admin
async def restart_command(_, msg: Message):
    """Restarts the bot process smoothly and notifies user upon coming back online."""
    import time
    restart_msg = await msg.reply("🔄 **Restarting GateStream...**\nPlease wait ~5-10 seconds.", quote=True)

    # Save state to edit message after reboot
    try:
        with open('.restart_state.txt', 'w') as f:
            f.write(f"{msg.chat.id} {restart_msg.id} {time.time()}")
    except Exception:
        pass

    # Touch start.sh to update timestamp if present
    try:
        if os.path.exists("start.sh"):
            os.utime("start.sh", None)
    except Exception:
        pass

    await asyncio.sleep(1.5)

    # Replace current python process
    os.execl(sys.executable, sys.executable, "-m", "bot")

try:
    from hydrogram.errors import FloodWait
except ImportError:
    try:
        from pyrogram.errors import FloodWait
    except ImportError:
        FloodWait = Exception

@TelegramBot.on_message(filters.command(['purge', 'clear']) & (filters.private | filters.group))
@verify_user
@verify_admin
async def purge_command(client, msg: Message):
    """
    Ultimate Unified Purge Engine:
    - Dual Trigger: Reply to a message with /purge OR specify a count (e.g. /purge 20).
    - Bi-directional Safety: Uses min() and max() so range is never inverted.
    - Works in both Private chats and Groups.
    - Paced 100-chunk batching with 1s delay to prevent Telegram flood penalties.
    - FloodWait auto-pause and resume.
    - Telegram 48h limit fallback: if bulk delete fails, deletes bot's own messages individually.
    - 100% Self-Cleaning: Cleans up command message, status alert, and confirmation.
    """
    if msg.reply_to_message:
        start_id = msg.reply_to_message.id
        end_id = msg.id
        min_id = min(start_id, end_id)
        max_id = max(start_id, end_id)
        message_ids = list(range(min_id, max_id + 1))
    elif len(msg.command) > 1 and msg.command[1].isdigit():
        count = min(int(msg.command[1]), 1000)
        message_ids = list(range(msg.id - count, msg.id + 1))
    else:
        usage = await msg.reply(
            "🧹 **Purge Usage:**\n"
            "• Reply to a message with `/purge` to delete everything from that message downwards.\n"
            "• Send `/purge 20` to delete the last 20 messages.\n\n"
            "ℹ️ **Telegram Limitation:** Telegram API strictly forbids bots from deleting user messages older than 48 hours. To wipe older chat history completely, use Telegram's **Clear History** feature.",
            quote=True
        )
        await asyncio.sleep(6)
        try:
            await usage.delete()
            await msg.delete()
        except Exception:
            pass
        return

    status = await client.send_message(
        chat_id=msg.chat.id,
        text="🌪️ **Purging messages...**"
    )
    deleted_count = 0

    # Try batch delete in chunks of 100 (Telegram API maximum)
    chunks = [message_ids[i:i + 100] for i in range(0, len(message_ids), 100)]

    try:
        for chunk in chunks:
            try:
                await client.delete_messages(chat_id=msg.chat.id, message_ids=chunk)
                deleted_count += len(chunk)
                await asyncio.sleep(1)  # Rate-limit pacing between chunks
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await client.delete_messages(chat_id=msg.chat.id, message_ids=chunk)
                    deleted_count += len(chunk)
                except Exception:
                    pass
            except Exception:
                # If batch delete fails (e.g. contains user messages > 48 hours old),
                # delete individually so bot's own messages still get deleted!
                for mid in chunk:
                    try:
                        await client.delete_messages(chat_id=msg.chat.id, message_ids=[mid])
                        deleted_count += 1
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value + 1)
                        try:
                            await client.delete_messages(chat_id=msg.chat.id, message_ids=[mid])
                            deleted_count += 1
                        except Exception:
                            pass
                    except Exception:
                        pass

        confirm = await client.send_message(
            chat_id=msg.chat.id,
            text=(
                f"🧹 **Purge Complete!** Deleted `{deleted_count}` messages.\n\n"
                f"ℹ️ *Note: Telegram does not allow bots to delete user messages older than 48 hours. "
                f"To delete everything including old messages, use Telegram's **Clear History** option.*"
            )
        )
        try:
            await status.delete()
        except Exception:
            pass

        # Also clean up the original command message
        try:
            await msg.delete()
        except Exception:
            pass

        await asyncio.sleep(3)
        try:
            await confirm.delete()
        except Exception:
            pass

    except Exception as e:
        try:
            await status.edit_text(f"⚠️ **Error during purge:** `{e}`")
        except Exception:
            pass

@TelegramBot.on_message(filters.command(['clean', 'gc', 'flush']) & filters.private)
@verify_user
@verify_admin
async def clean_memory_command(_, msg: Message):
    """Manually cleans memory, runs cyclic garbage collection and releases freed heap back to OS."""
    from bot.modules.memory import flush_ram, get_current_ram_mb
    from bot.modules.cache import clear_media_cache
    try:
        from bot.modules.telegram import _MESSAGE_CACHE
        _MESSAGE_CACHE.clear()
    except Exception:
        pass

    before_ram_mb = get_current_ram_mb()
    flush_ram()
    clear_media_cache()
    after_ram_mb = get_current_ram_mb()

    freed_mb = max(0.0, before_ram_mb - after_ram_mb)

    await msg.reply(
        f"🧹 **System Cleaned & Memory Compacted!**\n\n"
        f"📉 **Freed RAM:** `{freed_mb:.1f} MB`\n"
        f"🧠 **Current Process RAM:** `{after_ram_mb:.1f} MB`\n"
        f"💾 **SSD Head/Tail Media Cache:** Purged cleanly",
        quote=True
    )

@TelegramBot.on_message(filters.command(['sync_duration', 'backfill', 'sync_metadata']) & filters.private)
@verify_user
@verify_admin
async def sync_duration_command(_, msg: Message):
    """Automatically scans all existing files/batches in MongoDB and backfills exact duration."""
    from bot.database import db
    from bot.modules.telegram import get_message, get_file_properties

    status = await msg.reply("⏳ **Syncing duration for all existing database records...**\nPlease wait...", quote=True)
    synced_files = 0
    synced_batches = 0

    try:
        # 1. Sync movies and direct files
        for col in (db.movies, db.direct_files):
            if col is not None:
                async for doc in col.find({'duration': {'$in': [None, 0, '']}}):
                    chan_id = doc.get('channel_id')
                    msg_id = doc.get('message_id')
                    if chan_id and msg_id:
                        try:
                            t_msg = await get_message(chan_id, msg_id)
                            if t_msg:
                                _, _, _, dur, dur_fmt = get_file_properties(t_msg)
                                await col.update_one(
                                    {'_id': doc['_id']},
                                    {'$set': {'duration': dur, 'duration_formatted': dur_fmt}}
                                )
                                synced_files += 1
                        except Exception:
                            pass
                    await asyncio.sleep(0.1)

        # 2. Sync anime and webseries batches
        for col in (db.anime, db.webseries):
            if col is not None:
                async for batch_doc in col.find():
                    chan_id = batch_doc.get('channel_id')
                    episodes = batch_doc.get('episodes', [])
                    updated = False

                    for ep in episodes:
                        if not ep.get('duration'):
                            msg_id = ep.get('message_id')
                            if chan_id and msg_id:
                                try:
                                    t_msg = await get_message(chan_id, msg_id)
                                    if t_msg:
                                        _, _, _, dur, dur_fmt = get_file_properties(t_msg)
                                        ep['duration'] = dur
                                        ep['duration_formatted'] = dur_fmt
                                        updated = True
                                        synced_files += 1
                                except Exception:
                                    pass
                                await asyncio.sleep(0.1)

                    if updated:
                        await col.update_one(
                            {'_id': batch_doc['_id']},
                            {'$set': {'episodes': episodes}}
                        )
                        synced_batches += 1

        await status.edit_text(
            f"✅ **Duration Backfill Completed Successfully!**\n\n"
            f"🎬 **Total Episodes/Files Updated:** `{synced_files}`\n"
            f"📁 **Batches Updated:** `{synced_batches}`\n\n"
            f"All your existing MongoDB records and REST API endpoints now include exact durations! ⏱️"
        )
    except Exception as e:
        await status.edit_text(f"❌ Error during sync: `{e}`")

@TelegramBot.on_message(filters.command(['revoke', 'del', 'delete_link', 'rm']) & filters.private)
@verify_user
@verify_admin
async def revoke_command(_, msg: Message):
    """Revokes an existing file link or batch from MongoDB so it can be re-indexed cleanly."""
    from bot.config import Server
    from bot.database.files import get_file, delete_file

    if len(msg.command) < 2:
        return await msg.reply(
            "🗑️ **Revoke / Delete Link Command**\n\n"
            "**Usage for Single Files:**\n"
            "• `/revoke <file_code>`\n"
            f"• `/revoke {Server.BASE_URL}/dl/<file_code>`\n"
            f"• `/revoke {Server.BASE_URL}/stream/<file_code>`\n\n"
            "**Usage for Whole Batches (Anime / Series):**\n"
            "• `/revoke <batch_id>`\n"
            f"• `/revoke {Server.BASE_URL}/api/batch/<batch_id>`\n\n"
            "*(You can also simply click the `[🗑️ Revoke]` button under any link card)*",
            quote=True
        )

    raw_input = msg.command[1].strip()
    from bot.modules.parser import parse_telegram_link
    parsed_tg = parse_telegram_link(raw_input)

    doc = None
    code = None
    if parsed_tg:
        chan_id, msg_id = parsed_tg
        from bot.database import db
        for col in (db.movies, db.direct_files, db.anime, db.webseries):
            if col is not None:
                doc = await col.find_one({'channel_id': chan_id, 'message_id': msg_id})
                if doc:
                    break
        if not doc:
            return await msg.reply("❌ No database record found matching that channel message link.", quote=True)
        code = doc.get('code') or str(doc.get('_id'))
    else:
        # Extract code/id from URL if full URL is pasted
        code = raw_input.rstrip('/').split('/')[-1].strip()
        doc = await get_file(code)

    if not doc:
        return await msg.reply("❌ Record not found in database or was already revoked.", quote=True)

    file_title = doc.get('title') or doc.get('file_name', 'N/A')
    episodes = doc.get('episodes')
    await delete_file(code)

    # Invalidate in-memory Telegram message cache across all workers so replaced files don't stay cached
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

    if episodes is not None:
        batch_info = f"📦 **Type:** `Entire Batch ({len(episodes)} episodes removed)`\n"
    elif doc.get('batch_id'):
        batch_info = "🎬 **Type:** `Single episode removed from batch`\n"
    else:
        batch_info = "🎬 **Type:** `Single Movie / Direct File`\n"

    await msg.reply(
        f"🗑️ **Revoked Successfully!**\n\n"
        f"🎬 **Title:** `{file_title}`\n"
        f"🔑 **Code / ID:** `{code}`\n"
        f"{batch_info}\n"
        f"✨ Database record has been cleared cleanly. You can now re-index anytime!",
        quote=True
    )

@TelegramBot.on_message(filters.command(['backup', 'db_dump', 'dump_db']) & filters.private)
@verify_user
@verify_admin
async def backup_database_command(client, msg: Message):
    """Creates a compressed full JSON backup of the MongoDB database and sends it directly via Telegram DM."""
    import json
    import gzip
    from datetime import datetime
    from bot.database import db
    from bot.modules.static import get_human_size

    status = await msg.reply("📦 **Generating database backup...**\nReading collections...", quote=True)

    try:
        backup_data = {
            "meta": {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "version": "1.0",
                "database_name": db.db.name if db.db is not None else "OmniArchiver"
            },
            "collections": {}
        }

        total_records = 0
        summary_lines = []

        for name, col in (
            ("movies", db.movies),
            ("anime", db.anime),
            ("webseries", db.webseries),
            ("direct_files", db.direct_files),
        ):
            if col is not None:
                docs = []
                async for doc in col.find():
                    doc_copy = {}
                    for k, v in doc.items():
                        if isinstance(v, datetime):
                            doc_copy[k] = v.isoformat()
                        else:
                            doc_copy[k] = v
                    docs.append(doc_copy)

                backup_data["collections"][name] = docs
                count = len(docs)
                total_records += count
                summary_lines.append(f"• **{name.capitalize()}**: `{count}` documents")

        # Serialize to JSON and compress with gzip
        json_str = json.dumps(backup_data, ensure_ascii=False, indent=2)
        json_bytes = json_str.encode('utf-8')
        raw_size = get_human_size(len(json_bytes))

        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode='wb', compresslevel=9) as gz_file:
            gz_file.write(json_bytes)
        gz_buf.seek(0)
        gz_size = get_human_size(len(gz_buf.getvalue()))

        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_name = f"GateStream_Backup_{timestamp_str}.json.gz"
        gz_buf.name = file_name

        caption = (
            f"📦 **GateStream Database Backup**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **Date:** `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
            f"📊 **Total Records:** `{total_records}`\n"
            + "\n".join(summary_lines) + "\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 **File Size:** `{gz_size}` *(uncompressed: {raw_size})*\n"
            f"🔒 Keep this backup safe in your Telegram Saved Messages!"
        )

        await status.delete()
        await client.send_document(
            chat_id=msg.chat.id,
            document=gz_buf,
            file_name=file_name,
            caption=caption
        )
    except Exception as e:
        await status.edit_text(f"❌ **Failed to create database backup:** `{e}`")

@TelegramBot.on_message(filters.command(['restore', 'db_restore', 'import_db']) & filters.private)
@verify_user
@verify_admin
async def restore_database_command(client, msg: Message):
    """Restores the MongoDB database from a .json.gz or .json backup file sent as a reply."""
    import json
    import gzip
    from datetime import datetime
    from bot.database import db

    if not msg.reply_to_message or not msg.reply_to_message.document:
        return await msg.reply(
            "📥 **Database Restore Usage:**\n\n"
            "Reply to any backup file (`.json.gz` or `.json`) with `/restore` to import all records into MongoDB.\n\n"
            "⚠️ *Warning: Existing records with the same `_id` will be updated with the backup version.*",
            quote=True
        )

    doc = msg.reply_to_message.document
    f_name = doc.file_name or ""
    if not (f_name.endswith('.json.gz') or f_name.endswith('.json') or f_name.endswith('.gz')):
        return await msg.reply("❌ The replied file must be a `.json.gz` or `.json` database backup file!", quote=True)

    status = await msg.reply("⏳ **Downloading and verifying backup file...**", quote=True)

    try:
        # Download document into memory
        file_obj = await client.download_media(doc, in_memory=True)
        raw_bytes = file_obj.getvalue() if hasattr(file_obj, 'getvalue') else bytes(file_obj)

        await status.edit_text("⏳ **Decompressing and parsing backup data...**")

        # Decompress if gzipped
        if f_name.endswith('.gz') or raw_bytes[:2] == b'\x1f\x8b':
            json_bytes = gzip.decompress(raw_bytes)
        else:
            json_bytes = raw_bytes

        backup_data = json.loads(json_bytes.decode('utf-8'))
        collections_data = backup_data.get("collections", {})

        if not collections_data:
            return await status.edit_text("❌ Invalid backup file: No collections found inside!")

        await status.edit_text("⏳ **Restoring database records into MongoDB...**")

        total_restored = 0
        summary_lines = []

        col_mapping = {
            "movies": db.movies,
            "anime": db.anime,
            "webseries": db.webseries,
            "direct_files": db.direct_files,
        }

        for name, col in col_mapping.items():
            if col is not None and name in collections_data:
                docs = collections_data[name]
                restored_col_count = 0
                for d in docs:
                    # Restore ISO format dates back to datetime objects if needed
                    for k, v in list(d.items()):
                        if isinstance(v, str) and ('T' in v and (v.endswith('Z') or '+' in v)):
                            try:
                                d[k] = datetime.fromisoformat(v.replace('Z', '+00:00'))
                            except Exception:
                                pass

                    doc_id = d.get('_id')
                    if doc_id:
                        await col.replace_one({'_id': doc_id}, d, upsert=True)
                        restored_col_count += 1

                total_restored += restored_col_count
                summary_lines.append(f"• **{name.capitalize()}**: `{restored_col_count}` documents")

        meta = backup_data.get("meta", {})
        gen_time = meta.get("generated_at", "Unknown")

        await status.edit_text(
            f"✅ **Database Restore Complete!**\n\n"
            f"📅 **Backup Created At:** `{gen_time}`\n"
            f"📊 **Total Restored:** `{total_restored}` records\n"
            + "\n".join(summary_lines) + "\n\n"
            f"✨ All records have been safely synced to MongoDB!"
        )

    except Exception as e:
        await status.edit_text(f"❌ **Failed to restore backup:** `{e}`")


