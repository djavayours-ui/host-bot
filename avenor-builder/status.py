"""Ishga tushish/yangilanish holati: adminlarga «run bo'ldi» xabari, ETA (keyingi qayta ishga tushishgacha)
va ishlagan vaqt. Xabarning o'zi tahrirlanib turadi (yangi xabar yuborilmaydi)."""
import asyncio
import json
import logging
import os
import time

import config
import db
import util
from util import esc

log = logging.getLogger("builder.status")
KEY = "status_msgs"      # oxirgi seans xabarlari: {admin_id: message_id}
MAX_EVENTS = 8


def describe_trigger(env=None):
    """(sarlavha, [qo'shimcha satrlar]) — bot nima sababdan ishga tushganini tushuntiradi."""
    env = os.environ if env is None else env
    ev = env.get("GITHUB_EVENT_NAME", "")
    sha = (env.get("GITHUB_SHA") or "")[:7]
    ref = env.get("GITHUB_REF_NAME", "")
    lines = []
    if sha:
        lines.append(f"📦 Versiya: <code>{esc(sha)}</code>" + (f" ({esc(ref)})" if ref else ""))
    msg = (env.get("COMMIT_MSG") or "").strip().split("\n")[0][:200]
    if msg and ev == "push":
        lines.append(f"📝 O'zgarish: {esc(msg)}")
    actor = env.get("GITHUB_ACTOR")
    if actor and ev in ("push", "workflow_dispatch"):
        lines.append(f"👤 Kim: {esc(actor)}")
    run, repo, rid = env.get("GITHUB_RUN_NUMBER"), env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID")
    if run:
        url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{rid}" if repo and rid else ""
        lines.append(f"🏃 Run #{esc(run)}" + (f' · <a href="{esc(url)}">GitHub\'da ochish</a>' if url else ""))
    titles = {
        "schedule": "⏰ Rejalashtirilgan avtomatik qayta ishga tushirish",
        "workflow_dispatch": "🖐 Qo'lda ishga tushirildi (Run workflow)",
        "push": "📦 Kod yangilandi — yangi versiya o'rnatildi",
    }
    return titles.get(ev, "💻 Oddiy ishga tushirish" if not ev else f"ℹ️ Hodisa: {esc(ev)}"), lines


def next_delay(rem):
    """Yangilash oralig'i (soniya): tugashga yaqinlashgan sari tez-tez."""
    if rem is None:
        return 300
    if rem > 600:
        return 60
    if rem > 120:
        return 30
    return 10


def bar(frac, width=10):
    n = max(0, min(width, round(frac * width)))
    return "▓" * n + "░" * (width - n)


FINAL = {
    "deadline": ("🔄 <b>Rejalashtirilgan qayta ishga tushirish boshlandi</b>",
                 "Yangi seans bir necha daqiqada ishga tushadi va shu yerga xabar beradi."),
    "signal": ("🔄 <b>Yangilanish: bot to'xtatilmoqda</b>",
               "Yangi versiya ishga tushgach shu yerga xabar keladi."),
    "crash": ("⚠️ <b>Bot kutilmaganda to'xtadi</b>", "Jurnalni (log) tekshiring."),
}


class Status:
    def __init__(self, bot, admin_ids, run_seconds=0, env=None, started=None):
        self.bot, self.admins = bot, sorted(admin_ids)
        self.total = run_seconds or 0
        self.env = env
        self.started = started or time.time()
        self.msgs = {}          # admin_id -> message_id
        self.events = []        # [(vaqt, matn)]
        self.head = ""          # @username va yaratilgan botlar soni

    def event(self, text, now=None):
        self.events.append((now or time.time(), text))
        del self.events[:-MAX_EVENTS]

    def _events_block(self):
        if not self.events:
            return ""
        rows = "\n".join(f"• <code>{util.fmt_dt(t)[-5:]}</code> — {esc(x)}" for t, x in self.events)
        return f"\n\n🗒 <b>Voqealar</b>\n{rows}"

    def render(self, now=None, reason=None):
        now = now or time.time()
        up = now - self.started
        title, lines = describe_trigger(self.env)
        info = "\n".join(lines)
        if reason:
            head, tail = FINAL.get(reason, FINAL["signal"])
            return (f"{head}\n{self.head}\n\n📌 <b>Hodisa:</b> {title}\n⏱ Jami ishladi: <b>{util.fmt_dur(up)}</b>"
                    f"{self._events_block()}\n\n{tail}")
        text = f"✅ <b>Botingiz ishga tushdi — run bo'ldi!</b>\n{self.head}\n\n📌 <b>Hodisa:</b> {title}"
        if info:
            text += "\n" + info
        text += (f"\n\n🕒 Ishga tushdi: {util.fmt_dt(self.started)}\n⏱ Ishlayapti: <b>{util.fmt_dur(up)}</b>")
        if self.total:
            rem = max(0, self.total - up)
            text += (f"\n⏳ Keyingi yangilanishgacha: <b>{util.fmt_dur(rem)}</b>\n"
                     f"{bar(up / self.total)} {min(100, int(up * 100 / self.total))}%\n"
                     f"🔄 Rejalashtirilgan: {util.fmt_dt(self.started + self.total)}")
        else:
            text += "\n♾ Muddatsiz ishlaydi (qayta ishga tushirish rejalashtirilmagan)"
        return text + self._events_block()

    # ------------------------------ yuborish / tahrirlash ------------------------------
    async def _edit(self, uid, mid, text):
        try:
            await self.bot.edit_message_text(text, chat_id=uid, message_id=mid)
            return True
        except Exception as e:
            return "not modified" in str(e).lower()

    async def _mark_stale(self):
        """Oldingi seans xabari yangilanmay qolgan bo'lsa (uzilib qolgan) — belgilab qo'yamiz."""
        try:
            old = json.loads(db.raw_get(KEY, "{}") or "{}")
        except ValueError:
            old = {}
        text = "⏹ <b>Bu seans tugagan</b> — yangi seans ishga tushgan (pastdagi xabarga qarang)."
        await asyncio.gather(*(self._edit(int(u), m, text) for u, m in old.items()), return_exceptions=True)
        db.raw_set(KEY, "{}")

    async def start(self, username, children):
        self.head = f"🤖 @{esc(username)} · 🧩 yaratilgan botlar: {children}"
        self.event("Bot ishga tushdi")
        try:
            await asyncio.wait_for(self._mark_stale(), 8)
        except Exception:
            pass
        for uid in self.admins:
            try:
                m = await self.bot.send_message(uid, self.render())
                self.msgs[uid] = m.message_id
            except Exception as e:  # admin botga /start bosmagan bo'lishi mumkin
                log.info("Holat xabari %s ga yuborilmadi: %s", uid, e)
        db.raw_set(KEY, json.dumps({str(u): m for u, m in self.msgs.items()}))

    async def loop(self):
        while self.msgs:
            rem = (self.total - (time.time() - self.started)) if self.total else None
            delay = next_delay(rem)
            if rem is not None:
                delay = min(delay, max(1, rem))
            await asyncio.sleep(delay)
            text = self.render()
            for uid, mid in list(self.msgs.items()):
                if not await self._edit(uid, mid, text):
                    self.msgs.pop(uid, None)

    async def finish(self, reason):
        self.event({"deadline": "Rejalashtirilgan qayta ishga tushirish", "signal": "To'xtatish signali (yangilanish)",
                    "crash": "Kutilmagan xato"}.get(reason, reason))
        text = self.render(reason=reason)
        try:
            await asyncio.wait_for(asyncio.gather(*(self._edit(u, m, text) for u, m in self.msgs.items()),
                                                  return_exceptions=True), 5)
            db.raw_set(KEY, "{}")
        except Exception:
            pass
