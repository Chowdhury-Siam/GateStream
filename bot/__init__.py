import os
from logging import getLogger
from logging.config import dictConfig
from bot.config import Resources, LOGGER_CONFIG_JSON, validate_config

validate_config()
# Motor reads this documented setting once on import. Publish the centrally
# validated value before any module imports Motor, including values from .env.
os.environ['MOTOR_MAX_WORKERS'] = str(Resources.MOTOR_MAX_WORKERS)
dictConfig(LOGGER_CONFIG_JSON)

version = "2.0.36"
logger = getLogger('bot')

# Keep legacy imports available without constructing clients before asyncio.run.
def __getattr__(name):
    if name in {'TelegramBot', 'worker_clients', 'get_worker_client', 'start_all_clients', 'stop_all_clients'}:
        from bot import clients
        return getattr(clients, name)
    raise AttributeError(name)
