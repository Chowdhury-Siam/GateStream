from logging import getLogger
from logging.config import dictConfig
from bot.config import Telegram, LOGGER_CONFIG_JSON, validate_config

validate_config()
dictConfig(LOGGER_CONFIG_JSON)

version = "2.0.5"
logger = getLogger('bot')

from bot.clients import TelegramBot, worker_clients, get_worker_client, start_all_clients, stop_all_clients
