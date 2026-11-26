"""Central configuration. Environment > .env > config.env > literal start.sh exports."""
import io
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv.parser import parse_stream

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    pass


def read_settings(path, *, shell=False):
    """Use dotenv's grammar, retaining empty values and reporting only line numbers."""
    try:
        content = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ConfigurationError(f'Cannot read {path.name}. Check its permissions and UTF-8 encoding.') from None
    values = {}
    for binding in parse_stream(io.StringIO(content)):
        if binding.error and shell:
            continue
        if binding.error:
            raise ConfigurationError(f'Invalid assignment in {path.name} at line {binding.original.line}.')
        if binding.key is not None and binding.value is not None:
            if shell and any(text in binding.value for text in ('$(', '`', ';', ' && ', ' || ')):
                continue
            values[binding.key] = binding.value
    return values


def load_sources(directory=None, environ=None):
    directory = Path(directory) if directory is not None else (
        Path.cwd() if any((Path.cwd() / name).is_file() for name in ('.env', 'config.env', 'start.sh')) else PROJECT_ROOT)
    sources = [dict(os.environ if environ is None else environ)]
    sources.extend(read_settings(directory / name, shell=name == 'start.sh')
                   for name in ('.env', 'config.env', 'start.sh'))
    return sources


_sources = load_sources()
_errors = []


def setting(name, default='', *, aliases=()):
    # Resolve aliases by source so an exported legacy variable also takes
    # precedence over a canonical spelling in a lower-priority file.
    for source in _sources:
        for key in (name, *aliases):
            if key in source:
                value = source[key]
                legacy = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*):-([^{}]*)\}', value)
                if legacy:
                    return _sources[0].get(legacy[1], legacy[2])
                return value
    return default


def integer(name, default, *, minimum=0, maximum=None, aliases=(), empty=None):
    value = setting(name, str(default), aliases=aliases).strip()
    if value == '' and empty is not None:
        return empty
    try:
        parsed = int(value)
        if parsed < minimum or maximum is not None and parsed > maximum:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        bound = f'{minimum}..{maximum}' if maximum is not None else f'at least {minimum}'
        _errors.append(f'{name} must be an integer {bound}.')
        return default


def configured_path(name, default):
    value = setting(name).strip()
    path = Path(value).expanduser() if value else Path(default)
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_worker_tokens(value, primary=''):
    tokens = []
    ignored = 0
    for token in re.split(r'[\s,;]+', value.strip()):
        if not token or token == primary or token in tokens:
            continue
        if (not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{20,}', token)
                or re.search(r'(?i)example|placeholder|your[_-]|replace[_-]|abcdefghi|token[0-9]', token)):
            ignored += 1
            continue
        tokens.append(token)
    return tokens, ignored


class Telegram:
    API_ID = integer('TELEGRAM_API_ID', 0, minimum=1, maximum=2**31 - 1, aliases=('API_ID',))
    API_HASH = setting('TELEGRAM_API_HASH', aliases=('API_HASH',))
    BOT_TOKEN = setting('TELEGRAM_BOT_TOKEN', aliases=('BOT_TOKEN',)).strip()
    BOT_USERNAME = setting('TELEGRAM_BOT_USERNAME', aliases=('BOT_USERNAME',)).strip().lstrip('@')
    MULTI_BOT_TOKENS, IGNORED_WORKER_TOKENS = parse_worker_tokens(
        setting('MULTI_BOT_TOKENS', aliases=('BOT_TOKENS',)), BOT_TOKEN)
    WORKER_TOKENS = [BOT_TOKEN, *MULTI_BOT_TOKENS]
    OWNER_ID = integer('OWNER_ID', 0, minimum=1, maximum=2**63 - 1)
    REPAIR_API_ID = integer('WORKER_REPAIR_API_ID', 0, empty=0)
    REPAIR_API_HASH = setting('WORKER_REPAIR_API_HASH').strip()
    REPAIR_SESSION = setting('WORKER_REPAIR_SESSION').strip()
    CHANNEL_ID = integer('TELEGRAM_CHANNEL_ID', 0, minimum=-(2**63), maximum=0,
                         aliases=('CHANNEL_ID', 'BIN_CHANNEL_ID'), empty=0)
    SECRET_CODE_LENGTH = integer('SECRET_CODE_LENGTH', 24, minimum=8, maximum=64)


class Database:
    DATABASE_URL = setting('DATABASE_URL', '' if setting('SPACE_ID') else 'mongodb://localhost:27017',
                           aliases=('MONGODB_URI',))
    DATABASE_NAME = setting('DATABASE_NAME', 'OmniArchiver')
    OMNI_MONGO_URI = setting('OMNI_MONGO_URI', aliases=('ANIME_MONGO_URI',))


class Server:
    PORT = integer('PORT', 7860 if setting('SPACE_ID') else 8080, minimum=1, maximum=65535)
    BIND_ADDRESS = setting('BIND_ADDRESS', '0.0.0.0', aliases=('IP',)).strip()
    # Empty BASE_URL deliberately selects automatic host detection, as in older deployments.
    BASE_URL = (setting('BASE_URL') or (
        f"https://{setting('SPACE_HOST')}" if setting('SPACE_HOST') else f'http://127.0.0.1:{PORT}'
    )).rstrip('/')
    SHUTDOWN_TIMEOUT = integer('SHUTDOWN_TIMEOUT', 20, minimum=1, maximum=120)
    TRUSTED_PROXY_IPS = setting('TRUSTED_PROXY_IPS', '127.0.0.1')


class Resources:
    ZIP_CACHE_MAX_MB = integer('ZIP_CACHE_MAX_MB', 8192)
    ZIP_CACHE_DIR = configured_path('ZIP_CACHE_DIR', PROJECT_ROOT / 'cache' / 'archives')
    MEDIA_CACHE_MAX_MB = integer('MEDIA_CACHE_MAX_MB', 500)
    MEDIA_CACHE_DIR = configured_path('MEDIA_CACHE_DIR', PROJECT_ROOT / 'cache' / 'media')
    CACHE_FREE_MB = integer('CACHE_FREE_MB', 64)
    MEMORY_CLEAN_MB = integer('MEMORY_CLEAN_MB', 420, minimum=1)
    MEMORY_RESTART_MB = integer('MEMORY_RESTART_MB', 498)
    HANDLER_WORKERS = integer('HANDLER_WORKERS', 2, minimum=1, maximum=32)
    IO_WORKERS = integer('IO_WORKERS', 4, minimum=1, maximum=32)
    MOTOR_MAX_WORKERS = integer('MOTOR_MAX_WORKERS', 4, minimum=1, maximum=32)
    MAX_TRANSMISSIONS = integer('MAX_TRANSMISSIONS', 4, minimum=1, maximum=32)
    LOG_FILE = configured_path('LOG_FILE', PROJECT_ROOT / 'event-log.txt')


class Metadata:
    MAL_CLIENT_ID = setting('MAL_CLIENT_ID').strip()
    OMDB_API_KEY = setting('OMDB_API_KEY').strip()


def validate_config():
    errors = list(_errors)
    for name, value in (('TELEGRAM_API_HASH', Telegram.API_HASH), ('TELEGRAM_BOT_TOKEN', Telegram.BOT_TOKEN),
                        ('DATABASE_URL', Database.DATABASE_URL), ('DATABASE_NAME', Database.DATABASE_NAME)):
        if not value.strip():
            errors.append(f'{name} must not be empty.')
        elif name in ('TELEGRAM_API_HASH', 'TELEGRAM_BOT_TOKEN') and re.search(r'(?i)your_api_hash|replace_with|your_bot_token', value):
            errors.append(f'{name} still contains an example placeholder.')
    if any(char.isspace() for char in Server.BIND_ADDRESS) or not Server.BIND_ADDRESS:
        errors.append('BIND_ADDRESS must be a nonempty IP address or hostname.')
    try:
        url = urlsplit(Server.BASE_URL)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(c.isspace() for c in Server.BASE_URL)):
            raise ValueError
        url.port
    except ValueError:
        errors.append('BASE_URL must be a valid http:// or https:// URL without credentials, query, or fragment.')
    for name, value in (('DATABASE_URL', Database.DATABASE_URL), ('OMNI_MONGO_URI', Database.OMNI_MONGO_URI)):
        if value and not value.startswith(('mongodb://', 'mongodb+srv://')):
            errors.append(f'{name} must be a MongoDB connection URI.')
    if Resources.MEMORY_RESTART_MB and Resources.MEMORY_RESTART_MB <= Resources.MEMORY_CLEAN_MB:
        errors.append('MEMORY_RESTART_MB must exceed MEMORY_CLEAN_MB, or be 0 to disable automatic restart.')
    if errors:
        raise ConfigurationError('Invalid configuration. ' + ' '.join(dict.fromkeys(errors)))


def sanitize_text(text):
    secrets = (Telegram.BOT_TOKEN, Telegram.API_HASH, Telegram.REPAIR_API_HASH,
               Telegram.REPAIR_SESSION, Database.DATABASE_URL, Database.OMNI_MONGO_URI,
               Metadata.MAL_CLIENT_ID, Metadata.OMDB_API_KEY, *Telegram.MULTI_BOT_TOKENS)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)mongodb(?:\+srv)?://[^\s\"\'<>]+', '[REDACTED_DATABASE_URI]', text)
    text = re.sub(r'\b[0-9]+:[A-Za-z0-9_-]{20,}', '[REDACTED_BOT_TOKEN]', text)
    return re.sub(r'(?i)(https?://)[^/\s@:]+:[^/\s@]+@', r'\1[REDACTED]@', text)


class SanitizedFilter(logging.Filter):
    def filter(self, record):
        # Also sanitize the underlying record. Logging's emergency handleError
        # prints msg/args when the log itself hits EDQUOT/ENOSPC.
        record.msg = sanitize_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = sanitize_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class SanitizedFormatter(logging.Formatter):
    """Redact rendered messages and tracebacks, including third-party output."""
    def format(self, record):
        return sanitize_text(super().format(record))


# LOGGING CONFIGURATION
LOGGER_CONFIG_JSON = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            '()': SanitizedFormatter,
            'format': '[%(asctime)s][%(name)s][%(levelname)s] -> %(message)s',
            'datefmt': '%d/%m/%Y %H:%M:%S'
        },
    },
    'filters': {'secrets': {'()': SanitizedFilter}},
    'handlers': {
        'file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(Resources.LOG_FILE),
            'maxBytes': 2 * 1024 * 1024,
            'backupCount': 2,
            'formatter': 'default',
            'filters': ['secrets']
        },
        'stream_handler': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
            'filters': ['secrets']
        }
    },
    'root': {'level': 'WARNING', 'handlers': ['file_handler', 'stream_handler']},
    'loggers': {
        'uvicorn': {
            'propagate': False,
            'level': 'INFO',
            'handlers': ['file_handler', 'stream_handler']
        },
        'uvicorn.error': {
            'propagate': False,
            'level': 'WARNING',
            'handlers': ['file_handler', 'stream_handler']
        },
        'bot': {
            'propagate': False,
            'level': 'INFO',
            'handlers': ['file_handler', 'stream_handler']
        },
        'hydrogram': {
            'propagate': False,
            'level': 'WARNING',
            'handlers': ['file_handler', 'stream_handler']
        }
    }
}
