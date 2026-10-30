import asyncio
import copy
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

with patch.dict(os.environ, {
    'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test_hash','TELEGRAM_BOT_TOKEN':'12345:test_token',
    'OWNER_ID':'12345','DATABASE_URL':'mongodb://localhost:27017',
}, clear=True):
    from bot import clients
    from bot.config import Drive, Telegram
    from bot.modules import runtime_settings as runtime
    from bot.modules import drive as drive_io
    with patch.object(clients.TelegramBot,'add_handler'):
        from bot.plugins import settings as ui, drive as drive_plugin, files
    from hydrogram import StopPropagation

TOKEN = json.dumps(dict(type='authorized_user',client_id='test.apps.googleusercontent.com',client_secret='private-client-secret',
                        refresh_token='private-refresh-token',token_uri='https://evil.invalid/token')).encode()


class Collection:
    def __init__(self):
        self.document = {'_id':'bot_runtime'}
        self.fail = False

    async def find_one(self, query):
        if self.fail: raise RuntimeError('database offline')
        return copy.deepcopy(self.document)

    async def update_one(self, query, update, **kwargs):
        if self.fail: raise RuntimeError('database offline')
        for op, values in update.items():
            for key,value in values.items():
                target=self.document
                parts=key.split('.')
                for part in parts[:-1]: target=target.setdefault(part,{})
                if op=='$set': target[parts[-1]]=copy.deepcopy(value)
                else: target.pop(parts[-1],None)


def message(text='',user=12345,chat=None,document=None,reply=None,ident=7):
    result=SimpleNamespace(text=text,from_user=SimpleNamespace(id=user),chat=SimpleNamespace(id=user if chat is None else chat),
        id=ident,document=document,reply_to_message=reply,delete=AsyncMock(),reply=AsyncMock(),edit_text=AsyncMock())
    result.reply.return_value=message_stub(100)
    return result


def message_stub(ident):
    return SimpleNamespace(id=ident,delete=AsyncMock())


class BotSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved={key:runtime.current(key) for key in runtime.FIELDS}
        self.token_state=(runtime._uploaded_json,runtime._token_error,set(runtime._overrides))
        runtime._uploaded_json=None;runtime._token_error=False
        self.storage=Collection()
        self.db_patch=patch.object(runtime,'collection',return_value=self.storage)
        self.db_patch.start();self.addCleanup(self.db_patch.stop)
        self.owner_patch=patch.object(Telegram,'OWNER_ID',12345)
        self.owner_patch.start();self.addCleanup(self.owner_patch.stop)
        ui._pending.clear()

    async def asyncTearDown(self):
        ui._pending.clear()
        for key,value in self.saved.items():runtime.apply(key,value)
        runtime._uploaded_json,runtime._token_error,runtime._overrides=self.token_state

    async def stopped(self, awaitable):
        with self.assertRaises(StopPropagation): await awaitable

    async def test_saved_settings_validate_and_restore_without_changing_bootstrap_configuration(self):
        await runtime.save('DRIVE_FOLDER_ID','https://drive.google.com/drive/folders/destination12345')
        await runtime.save('DRIVE_MAX_MB','2048')
        await runtime.save('ZIP_CACHE_MAX_MB','4096')
        await runtime.save('TELEGRAM_CHANNEL_ID','-1001234567890')
        for key in ('DRIVE_FOLDER_ID','DRIVE_MAX_MB','ZIP_CACHE_MAX_MB','TELEGRAM_CHANNEL_ID'):
            runtime.apply(key,runtime.DEFAULTS[key])
        self.assertTrue(await runtime.load())
        self.assertEqual(Drive.FOLDER_ID,'destination12345')
        self.assertEqual(runtime.current('ZIP_CACHE_MAX_MB'),4096)
        self.assertEqual(Telegram.CHANNEL_ID,-1001234567890)
        for key,value in [('OWNER_ID','999'),('DATABASE_NAME','NewDB'),('DRIVE_MAX_MB','-5'),
                          ('DRIVE_INDEX_URL','http://index.example/0:/'),('DRIVE_CLONE_MAX_FILES','10001')]:
            with self.subTest(key=key),self.assertRaises(runtime.SettingsError):await runtime.save(key,value)
        self.assertEqual(Telegram.OWNER_ID,12345)
        await runtime.save('DRIVE_MAX_MB',reset=True)
        self.assertEqual(runtime.current('DRIVE_MAX_MB'),runtime.DEFAULTS['DRIVE_MAX_MB'])

    async def test_secret_encryption_restoration_and_token_rotation_failure(self):
        await runtime.save('OMDB_API_KEY','private-api-key')
        await runtime.store_token(TOKEN)
        stored=json.dumps(self.storage.document)
        for secret in ('private-api-key','private-client-secret','private-refresh-token'):
            self.assertNotIn(secret,stored)
        runtime._uploaded_json=None
        self.assertTrue(await runtime.load())
        credentials=runtime.uploaded_credentials()
        self.assertEqual(credentials.refresh_token,'private-refresh-token')
        self.assertEqual(credentials.token_uri,'https://oauth2.googleapis.com/token')
        self.assertEqual(ui.value_text('OMDB_API_KEY'),'Configured')
        with patch.object(Telegram,'BOT_TOKEN','different:rotated-token'):
            self.assertTrue(await runtime.load())
            self.assertFalse(Drive.ENABLED)
            with self.assertRaises(runtime.SettingsError):runtime.uploaded_credentials()
        await runtime.remove_token()
        self.assertIsNone(runtime.uploaded_credentials())
        self.assertNotIn('drive_token',self.storage.document)

    async def test_failed_writes_and_reads_keep_current_values_and_token(self):
        await runtime.store_token(TOKEN)
        before=runtime.current('DRIVE_FOLDER_ID')
        self.storage.fail=True
        with self.assertRaises(RuntimeError):await runtime.save('DRIVE_FOLDER_ID','different12345')
        with self.assertRaises(RuntimeError):await runtime.remove_token()
        self.assertEqual(runtime.current('DRIVE_FOLDER_ID'),before)
        self.assertIsNotNone(runtime.uploaded_credentials())
        self.assertFalse(await runtime.load())
        self.assertIsNotNone(runtime.uploaded_credentials())

    async def test_invalid_tokens_cannot_execute_pickle_or_accept_arbitrary_credentials(self):
        for raw in (b'\x80\x04pickle',b'{}',b'[]',b'{"type":"service_account"}',TOKEN*1000):
            with self.subTest(raw=raw[:20]),self.assertRaises(runtime.SettingsError):await runtime.store_token(raw)
        self.assertNotIn('drive_token',self.storage.document)

    async def test_panel_is_owner_only_and_cannot_be_used_in_groups(self):
        for user,chat in ((999,999),(12345,-1001234567890)):
            msg=message('/bset',user,chat)
            await self.stopped(ui.bot_settings(None,msg))
            self.assertIn('owner',msg.reply.call_args.args[0])
            query=SimpleNamespace(message=msg,from_user=msg.from_user,answer=AsyncMock(),data='bset:toggle')
            with patch.object(runtime,'save',AsyncMock()) as save:
                await self.stopped(ui.settings_callback(None,query))
                save.assert_not_awaited()
        msg=message('/bset')
        await self.stopped(ui.bot_settings(None,msg))
        keyboard=msg.reply.call_args.kwargs['reply_markup'].inline_keyboard
        labels=[row[0].text for row in keyboard]
        self.assertTrue(all(len(row)==1 for row in keyboard))
        self.assertTrue(any('PRIVATE FILES' in label for label in labels))
        self.assertTrue(any('GOOGLE DRIVE' in label for label in labels))
        self.assertFalse(any('QBITTORRENT' in label for label in labels))

    async def test_reply_bound_input_and_expired_prompts_cannot_change_settings(self):
        msg=message()
        await ui.prompt(msg,'DRIVE_FOLDER_ID')
        before=Drive.FOLDER_ID
        wrong=message('destination12345',reply=message_stub(999))
        await self.stopped(ui.settings_input(None,wrong))
        self.assertEqual(Drive.FOLDER_ID,before)
        self.assertFalse(wrong.reply.call_args.kwargs['quote'])
        wrong.delete.assert_awaited_once()
        correct=message('destination12345',reply=message_stub(100))
        await self.stopped(ui.settings_input(None,correct))
        self.assertEqual(Drive.FOLDER_ID,'destination12345')
        correct.delete.assert_awaited_once()
        self.assertNotIn(12345,ui._pending)
        await ui.prompt(msg,'DRIVE_FOLDER_ID')
        ui._pending[12345]['expires']=0
        stale=message('another12345',reply=message_stub(100))
        await self.stopped(ui.settings_input(None,stale))
        self.assertEqual(Drive.FOLDER_ID,'destination12345')
        self.assertFalse(stale.reply.call_args.kwargs['quote'])
        stale.delete.assert_awaited_once()

    async def test_private_upload_is_encrypted_deleted_and_never_falls_through_to_media(self):
        start=message()
        await ui.prompt(start,'DRIVE_TOKEN')
        doc=SimpleNamespace(file_name='token.json',file_size=len(TOKEN))
        upload=message(document=doc,reply=message_stub(100))
        client=SimpleNamespace(download_media=AsyncMock(return_value=io.BytesIO(TOKEN)))
        await self.stopped(ui.settings_input(client,upload))
        upload.delete.assert_awaited_once()
        self.assertIsNotNone(runtime.uploaded_credentials())
        self.assertNotIn('private-refresh-token',json.dumps(self.storage.document))
        # Outside the upload flow, the normal media handler blocks credentials too.
        with patch.object(files,'save_file',AsyncMock()) as save:
            await files.handle_user_file.__wrapped__(None,upload)
            save.assert_not_awaited()
        self.assertIn('Private credential',upload.reply.call_args.args[0])

    async def test_large_and_wrong_private_files_are_rejected_before_download(self):
        client=SimpleNamespace(download_media=AsyncMock())
        for filename,size in [('token.pickle',30),('other.json',30),('token.json',65537)]:
            await ui.prompt(message(),'DRIVE_TOKEN')
            incoming=message(document=SimpleNamespace(file_name=filename,file_size=size),reply=message_stub(100))
            await self.stopped(ui.settings_input(client,incoming))
        client.download_media.assert_not_awaited()
        self.assertIsNone(runtime.uploaded_credentials())

    async def test_drive_job_lock_blocks_credentials_and_config_changes(self):
        original=Drive.FOLDER_ID
        async with drive_plugin._job_lock:
            with self.assertRaises(runtime.SettingsError):
                await ui.mutate('DRIVE_FOLDER_ID',runtime.save,'DRIVE_FOLDER_ID','destination12345')
            with self.assertRaises(runtime.SettingsError):
                await ui.mutate('DRIVE_TOKEN',runtime.store_token,TOKEN)
        self.assertEqual(Drive.FOLDER_ID,original)
        self.assertIsNone(runtime.uploaded_credentials())

    async def test_check_uses_folder_capabilities_without_writing_files(self):
        service=MagicMock()
        context=MagicMock();context.__enter__.return_value=service
        api=service.files.return_value
        api.get.return_value.execute.return_value=dict(mimeType=drive_io.FOLDER_MIME,capabilities={'canAddChildren':True})
        with patch.object(drive_io,'build',return_value=context):
            self.assertTrue(drive_io.check_destination(object(),'destination12345'))
            api.get.return_value.execute.return_value=dict(mimeType=drive_io.FOLDER_MIME,capabilities={'canAddChildren':False})
            with self.assertRaises(drive_io.DriveTransferError):drive_io.check_destination(object(),'destination12345')
        api.create.assert_not_called();api.copy.assert_not_called()

    async def test_startup_restores_settings_before_starting_telegram(self):
        await runtime.save('DRIVE_FOLDER_ID','destination12345')
        runtime.apply('DRIVE_FOLDER_ID','')
        database=SimpleNamespace(command=AsyncMock(),__getitem__=lambda *args:self.storage)
        from bot.modules.activity_store import activity_store
        async def start():self.assertEqual(Drive.FOLDER_ID,'destination12345')
        with patch.object(clients.db,'connect'),patch.object(clients.db,'db',database), \
             patch.object(clients.db,'create_indexes',AsyncMock()),patch.object(activity_store,'flush',AsyncMock()), \
             patch.object(clients.TelegramBot,'start',AsyncMock(side_effect=start)), \
             patch.object(clients.TelegramBot,'me',SimpleNamespace(username='TestBot')), \
             patch.object(clients,'init_worker_clients'),patch.object(clients,'worker_clients',[]), \
             patch.object(Telegram,'BOT_USERNAME',Telegram.BOT_USERNAME):
            await clients.start_all_clients()

    async def test_all_panel_keyboards_serialize_with_telegram_callback_limits(self):
        for page in ['home','config:0','config:1','modules','drive','private','remove','status','drivehelp'] + ['field:'+key for key in runtime.FIELDS]:
            text,keyboard=ui.panel(page)
            self.assertLess(len(text),4096)
            for row in keyboard.inline_keyboard:
                self.assertEqual(len(row),1)
                self.assertLessEqual(len(row[0].callback_data.encode()),64)
                self.assertNotIn('private-api-key',row[0].text)
            raw=await keyboard.write(None)
            self.assertEqual(len(raw.rows),len(keyboard.inline_keyboard))

    async def test_callback_edits_persist_and_drive_setup_alias_opens_drive_page(self):
        msg=message('/dset')
        await self.stopped(ui.bot_settings(None,msg))
        self.assertIn('GOOGLE DRIVE',msg.reply.call_args.args[0])
        query=SimpleNamespace(message=msg,from_user=msg.from_user,answer=AsyncMock(),data='bset:toggle')
        before=Drive.ENABLED
        await self.stopped(ui.settings_callback(None,query))
        self.assertEqual(Drive.ENABLED,not before)
        self.assertEqual(self.storage.document['values']['DRIVE_ENABLED'],not before)
        await ui.prompt(msg,'OMDB_API_KEY')
        reply=message('private-api-key',reply=message_stub(100))
        await self.stopped(ui.settings_input(None,reply))
        self.assertNotIn('private-api-key',str(reply.reply.call_args))
        self.assertNotIn('private-api-key',json.dumps(self.storage.document))
