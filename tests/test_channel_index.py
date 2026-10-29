import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test_hash','TELEGRAM_BOT_TOKEN':'12345:test_token',
    'TELEGRAM_BOT_USERNAME':'TestGateStreamBot','OWNER_ID':'12345','DATABASE_URL':'mongodb://localhost:27017',
    'SPACE_ID':'user/app','SPACE_HOST':'user-app.hf.space',
}, clear=True):
    from bot.modules import channel_index
    from bot import clients
    with patch.object(clients.TelegramBot,'add_handler'):
        from bot.plugins import channel_index as plugin


class ChannelScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_uses_bounded_requests_and_resumes_saved_checkpoint(self):
        state=SimpleNamespace(find_one=AsyncMock(return_value={'last_scanned':50}),update_one=AsyncMock())
        def message(ident):return SimpleNamespace(id=ident,empty=False,document=object(),video=None,audio=None,voice=None,photo=None,video_note=None)
        async def get_messages(channel_id,message_ids):
            self.assertLessEqual(len(message_ids),50)
            return [message(i) for i in message_ids if i in (51,75,120)]
        client=SimpleNamespace(get_messages=AsyncMock(side_effect=get_messages))
        stop=asyncio.Event()
        with patch.object(channel_index.db,'db',{'index_channels':state}),patch.object(channel_index,'save_scanned_file',AsyncMock(return_value=('added','anime'))) as save:
            last,counts=await channel_index.scan_channel(client,-100123,120,12345,stop,AsyncMock())
        self.assertEqual(last,120)
        self.assertEqual(counts['added'],3)
        self.assertEqual(counts['skipped'],67)
        self.assertEqual(save.await_count,3)
        self.assertEqual(client.get_messages.call_args_list[0].kwargs['message_ids'][0],51)
        self.assertEqual(state.update_one.call_args_list[-1].args[1]['$max']['last_scanned'],120)

    async def test_cancel_checkpoints_only_processed_messages(self):
        state=SimpleNamespace(find_one=AsyncMock(return_value={}),update_one=AsyncMock())
        client=SimpleNamespace(get_messages=AsyncMock(return_value=[]))
        stop=asyncio.Event()
        async def report(*args):stop.set()
        with patch.object(channel_index.db,'db',{'index_channels':state}):
            last,counts=await channel_index.scan_channel(client,-100123,1000,12345,stop,report)
        self.assertEqual(last,1)
        self.assertEqual(state.update_one.call_args.args[1]['$max']['last_scanned'],1)
        self.assertEqual(client.get_messages.await_count,1)

    async def test_admin_only_and_supported_channel_targets(self):
        from hydrogram.types import Message
        message=MagicMock(spec=Message)
        message.text='/index https://t.me/c/123456/500'
        message.reply_to_message=None
        message.from_user=SimpleNamespace(id=999)
        message.reply=AsyncMock()
        client=SimpleNamespace(get_chat=AsyncMock())
        await plugin.index_command(client,message)
        client.get_chat.assert_not_awaited()
        self.assertIn('admins',message.reply.call_args.args[0])
        self.assertEqual(plugin.parse_target('https://t.me/c/123456/500'),(-100123456,500))
        self.assertEqual(plugin.parse_target('@MyChannel'),('MyChannel',None))
        self.assertEqual(plugin.parse_target('-100123456'),(-100123456,None))
        with self.assertRaises(ValueError):plugin.parse_target('https://evil.example/channel')

    async def test_private_link_record_is_not_published_by_scanner(self):
        collections=[]
        for name in ('movies','anime','webseries','direct_files'):
            collections.append(SimpleNamespace(name=name,find_one=AsyncMock(return_value=None),find_one_and_update=AsyncMock()))
        properties=('Cowboy Bebop.mkv',100,'video/x-matroska',30,'00:30')
        message=SimpleNamespace(chat=SimpleNamespace(id=-100123),id=2)
        classifier=SimpleNamespace(classify=AsyncMock(return_value=dict(category='anime',title='Cowboy Bebop',status='matched',source='AniList')))
        with patch.multiple(channel_index.db,**dict(zip(('movies','anime','webseries','direct_files'),collections))),patch.object(channel_index.db,'get_collection',return_value=collections[1]),patch.object(channel_index,'get_file_properties',return_value=properties):
            result=await channel_index.save_scanned_file(message,12345,classifier)
        self.assertEqual(result,('added','anime'))
        for collection in collections:
            self.assertEqual(collection.find_one.call_args.args[0]['catalog_section'],'index')
        query,update=collections[1].find_one_and_update.call_args.args
        self.assertEqual(query['catalog_section'],'index')
        self.assertEqual(update['$set']['metadata']['source'],'AniList')
        self.assertEqual(update['$set']['catalog_section'],'index')
