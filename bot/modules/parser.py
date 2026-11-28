import re

# Regex for Telegram message links
# 1. Private channel: https://t.me/c/1234567890/42
# 2. Public channel: https://t.me/username/42
TG_LINK_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:c/(\d+)|([a-zA-Z0-9_]+))/(\d+)'
)

def parse_telegram_link(link: str) -> tuple[int | str, int] | None:
    """
    Parses a Telegram message link.
    Returns (channel_id_or_username, message_id) or None if invalid.
    For private channels (t.me/c/1234567890/42), returns (-1001234567890, 42).
    For public channels (t.me/channel/42), returns ("channel", 42).
    """
    if not link:
        return None
    
    match = TG_LINK_PATTERN.search(link.strip())
    if not match:
        return None
    
    private_chat_id, public_username, message_id = match.groups()
    msg_id = int(message_id)
    
    if private_chat_id:
        # Convert to Telegram supergroup/channel ID format (-100...)
        cid_str = private_chat_id
        if not cid_str.startswith("-100"):
            channel_id = int(f"-100{cid_str}")
        else:
            channel_id = int(cid_str)
        return channel_id, msg_id
    elif public_username:
        return public_username, msg_id
        
    return None
