import math

def get_human_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "N/A"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

WelcomeText = """\
✦ **GateStream — Gateway to your media**

Hi **%(first_name)s**. Turn your Telegram library into stream and download links. Open a gate. Press play.

**User Commands:**
🌐 `/catalog` - Open public Index files and password-protected Link files.
🔎 `/index <last_post_link>` - Auto-index a channel and identify media categories.
⏹ `/cancelindex` - Stop indexing and retain progress (admin).
🔍 `/search <name>` - Search movies, anime & series with streaming links.
⚡ `/search_api <name>` - Search and get 1-tap copyable API endpoints.
🎬 `/link [category] <link>` - Index single file (Movie, Anime OVA, or Series).
📺 `/batch anime <start_link> <end_link>` - Batch index Anime episodes.
🍿 `/batch series <start_link> <end_link>` - Batch index Web Series.
📜 `/privacy` - View Privacy Policy.
❓ `/help` - Show this help menu.

**Admin Commands:**
⚙ `/bset` - Owner-only settings menu; `/dset` opens Drive setup.
🔐 `/setpassword <new password>` - Change the Link category password (owner only).
☁️ `/drive <url>` - Download a direct file link and upload it to Google Drive (optional).
📊 `/stats` - View live database, RAM, CPU & bandwidth metrics.
⏱️ `/sync_duration` - Auto-backfill video duration for all past records.
🧹 `/clean` - Flush RAM caches & compact memory.
💻 `/sh <cmd>` - Execute a terminal shell command.
☁️ `/mirror <direct-url>` - Download a direct file to Google Drive.
📋 `/clone <drive-link>` - Copy a Drive file or folder.
📊 `/drivestatus` · `/canceldrive` - Drive job progress and cancellation.
🔄 `/restart` - Restart the bot process.
📦 `/backup` - Download full MongoDB database backup file.
🧹 `/purge <count>` - Purge messages in chat (or reply with `/purge`).
"""

PrivacyText = """
**GateStream · Privacy**

**1. Data Storage:** File metadata and Telegram message pointers are stored in the deployment's MongoDB database. Media is retrieved from Telegram, with selected chunks cached on the host.

**2. Download Links:** Links contain random tokens. Anyone with a valid link can access its media; Telegram user restrictions do not protect HTTP links.

**3. User Control:** You can revoke links anytime using the "Revoke" button or commands.

**4. Automatic Categories:** Cleaned media titles and release years are sent to AniList and any configured MyAnimeList/OMDb providers during automatic classification. Channel scans are public in Index; /link discovery is password protected. Temporary ZIP links authorize their selected files for 24 hours. Custom ZIP names are stored with their tickets.

**5. Drive Transfers:** Optional admin-only mirror/clone jobs use the owner’s Google credentials. Destination-folder sharing applies; the bot does not make files public. A configured external Drive index has its own access policy.

**6. Your Deployment:** The hosting provider and deployment owner control the server and database. [View GateStream's source](https://github.com/Chowdhury-Siam/GateStream) before deploying your own instance.

__By using this bot, you agree to this policy.__
"""

FileLinksText = """
✦ **GateStream · File ready**
📂 **File:** `%(file_name)s`
📦 **Size:** `%(file_size)s`
⏱️ **Duration:** `%(duration)s`
🏷️ **Category:** `%(category)s`

📥 **Download Link:**
`%(dl_link)s`
"""

MediaLinksText = """
✦ **GateStream · Media indexed**
━━━━━━━━━━━━━━━━━━━━━━━
🎬 **Title:** `%(file_name)s`
📦 **Size:** `%(file_size)s`  •  ⏱️ **Duration:** `%(duration)s`
🏷️ **Category:** `%(category)s`
━━━━━━━━━━━━━━━━━━━━━━━
🔗 **Stream Online:**
`%(stream_link)s`

📥 **Direct Download:**
`%(dl_link)s`
"""

InvalidQueryText = "Query data mismatched."
MessageNotExist = "File does not exist or has been revoked."
LinkRevokedText = "The link has been revoked successfully from the database."
InvalidPayloadText = "Invalid payload."
UserNotInAllowedList = "You are not allowed to use this bot."
AdminOnlyText = "⚠️ This command is restricted to Bot Administrators."
InvalidLinkText = "❌ Invalid Telegram message link provided.\n\n**Usage:**\n`/link https://t.me/c/1234567890/42` or `/link https://t.me/channel_name/42`"
InvalidBatchUsageText = """❌ **Invalid Batch Command Usage!**

**Format:**
`/batch <category> <start_message_link> <end_message_link>`

**Examples:**
• `/batch anime https://t.me/c/1234567890/10 https://t.me/c/1234567890/25`
• `/batch series https://t.me/c/1234567890/50 https://t.me/c/1234567890/60`
"""
