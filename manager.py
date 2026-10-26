"""Yaratilgan botlarni bitta jarayonda ishga tushiradi: har bot uchun o'z polling sikli."""
import asyncio
import logging
from collections import OrderedDict

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramRetryAfter, TelegramUnauthorizedError

import config
import db

log = logging.getLogger("builder.manager")
UPDATES = ["message", "callback_query", "my_chat_member"]
SEEN_MAX = 2000            # har bot uchun eslab qolinadigan oxirgi update_id lar soni


class Manager:
    def __init__(self, dp: Dispatcher, notify=None):
        self.dp = dp
        self.notify = notify                 # async (owner_id, text)
        self.session = AiohttpSession()      # barcha botlar uchun umumiy ulanishlar
        self.bots = {}                       # bot_id -> Bot
        self.tasks = {}                      # bot_id -> asyncio.Task
        self._jobs = set()
        self.seen = {}                       # bot_id -> OrderedDict(update_id)
        self._warned = set()                 # Conflict haqida ogohlantirilgan botlar

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
        self.seen.pop(bot_id, None)
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

    def _dup(self, bot_id, update_id):
        """True — bu update shu jarayonda allaqachon qayta ishlangan (ikki marta javob bermaslik uchun)."""
        s = self.seen.setdefault(bot_id, OrderedDict())
        if update_id in s:
            return True
        s[update_id] = None
        if len(s) > SEEN_MAX:
            s.popitem(last=False)
        return False

    async def _conflict_alert(self, bot_id):
        """Bir bot ikki joyda polling qilsa har bir xabarga ikki marta javob ketadi — egasi va adminlarga aytamiz."""
        if bot_id in self._warned or not self.notify:
            return
        self._warned.add(bot_id)
        row = db.get_bot(bot_id)
        who = f"@{row['username']}" if row else str(bot_id)
        ids = set(config.ADMIN_IDS) | ({row["owner_id"]} if row else set())
        for uid in ids:
            try:
                await self.notify(uid, f"⚠️ {who}: bot boshqa joyda ham ishlayapti (Conflict). Shu sababli javoblar "
                                       "ikki marta ketishi mumkin. Dasturning faqat BITTA nusxasi ishlasin "
                                       "(boshqa server, kompyuter yoki GitHub Actions'dagi nusxani o'chiring).")
            except Exception:
                pass

    async def _feed(self, bot, upd):
        try:
            await self.dp.feed_update(bot, upd)
        except Exception:
            log.exception("update xatosi (bot %s)", bot.id)

    async def _run(self, bot_id, bot):
        offset, backoff, conflicts = None, 1, 0
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
                    if conflicts:
                        conflicts = 0
                        self._warned.discard(bot_id)
                    for u in updates:
                        offset = u.update_id + 1
                        if self._dup(bot_id, u.update_id):
                            continue
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
                    conflicts += 1
                    if conflicts >= 3:
                        await self._conflict_alert(bot_id)
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
