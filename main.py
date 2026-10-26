"""Avenor Builder: asosiy bot + yaratilgan botlar bitta jarayonda."""
import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import child
import config
import db
import madmin  # noqa: F401  (admin handlerlarini ro'yxatdan o'tkazadi)
import master
from manager import Manager

log = logging.getLogger("builder")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        sys.exit("BOT_TOKEN o'rnatilmagan (asosiy bot tokeni).")
    if not config.ADMIN_IDS:
        log.warning("ADMIN_IDS bo'sh: admin panel hech kimga ochilmaydi. O'z Telegram ID'ingizni ADMIN_IDS ga yozing.")
    db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    config.MASTER_USERNAME = me.username

    async def notify(uid, text):
        await bot.send_message(uid, text)

    mgr = Manager(child.dp, notify)
    master.mgr = mgr
    await mgr.start_all()
    log.info("@%s ishga tushdi. Yaratilgan botlar: %s", me.username, len(mgr.tasks))
    try:
        await master.dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])
    finally:
        await mgr.stop_all()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
