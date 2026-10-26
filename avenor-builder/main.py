"""Avenor Builder: asosiy bot + yaratilgan botlar bitta jarayonda."""
import asyncio
import logging
import signal
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import child
import config
import db
import madmin  # noqa: F401  (admin handlerlarini ro'yxatdan o'tkazadi)
import master
import status
from manager import Manager

log = logging.getLogger("builder")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        sys.exit("BOT_TOKEN o'rnatilmagan (asosiy bot tokeni).")
    if not config.ADMIN_IDS:
        log.warning("ADMIN_IDS bo'sh: admin panel hech kimga ochilmaydi. O'z Telegram ID'ingizni ADMIN_IDS ga yozing.")
    db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    me = await bot.get_me()
    config.MASTER_USERNAME = me.username

    async def notify(uid, text):
        await bot.send_message(uid, text)

    mgr = Manager(child.dp, notify)
    master.mgr = mgr
    st = status.Status(bot, config.ADMIN_IDS, config.RUN_SECONDS)

    # To'xtatish signallari (GitHub bekor qilganda / yangilanishda / timeout): tartibli yakunlash
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows

    await mgr.start_all()
    st.event(f"{len(mgr.tasks)} ta yaratilgan bot ulandi")
    log.info("@%s ishga tushdi. Yaratilgan botlar: %s", me.username, len(mgr.tasks))
    await st.start(me.username, len(mgr.tasks))

    poll = asyncio.create_task(master.dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"]))
    waiter = asyncio.create_task(stop.wait())
    life = asyncio.create_task(st.loop())
    reason, exc = "signal", None
    try:
        done, _ = await asyncio.wait({poll, waiter}, timeout=config.RUN_SECONDS or None,
                                     return_when=asyncio.FIRST_COMPLETED)
        if poll in done:
            exc = poll.exception() if not poll.cancelled() else None
            reason = "crash" if exc else "signal"
        elif waiter in done:
            reason = "signal"
        else:
            reason = "deadline"
            log.info("Rejalashtirilgan vaqt tugadi (%s s): qayta ishga tushirish uchun to'xtayapman.", config.RUN_SECONDS)
    finally:
        for t in (poll, waiter, life):
            t.cancel()
        await asyncio.gather(poll, waiter, life, return_exceptions=True)
        await st.finish(reason)
        await mgr.stop_all()
        await bot.session.close()
    if exc:
        raise exc


if __name__ == "__main__":
    asyncio.run(main())
