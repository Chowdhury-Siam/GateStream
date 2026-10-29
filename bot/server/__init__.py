from quart import Quart
from uvicorn import Server as UvicornServer, Config
from logging import getLogger
from bot.config import Server, LOGGER_CONFIG_JSON
from . import main, error, catalog
from bot.modules.activity_store import activity_store
from bot.modules.archive_cache import archive_cache

logger = getLogger('uvicorn')
instance = Quart(__name__)
instance.config['RESPONSE_TIMEOUT'] = None
instance.config['BODY_TIMEOUT'] = 300
instance.config['BACKGROUND_TASK_SHUTDOWN_TIMEOUT'] = 5
instance.config['MAX_CONTENT_LENGTH'] = 999999999999999

@instance.before_serving
async def before_serve():
    await activity_store.flush()
    instance.add_background_task(activity_store.run, instance.shutdown_event)
    logger.info('Web server is started!')
    logger.info(f'Server running on {Server.BIND_ADDRESS}:{Server.PORT}')

@instance.after_serving
async def after_serve():
    await archive_cache.close()
    await activity_store.flush()


instance.register_blueprint(main.bp)
instance.register_blueprint(catalog.bp)

instance.register_error_handler(400, error.invalid_request)
instance.register_error_handler(404, error.not_found)
instance.register_error_handler(405, error.invalid_method)
instance.register_error_handler(error.HTTPError, error.http_error)

server = UvicornServer(
    Config(
        app=instance,
        host=Server.BIND_ADDRESS,
        port=Server.PORT,
        timeout_keep_alive=300,
        timeout_notify=120,
        log_config=LOGGER_CONFIG_JSON
    )
)
