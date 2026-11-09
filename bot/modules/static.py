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
InvalidLinkText = "❌ Invalid Telegram message link provided.\n\n**Usage:**\n`/link https://t.me/c/1234567890/42` or `/link https://t.me/channel_name/42`"
