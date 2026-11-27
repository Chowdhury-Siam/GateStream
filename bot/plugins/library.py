from hydrogram import Client, filters

from bot.modules.decorators import verify_user
from bot.modules.personal_library import send_login_link


@Client.on_message(filters.command('catalog') & filters.private & filters.text)
@verify_user
async def catalog_command(_, message):
    await send_login_link(message)
