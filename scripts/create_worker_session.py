"""Create a GateStream repair session on a trusted computer, outside Telegram chat."""
import asyncio
import getpass
import os
import sys
from pathlib import Path

from hydrogram import Client
from hydrogram.errors import SessionPasswordNeeded


async def create_session():
    output = Path('.env.worker-repair')
    if output.exists():
        raise FileExistsError
    api_id = int(input('Telegram API ID: ').strip())
    api_hash = getpass.getpass('Telegram API hash: ').strip()
    phone = getpass.getpass('Owner phone number with country code: ').strip()
    if api_id <= 0 or len(api_hash) != 32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash) or not phone:
        raise ValueError
    client = Client('worker_repair_setup', api_id=api_id, api_hash=api_hash,
                    in_memory=True, no_updates=True, sleep_threshold=0)
    try:
        await client.connect()
        sent = await client.send_code(phone)
        code = getpass.getpass('Telegram login code: ').strip()
        try:
            user = await client.sign_in(phone, sent.phone_code_hash, code)
        except SessionPasswordNeeded:
            user = await client.check_password(getpass.getpass('Telegram two-step password: '))
        if not getattr(user, 'id', None) or user.is_bot:
            raise ValueError
        session = await client.export_session_string()
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(f'WORKER_REPAIR_API_ID={api_id}\nWORKER_REPAIR_API_HASH={api_hash}\n'
                       f'WORKER_REPAIR_SESSION={session}\n')
        print(f'Saved the repair settings to {output}. Telegram user ID: {user.id}.')
        print('Copy the three values to your host secrets. Keep this file private.')
    finally:
        if client.is_connected:
            await client.disconnect()


if __name__ == '__main__':
    if not sys.stdin.isatty():
        sys.exit('Run this script in an interactive terminal.')
    try:
        asyncio.run(create_session())
    except (KeyboardInterrupt, EOFError):
        sys.exit('Setup canceled.')
    except FileExistsError:
        sys.exit('.env.worker-repair already exists. Move it to a private location before generating another.')
    except Exception as error:
        sys.exit(f'Session setup failed ({type(error).__name__}). Check your credentials or Telegram app.')
