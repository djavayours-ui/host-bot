"""Yaratilgan botlarni bitta jarayonda ishga tushiradi: har bot uchun o'z polling sikli."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramRetryAfter, TelegramUnauthorizedError

import db

log = logging.getLogger("builder.manager")
UPDATES = ["message", "callback_query"]


class Manager:
    def __init__(self, dp: Dispatcher, notify=None):
        self.dp = dp
        self.notify = notify                 # async (owner_id, text)
        self.session = AiohttpSession()      # barcha botlar uchun umumiy ulanishlar
        self.bots = {}                       # bot_id -> Bot
        self.tasks = {}                      # bot_id -> asyncio.Task
        self._jobs = set()

    def make_bot(self, token):
        return Bot(token, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    def is_running(self, bot_id):
        t = self.tasks.get(bot_id)
        return bool(t and not t.done())

    def get(self, bot_id):
        return self.bots.get(bot_id)

    async def start_bot(self, row):
        await self.stop_bot(row["id"])
        bot = self.make_bot(row["token"])
        self.bots[row["id"]] = bot
        self.tasks[row["id"]] = asyncio.create_task(self._run(row["id"], bot), name=f"child-{row['id']}")

    async def stop_bot(self, bot_id):
        t = self.tasks.pop(bot_id, None)
        self.bots.pop(bot_id, None)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def start_all(self):
        for row in db.all_bots():
            if row["active"]:
                await self.start_bot(row)

    async def stop_all(self):
        for bid in list(self.tasks):
            await self.stop_bot(bid)
        await self.session.close()

    async def _feed(self, bot, upd):
        try:
            await self.dp.feed_update(bot, upd)
        except Exception:
            log.exception("update xatosi (bot %s)", bot.id)

    async def _run(self, bot_id, bot):
        offset, backoff = None, 1
        try:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except TelegramUnauthorizedError:
                return await self._dead(bot_id)
            except Exception:
                pass
            while True:
                try:
                    updates = await bot.get_updates(offset=offset, timeout=25, allowed_updates=UPDATES,
                                                    request_timeout=60)
                    backoff = 1
                    for u in updates:
                        offset = u.update_id + 1
                        job = asyncio.create_task(self._feed(bot, u))
                        self._jobs.add(job)
                        job.add_done_callback(self._jobs.discard)
                except asyncio.CancelledError:
                    raise
                except TelegramUnauthorizedError:
                    return await self._dead(bot_id)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramConflictError:
                    log.warning("Bot %s: boshqa joyda ham ishlayapti (Conflict)", bot_id)
                    await asyncio.sleep(10)
                except Exception as e:
                    log.warning("Bot %s polling xatosi: %s", bot_id, e)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
        except asyncio.CancelledError:
            raise

    async def _dead(self, bot_id):
        """Token bekor qilingan: botni o'chiramiz va egasiga xabar beramiz."""
        row = db.get_bot(bot_id)
        db.update_bot(bot_id, active=0)
        if row and self.notify:
            try:
                await self.notify(row["owner_id"], f"⚠️ @{row['username']} tokeni bekor qilingan, bot to'xtatildi.\n"
                                                   "Yangi token olib, «📂 Botlarim» orqali almashtiring.")
            except Exception:
                pass
