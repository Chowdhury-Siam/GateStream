"""Work around Hydrogram 0.2.0 refetching a forum message to parse its topic."""
import asyncio
import logging
from copy import copy
from functools import wraps

from hydrogram import raw, types, utils

logger = logging.getLogger('bot')


def install_forum_parser(primary_client):
    # Incoming updates and the responses to send/edit both use Message._parse.
    original_parse = types.Message._parse

    @wraps(original_parse)
    async def parse(*, client, message, users, chats, topics=None, is_scheduled=False, replies=1):
        header = getattr(message, 'reply_to', None)
        if (client is not primary_client or not isinstance(message, raw.types.Message)
                or not isinstance(message.peer_id, raw.types.PeerChannel)
                or not isinstance(header, raw.types.MessageReplyHeader) or not header.forum_topic):
            return await original_parse(client=client, message=message, users=users, chats=chats,
                                        topics=topics, is_scheduled=is_scheduled, replies=replies)

        # The topic ID is already in the header. Avoid the SDK's lookup of this
        # same message, which can recurse when Telegram returns no topic details.
        standalone = copy(message)
        standalone.reply_to = None
        forwarded = standalone.fwd_from
        if forwarded and forwarded.from_id:
            source = forwarded.from_id
            peers = users if isinstance(source, raw.types.PeerUser) else chats
            if utils.get_raw_peer_id(source) not in peers:
                standalone.fwd_from = copy(forwarded)
                standalone.fwd_from.from_id = None
        parsed = await original_parse(client=client, message=standalone, users=users, chats=chats,
                                      replies=0, is_scheduled=is_scheduled)
        parsed.message_thread_id = header.reply_to_top_id or header.reply_to_msg_id
        parsed.is_topic_message = True
        topic = (topics or {}).get(parsed.message_thread_id)
        if isinstance(topic, raw.types.ForumTopic):
            parsed.topics = types.ForumTopic._parse(topic)

        reply_id = header.reply_to_msg_id
        if reply_id == parsed.message_thread_id:
            return parsed
        parsed.reply_to_message_id = reply_id
        reply_peer = header.reply_to_peer_id
        if replies and reply_id and (reply_peer is None or (
                isinstance(reply_peer, raw.types.PeerChannel)
                and reply_peer.channel_id == message.peer_id.channel_id)):
            try:
                reply = client.message_cache[(parsed.chat.id, reply_id)]
                if reply is None:
                    async with asyncio.timeout(5):
                        reply = await client.get_messages(parsed.chat.id, message_ids=reply_id, replies=0)
                if reply and not reply.empty and not reply.forum_topic_created:
                    parsed.reply_to_message = reply
            except Exception as error:
                logger.debug('Could not load a forum reply (%s).', type(error).__name__)
        return parsed

    types.Message._parse = staticmethod(parse)
