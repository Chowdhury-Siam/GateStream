import os
import re
from os import environ as env

# Auto-load variables from start.sh, .env or config.env if present (supporting multi-line quoted strings)
def _load_env_files():
    for fn in ("start.sh", ".env", "config.env"):
        if os.path.exists(fn):
            try:
                with open(fn, "r", encoding="utf-8") as f:
                    content = f.read()
                matches = re.findall(
                    r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))',
                    content
                )
                for k, v1, v2, v3 in matches:
                    v = (v1 or v2 or v3 or "").strip()
                    # Resolve bash parameter expansion like ${PORT:-8080}
                    bash_match = re.match(r'^\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}$', v)
                    if bash_match:
                        var_name, default_val = bash_match.groups()
                        v = os.environ.get(var_name, default_val)
                    if k and v:
                        os.environ.setdefault(k, v)
            except Exception:
                pass

_load_env_files()

def _get_int(val, default=0) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return default

class Telegram:
    API_ID = _get_int(env.get("TELEGRAM_API_ID", env.get("API_ID", 0)))
    API_HASH = env.get("TELEGRAM_API_HASH", env.get("API_HASH", ""))
    
    # Primary Bot
    BOT_TOKEN = env.get("TELEGRAM_BOT_TOKEN", env.get("BOT_TOKEN", ""))
    BOT_USERNAME = env.get("TELEGRAM_BOT_USERNAME", env.get("BOT_USERNAME", ""))
    
    # Multi-client worker tokens (comma, space, or newline separated)
    _raw_multi_tokens = env.get("MULTI_BOT_TOKENS", env.get("BOT_TOKENS", ""))
    MULTI_BOT_TOKENS = [
        tok.strip() for tok in _raw_multi_tokens.replace(",", " ").split() if tok.strip()
    ]
    # Worker pool combining primary + worker tokens (preserving order, removing duplicates)
    WORKER_TOKENS = list(dict.fromkeys([BOT_TOKEN] + MULTI_BOT_TOKENS)) if MULTI_BOT_TOKENS else [BOT_TOKEN]

    # Owner & Auth/Admin Users
    OWNER_ID = _get_int(env.get("OWNER_ID", 0))
    _raw_auth_users = env.get("AUTH_USERS", "")
    AUTH_USERS = [
        int(uid.strip()) for uid in _raw_auth_users.replace(",", " ").split() if uid.strip().lstrip("-").isdigit()
    ]
    ADMIN_IDS = set([OWNER_ID] + AUTH_USERS)

    # Allowed Users for general usage (empty = everyone allowed)
    ALLOWED_USER_IDS = env.get("ALLOWED_USER_IDS", "").split()
    
    # Optional Storage / Bin Channel ID (Only needed if you upload files directly in private DM)
    CHANNEL_ID = _get_int(env.get("TELEGRAM_CHANNEL_ID", env.get("CHANNEL_ID", env.get("BIN_CHANNEL_ID", 0))), 0)
    SECRET_CODE_LENGTH = _get_int(env.get("SECRET_CODE_LENGTH", 24), 24)

class Database:
    DATABASE_URL = env.get("DATABASE_URL", env.get(
        "MONGODB_URI", "" if env.get("SPACE_ID") else "mongodb://localhost:27017"
    ))
    DATABASE_NAME = env.get("DATABASE_NAME", "OmniArchiver")
    OMNI_MONGO_URI = env.get(
        "OMNI_MONGO_URI",
        env.get("ANIME_MONGO_URI", "")
    )

class Server:
    PORT = _get_int(env.get("PORT"), 7860 if env.get("SPACE_ID") else 8080)
    BASE_URL = (env.get("BASE_URL") or (
        f"https://{env['SPACE_HOST']}" if env.get("SPACE_HOST") else f"http://127.0.0.1:{PORT}"
    )).rstrip("/")
    BIND_ADDRESS = env.get("BIND_ADDRESS", env.get("IP", "0.0.0.0"))
    if BIND_ADDRESS.startswith("${"):
        BIND_ADDRESS = "0.0.0.0"


class Metadata:
    MAL_CLIENT_ID = env.get('MAL_CLIENT_ID', '').strip()
    OMDB_API_KEY = env.get('OMDB_API_KEY', '').strip()


class Drive:
    ENABLED = env.get("DRIVE_ENABLED", "false").lower() in ("1", "true", "yes")
    TOKEN_PATH = env.get("DRIVE_TOKEN_PATH", "token.pickle")
    TOKEN_PICKLE_B64 = env.get("DRIVE_TOKEN_PICKLE_B64", "")
    FOLDER_ID = env.get("DRIVE_FOLDER_ID", "").strip()
    MAX_BYTES = _get_int(env.get("DRIVE_MAX_MB", 1024), 1024) * 1024 * 1024
    INDEX_URL = env.get("DRIVE_INDEX_URL", "").strip()
    CLONE_MAX_FILES = _get_int(env.get("DRIVE_CLONE_MAX_FILES", 1000), 1000)


def validate_config():
    required = {
        "TELEGRAM_API_ID": Telegram.API_ID > 0,
        "TELEGRAM_API_HASH": bool(Telegram.API_HASH.strip()),
        "TELEGRAM_BOT_TOKEN": bool(Telegram.BOT_TOKEN.strip()),
        "OWNER_ID": Telegram.OWNER_ID > 0,
        "DATABASE_URL": bool(Database.DATABASE_URL.strip()),
    }
    missing = [name for name, valid in required.items() if not valid]
    if missing:
        raise ValueError("Set these required environment variables / Space secrets: " + ", ".join(missing))
    if not 1 <= Server.PORT <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    if not re.match(r"^https?://[^/\s]+", Server.BASE_URL):
        raise ValueError("BASE_URL must be an http:// or https:// URL")

# LOGGING CONFIGURATION
LOGGER_CONFIG_JSON = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '[%(asctime)s][%(name)s][%(levelname)s] -> %(message)s',
            'datefmt': '%d/%m/%Y %H:%M:%S'
        },
    },
    'handlers': {
        'file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'event-log.txt',
            'maxBytes': 2 * 1024 * 1024,
            'backupCount': 2,
            'formatter': 'default'
        },
        'stream_handler': {
            'class': 'logging.StreamHandler',
            'formatter': 'default'
        }
    },
    'loggers': {
        'uvicorn': {
            'level': 'INFO',
            'handlers': ['file_handler', 'stream_handler']
        },
        'uvicorn.error': {
            'level': 'WARNING',
            'handlers': ['file_handler', 'stream_handler']
        },
        'bot': {
            'level': 'INFO',
            'handlers': ['file_handler', 'stream_handler']
        },
        'hydrogram': {
            'level': 'WARNING',
            'handlers': ['file_handler', 'stream_handler']
        }
    }
}
