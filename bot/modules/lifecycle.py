"""Track application work and route restarts through the normal shutdown path."""
import asyncio
import logging
from contextvars import Context

logger = logging.getLogger('bot')
_tasks = set()
_stopping = False
_stop_event = None
restart_requested = False


def begin(stop_event):
    global _stopping, _stop_event, restart_requested
    _stopping = False
    _stop_event = stop_event
    restart_requested = False


def request_restart():
    global restart_requested
    if _stop_event is None or _stopping:
        return False
    restart_requested = True
    _stop_event.set()
    return True


def spawn(coroutine):
    if _stopping:
        coroutine.close()
        return None
    task = asyncio.create_task(coroutine, context=Context())
    _tasks.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task):
    _tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning('Background task failed (%s).', type(task.exception()).__name__)


async def close():
    global _stopping, _stop_event
    _stopping = True
    _stop_event = None
    tasks = tuple(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
