"""Add worker tokens without reading comments or modifying launcher scripts."""
import getpass
import os
from pathlib import Path
import runpy
import sys
from dotenv import set_key


def main():
    project = Path(__file__).resolve().parents[1]
    config = runpy.run_path(str(project / 'bot' / 'config.py'))
    parse = config['parse_worker_tokens']
    raw = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else getpass.getpass('Worker tokens (hidden; comma or space separated): ')
    new, ignored = parse(raw, config['Telegram'].BOT_TOKEN)
    if not new:
        print('No valid worker tokens supplied. Nothing changed.')
        return 1
    directory = Path.cwd() if any((Path.cwd() / name).is_file() for name in ('.env', 'config.env', 'start.sh')) else project
    target = directory / '.env'
    existing = config['Telegram'].MULTI_BOT_TOKENS
    combined, _ = parse(' '.join([*existing, *new]), config['Telegram'].BOT_TOKEN)
    target.touch(mode=0o600, exist_ok=True)
    set_key(str(target), 'MULTI_BOT_TOKENS', ' '.join(combined), quote_mode='always', encoding='utf-8-sig')
    print(f'Updated {target.name}: {len(combined)} worker tokens. Restart the bot to apply.')
    if 'MULTI_BOT_TOKENS' in os.environ or 'BOT_TOKENS' in os.environ:
        print('A system worker-token variable overrides this file; update or unset it in your process manager.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
