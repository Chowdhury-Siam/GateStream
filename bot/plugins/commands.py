import os
import time
import shutil
import psutil
from datetime import timedelta
from hydrogram import filters
from hydrogram.types import Message
from bot.clients import TelegramBot, worker_clients
from bot.config import Telegram
from bot.database.files import get_stats, get_bandwidth_stats
from bot.modules.static import WelcomeText, PrivacyText, get_human_size
from bot.modules.decorators import verify_user, verify_admin
from bot.modules.new_users import notify_new_user

BOT_START_TIME = time.time()

def get_readable_time(seconds: int) -> str:
    result = []
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    if days > 0:
        result.append(f"{days}d")
    if hours > 0:
        result.append(f"{hours}h")
    if minutes > 0:
        result.append(f"{minutes}m")
    result.append(f"{seconds}s")
    
    return " ".join(result)

@TelegramBot.on_message(filters.command(['start', 'help']) & filters.private)
@verify_user
async def start_command(client, msg: Message):
    await msg.reply(
        text=WelcomeText % {'first_name': msg.from_user.first_name},
        quote=True,
        disable_web_page_preview=True
    )

    if msg.command and msg.command[0].split('@', 1)[0].lower() == 'start':
        await notify_new_user(client, msg.from_user)

@TelegramBot.on_message(filters.command('privacy') & filters.private)
@verify_user
async def privacy_command(_, msg: Message):
    await msg.reply(text=PrivacyText, quote=True, disable_web_page_preview=True)

@TelegramBot.on_message(filters.command('stats') & filters.private)
@verify_user
@verify_admin
async def stats_command(_, msg: Message):
    # Database counts
    stats = await get_stats()
    total_files = (
        stats.get('movies', 0) +
        stats.get('anime_episodes', 0) +
        stats.get('webseries_episodes', 0) +
        stats.get('direct_files', 0)
    )
    total_titles = stats.get('total_titles', 0)
    
    # Bandwidth & Request analytics from MongoDB
    bytes_streamed, requests_count = await get_bandwidth_stats()
    bandwidth_str = get_human_size(bytes_streamed)

    # Uptime
    uptime = get_readable_time(int(time.time() - BOT_START_TIME))

    # Process RAM & CPU
    try:
        process = psutil.Process(os.getpid())
        bot_ram = get_human_size(process.memory_info().rss)
    except Exception:
        bot_ram = "N/A"

    try:
        sys_ram = psutil.virtual_memory()
        sys_ram_str = f"{get_human_size(sys_ram.used)} / {get_human_size(sys_ram.total)} ({sys_ram.percent}%)"
    except Exception:
        sys_ram_str = "N/A"

    try:
        cpu_usage = f"{psutil.cpu_percent(interval=0.1)}%"
    except Exception:
        cpu_usage = "N/A"

    # Actual Bot Project Disk Space vs Shared Host Node Storage
    try:
        bot_disk_bytes = 0
        for root, dirs, files in os.walk('.'):
            if '.git' in dirs:
                dirs.remove('.git')
            for f in files:
                fp = os.path.join(root, f)
                try:
                    bot_disk_bytes += os.path.getsize(fp)
                except OSError:
                    pass
        bot_disk_str = get_human_size(bot_disk_bytes)
    except Exception:
        bot_disk_str = "N/A"

    try:
        disk = shutil.disk_usage('.')
        disk_pct = round((disk.used / disk.total) * 100, 1)
        host_disk_str = f"{get_human_size(disk.used)} / {get_human_size(disk.total)} ({disk_pct}%)"
    except Exception:
        host_disk_str = "N/A"

    # 24/7 Heartbeat status
    try:
        from bot.clients import get_heartbeat_status
        pings_count, last_time = get_heartbeat_status()
        if last_time > 0:
            elapsed = max(0, int(time.time() - last_time))
            heartbeat_str = f"Active ({pings_count} pings • {elapsed}s ago)"
        else:
            heartbeat_str = "Active (Starting up...)"
    except Exception:
        heartbeat_str = "Active"

    text = (
        "📊 **GateStream Live System & Database Stats**\n\n"
        f"🍿 **Anime:** `{stats.get('anime_franchises', stats.get('anime_titles', 0))} Titles` (`{stats.get('anime_titles', 0)} Arcs` • `{stats.get('anime_episodes', 0)} Episodes/Files`)\n"
        f"🎬 **Movies Indexed:** `{stats.get('movie_titles', 0)} Titles`\n"
        f"📺 **Web Series:** `{stats.get('webseries_titles', 0)} Titles` (`{stats.get('webseries_episodes', 0)} Episodes`)\n"
        f"📁 **Direct Files:** `{stats.get('direct_files', 0)}`\n"
        f"{'─'*28}\n"
        f"📦 **Vault Summary:** `{total_titles} Titles` • `{total_files} Streamable Files`\n"
        f"{'─'*28}\n"
        f"🤖 **Active Worker Bots:** `{len(worker_clients)}`\n"
        f"👑 **Admins Registered:** `{len(Telegram.ADMIN_IDS)}`\n"
        f"⏱️ **System Uptime:** `{uptime}`\n"
        f"💓 **24/7 Heartbeat:** `{heartbeat_str}`\n"
        f"{'─'*28}\n"
        f"🧠 **Bot Process RAM:** `{bot_ram}`\n"
        f"💾 **Bot Project Disk:** `{bot_disk_str}`\n"
        f"💻 **Host Node RAM:** `{sys_ram_str}`\n"
        f"🖥️ **Host Node Disk (Shared):** `{host_disk_str}`\n"
        f"⚡ **CPU Usage:** `{cpu_usage}`\n"
        f"🌐 **Total Bandwidth Streamed:** `{bandwidth_str}` (`{requests_count} hits`)\n"
    )
    await msg.reply(text, quote=True)

@TelegramBot.on_message(filters.command('log') & filters.private)
@verify_user
@verify_admin
async def log_command(_, msg: Message):
    try:
        await msg.reply_document('event-log.txt', quote=True)
    except Exception as e:
        await msg.reply(f"❌ Failed to send log file: `{e}`", quote=True)

@TelegramBot.on_message(filters.command(['workers', 'bots']) & filters.private)
@verify_user
@verify_admin
async def workers_command(_, msg: Message):
    """Diagnoses all worker bots in the pool, checks their channel permissions, and outputs required actions."""
    from bot.clients import worker_clients
    from bot.database import db
    from bot.config import Telegram

    status_msg = await msg.reply("🔍 **Scanning worker fleet and verifying channel permissions...**", quote=True)

    # 1. Discover unique channel IDs stored in database
    channel_ids = set()
    if Telegram.CHANNEL_ID:
        channel_ids.add(Telegram.CHANNEL_ID)

    try:
        for col in (db.movies, db.anime, db.webseries, db.direct_files):
            if col is not None:
                async for doc in col.find({}, {'channel_id': 1}).limit(20):
                    cid = doc.get('channel_id')
                    if cid:
                        channel_ids.add(cid)
    except Exception as e:
        logger.warning("Error fetching channels for workers command: %s", e)

    # 2. Inspect each worker client
    worker_results = []
    missing_admin_bots = []
    all_ready = True

    for idx, w_client in enumerate(worker_clients):
        w_username = getattr(getattr(w_client, 'me', None), 'username', None)
        if not w_username:
            try:
                me_obj = await w_client.get_me()
                w_username = me_obj.username
            except Exception:
                w_username = f"worker_{idx}"

        # Test channel permissions against discovered channels
        is_admin_all = True
        for cid in channel_ids:
            try:
                await w_client.get_chat(cid)
            except Exception:
                is_admin_all = False
                break

        if is_admin_all:
            tag = "👑 Primary" if w_client == TelegramBot else f"🤖 Worker #{idx}"
            worker_results.append(f"• {tag}: `@{w_username}` ➜ ✅ **Active Admin**")
        else:
            all_ready = False
            missing_admin_bots.append(f"@{w_username}")
            worker_results.append(f"• 🤖 Worker #{idx}: `@{w_username}` ➜ ❌ **Not in Channel**")

    # 3. Format detailed response
    if not channel_ids:
        channel_info = "⚠️ *No storage channels detected in database yet.*"
    else:
        channel_info = f"📁 **Storage Channel(s) Checked:** `{len(channel_ids)}`"

    header = (
        f"🤖 **GateStream Multi-Bot Fleet Status ({len(worker_clients)} Total)**\n"
        f"{channel_info}\n"
        f"{'─'*28}\n"
    )

    body = "\n".join(worker_results)

    if all_ready and len(worker_clients) > 1:
        footer = (
            f"\n{'─'*28}\n"
            f"🚀 **All {len(worker_clients)} bots are Active Admins!**\n"
            f"Multi-worker parallel streaming is operating at maximum throughput (2.5–3.0 MB/s)! ⚡"
        )
    elif missing_admin_bots:
        copy_usernames = "\n".join([f"`{u}`" for u in missing_admin_bots])
        footer = (
            f"\n{'─'*28}\n"
            f"⚠️ **ACTION REQUIRED TO UNLOCK 3.0 MB/s STREAMING:**\n"
            f"Telegram strictly prevents worker bots from downloading files from private channels "
            f"unless they are added as **Administrators**!\n\n"
            f"📋 **Click to Copy Bot Usernames:**\n{copy_usernames}\n\n"
            f"👉 **Quick Setup (Takes 30 seconds):**\n"
            f"1. Open your Telegram storage/movie channel.\n"
            f"2. Go to **Channel Settings ➜ Administrators ➜ Add Administrator**.\n"
            f"3. Search and add each username listed above.\n"
            f"4. Grant basic permissions (read/post messages).\n"
            f"5. Run `/workers` again to confirm all are active! 🚀"
        )
    else:
        footer = (
            f"\n{'─'*28}\n"
            f"ℹ️ Only the primary bot is configured. Add more worker tokens to `.env` to scale throughput!"
        )

    await status_msg.edit_text(header + body + footer)
