"""Yaratilgan botlar: foydalanuvchi qismi (start, tugmalar, majburiy obuna) va bot ichidagi admin panel (/admin)."""
import asyncio
import csv
import io
import logging
import re
import time
from collections import deque
from types import SimpleNamespace

from aiogram import BaseMiddleware, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import content as ct
import db
import util
from storage import SqliteStorage
from util import esc

log = logging.getLogger("builder.child")
RESERVED = {"start", "admin", "cancel", "balance", "bonus", "ref", "top", "contact", "new", "random"}
MAX_KEYWORDS = 50
BUILTIN = {"balance", "bonus", "ref", "top", "contact", "new", "random"}
CONTACT_WAIT = {}   # (bot_id, user_id) -> vaqt: keyingi xabar murojaat sifatida yuboriladi
CONTACT_TTL = 600


# ============================== umumiy yordamchilar ==============================
def B(text, data=None, url=None, style=None):
    kw = {"style": style} if style in ("primary", "success", "danger") else {}
    if url is not None:
        return InlineKeyboardButton(text=text, url=url, **kw)
    return InlineKeyboardButton(text=text, callback_data=data, **kw)


def M(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def cb_int(data, idx, default=0):
    try:
        return int(data.split(":")[idx])
    except (IndexError, ValueError):
        return default


async def show(obj, text, kb=None):
    """CallbackQuery bo'lsa xabarni tahrirlaydi (iloji bo'lmasa yangisini yuboradi), Message bo'lsa javob beradi."""
    if isinstance(obj, CallbackQuery):
        try:
            await obj.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                await obj.message.answer(text, reply_markup=kb)
        try:
            await obj.answer()
        except TelegramBadRequest:
            pass  # callback allaqachon javoblangan
    else:
        await obj.answer(text, reply_markup=kb)


def ctx_of(cbot, user):
    cu = db.get_cuser(cbot["id"], user.id)
    return util.make_ctx(cbot, user, cu["balance"] if cu else 0, db.count_cusers(cbot["id"]),
                         cu["refs"] if cu else 0)


def style_icon(s):
    return {"primary": "🔵 ", "success": "🟢 ", "danger": "🔴 "}.get(s, "")


# ============================== dispatcher va middleware ==============================
dp = Dispatcher(storage=SqliteStorage())
admin_r = Router()
user_r = Router()


_HITS = {}                      # (bot_id, user_id) -> oxirgi so'rovlar vaqti
FLOOD_N, FLOOD_SEC = 20, 10     # 10 soniyada 20 tadan ko'p so'rov — e'tiborsiz qoldiriladi


def flooding(key, now=None):
    """True — foydalanuvchi juda tez-tez yozmoqda (adminlarga qo'llanmaydi)."""
    now = now or time.monotonic()
    q = _HITS.get(key)
    if q is None:
        if len(_HITS) > 50000:
            _HITS.clear()
        q = _HITS[key] = deque()
    while q and now - q[0] > FLOOD_SEC:
        q.popleft()
    if len(q) >= FLOOD_N:
        return True
    q.append(now)
    return False


class CtxMw(BaseMiddleware):
    """Bot yozuvini topadi, foydalanuvchini ro'yxatga oladi, adminlikni aniqlaydi
    (egasi, asosiy admin yoki egasi qo'shgan admin). Botni bloklash/blokdan chiqarishni kuzatadi."""

    async def __call__(self, handler, event, data):
        bot = data["bot"]
        row = db.get_bot(bot.id)
        if not row or not row["active"]:
            return None
        data["cbot"] = row
        mcm = getattr(event, "my_chat_member", None)
        if mcm is not None:  # foydalanuvchi botni blokladi / blokdan chiqardi
            if mcm.chat.type == "private":
                st = getattr(mcm.new_chat_member.status, "value", mcm.new_chat_member.status)
                if st in ("kicked", "left"):
                    db.set_cactive(row["id"], mcm.from_user.id, False)
                elif st == "member":
                    db.set_cactive(row["id"], mcm.from_user.id, True)
            return None
        user = data.get("event_from_user")
        data["admin"] = False
        data["is_new"] = False
        if user and not user.is_bot:
            data["admin"] = (user.id == row["owner_id"] or user.id in config.ADMIN_IDS
                             or db.is_bot_admin(row["id"], user.id))
            if not data["admin"] and flooding((row["id"], user.id)):
                return None
            cu, data["is_new"] = db.cuser_touch_new(row["id"], user)
            if cu["banned"] and not data["admin"]:
                return None
        return await handler(event, data)


class IsAdmin(Filter):
    async def __call__(self, event, admin: bool = False) -> bool:
        return bool(admin)


PREFIXES = {"a", "x", "ce", "cw", "cp", "ia", "ik", "it", "is", "lay", "il", "ib", "ibt", "ibc", "ibs", "ibv", "ibd",
            "rbl", "rb", "rb+", "rbn", "rbc", "rbs", "rbd", "rbdo", "cml", "cm", "cm+", "cmd", "cmdo", "ch", "ch+",
            "chl", "chu", "chd", "sj", "sc", "sst", "bcgo", "bcx", "kwl", "kw", "kw+", "kwd", "kwdo", "adm", "adm+", "admd"}
KEEP_STATE = {"it", "is", "lay"}


class AdminCb(Filter):
    async def __call__(self, event: CallbackQuery, admin: bool = False) -> bool:
        return bool(admin and event.data and event.data.split(":")[0] in PREFIXES)


dp.update.outer_middleware(CtxMw())
dp.include_router(admin_r)
dp.include_router(user_r)
admin_r.message.filter(F.chat.type == "private")
user_r.message.filter(F.chat.type == "private")


class S(StatesGroup):
    ask = State()


# ============================== FOYDALANUVCHI QISMI ==============================
async def missing_channels(bot, cbot, uid):
    out = []
    for ch in db.channels(cbot["id"]):
        try:
            m = await bot.get_chat_member(ch["chat_id"], uid)
            st = getattr(m.status, "value", m.status)
            if st in ("left", "kicked") or (st == "restricted" and not getattr(m, "is_member", True)):
                out.append(ch)
        except Exception:
            continue  # tekshirib bo'lmasa (bot admin emas) — to'smaymiz
    return out


async def send_gate(bot, cbot, user, chat_id, missing):
    c = db.get_content(cbot["sub_cid"])
    ctx = ctx_of(cbot, user)
    top = []
    for ch in missing:
        text = util.render(cbot["join_text"], dict(ctx, channel=ch["label"] or ch["title"] or "Kanal"), "plain")[:64]
        if ch["url"]:
            top.append([B(text or "📢", url=ch["url"], style=cbot["join_style"])])
    bottom = [[B(util.render(cbot["check_text"], ctx, "plain")[:64] or "✅", "chk", style=cbot["check_style"])]]
    await ct.send_content(bot, chat_id, c, ctx, ct.inline_markup(cbot, c["id"], ctx, top=top, bottom=bottom))


async def passes_gate(bot, cbot, user, chat_id):
    miss = await missing_channels(bot, cbot, user.id)
    if not miss:
        return True
    await send_gate(bot, cbot, user, chat_id, miss)
    return False


async def send_cid(bot, cbot, user, chat_id, cid):
    c = db.get_content(cid)
    if not c or c["bot_id"] != cbot["id"]:
        return
    ctx = ctx_of(cbot, user)
    await ct.send_content(bot, chat_id, c, ctx, ct.inline_markup(cbot, cid, ctx))


async def send_start(bot, cbot, user, chat_id):
    c = db.get_content(cbot["start_cid"])
    ctx = ctx_of(cbot, user)
    rm = ct.reply_markup(cbot["id"])
    im = ct.inline_markup(cbot, c["id"], ctx)
    if im:
        await ct.send_content(bot, chat_id, c, ctx, im)
        if rm:
            await bot.send_message(chat_id, "⌨️", reply_markup=rm)  # inline va reply klaviatura bitta xabarda bo'lmaydi
    else:
        await ct.send_content(bot, chat_id, c, ctx, rm)


async def apply_referral(bot, cbot, user, arg):
    """/start ref_<id> orqali kelgan yangi foydalanuvchi uchun taklif qiluvchiga mukofot beradi."""
    if not arg.startswith("ref_") or not arg[4:].isdigit():
        return
    ref_id = int(arg[4:])
    st, reward = db.set_referrer(cbot["id"], user.id, ref_id)
    if st != "ok":
        return
    sym = f" (+{reward})" if reward else ""
    try:
        await bot.send_message(ref_id, f"🎉 <b>{esc(user.full_name)}</b> sizning havolangiz orqali qo'shildi{sym}")
    except Exception:
        pass


@user_r.message(CommandStart())
async def h_start(m: Message, bot, cbot, admin, command: CommandObject, is_new: bool = False):
    if is_new and command.args:
        await apply_referral(bot, cbot, m.from_user, command.args.strip())
    if is_new and not admin and cbot["welcome_bonus"] > 0:
        db.give_welcome(cbot["id"], m.from_user.id, cbot["welcome_bonus"])
        await m.answer(f"🎁 Xush kelibsiz bonusi: <b>+{cbot['welcome_bonus']}</b>")
    if not admin and not await passes_gate(bot, cbot, m.from_user, m.chat.id):
        return
    if cbot["kind"] == "kino" and command.args and re.fullmatch(r"k\d{1,9}", command.args.strip()):
        import kino  # ulashilgan havola: /start k<kod> — to'g'ridan-to'g'ri kinoni ochadi
        if await kino.deep_link(m, bot, cbot, int(command.args.strip()[1:])):
            return
    await send_start(bot, cbot, m.from_user, m.chat.id)
    if m.from_user.id == cbot["owner_id"]:
        await m.answer("🛠 Botni sozlash paneli: /admin")


def fmt_wait(sec):
    h, mi = sec // 3600, (sec % 3600) // 60
    return f"{h} soat {mi} daqiqa" if h else f"{max(mi, 1)} daqiqa"


async def builtin(m: Message, cbot, name):
    """/balance, /bonus, /ref — har bir botda tayyor ishlaydigan buyruqlar."""
    cbot = db.get_bot(cbot["id"]) or cbot
    uid = m.from_user.id
    if cbot["kind"] == "kino" and name in ("top", "new", "random"):
        import kino
        return await kino.lists(m, cbot, name)
    if name in ("new", "random"):
        return
    if name == "balance":
        cu = db.get_cuser(cbot["id"], uid)
        return await m.answer(f"💰 Balansingiz: <b>{cu['balance'] if cu else 0}</b>")
    if name == "bonus":
        st, v = db.claim_bonus(cbot["id"], uid)
        if st == "ok":
            cu = db.get_cuser(cbot["id"], uid)
            return await m.answer(f"🎁 Kunlik bonus: <b>+{v}</b>\n💰 Balansingiz: <b>{cu['balance']}</b>")
        if st == "wait":
            return await m.answer(f"⏳ Keyingi bonusgacha: <b>{fmt_wait(v)}</b>")
        return await m.answer("ℹ️ Kunlik bonus hozir yoqilmagan.")
    if name == "contact":
        if not cbot["contact_on"]:
            return await m.answer("ℹ️ Murojaat hozir yoqilmagan.")
        CONTACT_WAIT[(cbot["id"], uid)] = time.time()
        return await m.answer("✉️ Xabaringizni yuboring (matn, rasm, fayl...). Egasi javob bersa, shu yerda olasiz. "
                              "10 daqiqa ichida yuboring.")
    if name == "top":
        bal, refs = db.top_balances(cbot["id"], 10), db.top_referrers(cbot["id"], 10)
        if not bal and not refs:
            return await m.answer("🏆 Reyting hali bo'sh.")
        who = lambda r: esc(r["name"] or r["user_id"])
        parts = ["🏆 <b>Reyting</b>"]
        if bal:
            parts.append("\n💰 <b>Balans bo'yicha</b>\n" + "\n".join(f"{i}. {who(r)} — {r['balance']}" for i, r in enumerate(bal, 1)))
        if refs:
            parts.append("\n👥 <b>Takliflar bo'yicha</b>\n" + "\n".join(f"{i}. {who(r)} — {r['refs']}" for i, r in enumerate(refs, 1)))
        return await m.answer("\n".join(parts))
    if name == "ref":
        cu = db.get_cuser(cbot["id"], uid)
        link = f"https://t.me/{cbot['username']}?start=ref_{uid}"
        reward = f"\nHar bir taklif uchun: <b>+{cbot['ref_reward']}</b>" if cbot["ref_reward"] else ""
        return await m.answer(f"👥 <b>Do'stlarni taklif qiling</b>\nSizning havolangiz:\n<code>{link}</code>\n"
                              f"Taklif qilinganlar: <b>{cu['refs'] if cu else 0}</b>{reward}")


class ContactWait(Filter):
    async def __call__(self, m: Message, cbot, admin: bool = False) -> bool:
        if admin or not cbot["contact_on"]:
            return False
        key = (cbot["id"], m.from_user.id)
        t = CONTACT_WAIT.get(key)
        if not t or time.time() - t > CONTACT_TTL:
            CONTACT_WAIT.pop(key, None)
            return False
        return not (m.text or "").startswith("/")


class ContactReply(Filter):
    async def __call__(self, m: Message, cbot, admin: bool = False) -> bool:
        r = m.reply_to_message
        return bool(admin and r and db.contact_map_get(cbot["id"], m.chat.id, r.message_id))


@user_r.message(ContactWait())
async def h_contact_msg(m: Message, bot, cbot):
    """Foydalanuvchi murojaati egasiga va bot adminlariga yetkaziladi."""
    CONTACT_WAIT.pop((cbot["id"], m.from_user.id), None)
    u = m.from_user
    head = (f"✉️ <b>Murojaat</b> · {esc(u.full_name)}" + (f" (@{esc(u.username)})" if u.username else "")
            + f" · <code>{u.id}</code>\n↩️ Javob berish uchun shu xabarga (yoki pastdagi nusxasiga) reply qiling.")
    sent = 0
    for rid in {cbot["owner_id"], *db.bot_admins(cbot["id"])}:
        try:
            h = await bot.send_message(rid, head)
            cp = await bot.copy_message(rid, m.chat.id, m.message_id, reply_to_message_id=h.message_id)
            db.contact_map_add(cbot["id"], rid, h.message_id, u.id)
            db.contact_map_add(cbot["id"], rid, cp.message_id, u.id)
            sent += 1
        except Exception:
            pass
    await m.answer("✅ Xabaringiz yuborildi. Javobni shu yerda olasiz." if sent
                   else "❌ Hozir yuborib bo'lmadi, keyinroq urinib ko'ring.")


@user_r.message(ContactReply())
async def h_contact_reply(m: Message, bot, cbot):
    uid = db.contact_map_get(cbot["id"], m.chat.id, m.reply_to_message.message_id)
    try:
        await bot.copy_message(uid, m.chat.id, m.message_id)
        await m.answer("✅ Javob yuborildi.")
    except Exception:
        await m.answer("❌ Foydalanuvchiga yuborib bo'lmadi (botni bloklagan bo'lishi mumkin).")


@user_r.message(F.text)
async def h_text(m: Message, bot, cbot, admin):
    t = m.text.strip()
    if t.startswith("/"):
        name = t[1:].split()[0].split("@")[0].lower() if len(t) > 1 else ""
        row = db.command_by_name(cbot["id"], name)
        if not row and name in BUILTIN:  # o'z buyrug'i bo'lmasa — tayyor buyruq
            if not admin and not await passes_gate(bot, cbot, m.from_user, m.chat.id):
                return
            return await builtin(m, cbot, name)
    else:
        row = db.rbutton_by_text(cbot["id"], m.text) or db.keyword_by_word(cbot["id"], t)
    if not row:
        if cbot["kind"] == "kino" and not t.startswith("/"):  # Kino shablon: oddiy matn — qidiruv
            if not admin and not await passes_gate(bot, cbot, m.from_user, m.chat.id):
                return
            import kino
            return await kino.search(m, bot, cbot, t)
        return
    if not admin and not await passes_gate(bot, cbot, m.from_user, m.chat.id):
        return
    await send_cid(bot, cbot, m.from_user, m.chat.id, row["content_id"])


async def run_target(bot, cbot, user, chat_id, value):
    if value == "start":
        return await send_start(bot, cbot, user, chat_id)
    kind, _, num = value[:1], "", value[1:]
    if not num.isdigit():
        return
    row = db.get_command(int(num)) if kind == "c" else db.get_rbutton(int(num)) if kind == "r" else None
    if row and row["bot_id"] == cbot["id"]:
        await send_cid(bot, cbot, user, chat_id, row["content_id"])


@user_r.callback_query(F.data.startswith("i:"))
async def h_ibtn(c: CallbackQuery, bot, cbot, admin):
    b = db.get_ibutton(cb_int(c.data, 1))
    cont = db.get_content(b["content_id"]) if b else None
    if not b or not cont or cont["bot_id"] != cbot["id"]:
        return await c.answer("Tugma topilmadi", show_alert=True)
    if not admin:
        miss = await missing_channels(bot, cbot, c.from_user.id)
        if miss:
            await c.answer("❗️ Avval kanallarga obuna bo'ling", show_alert=True)
            return await send_gate(bot, cbot, c.from_user, c.message.chat.id, miss)
    if not admin:
        db.ibtn_click(b["id"])
    if b["kind"] == "popup":
        return await c.answer(util.render(b["value"], ctx_of(cbot, c.from_user), "plain")[:200], show_alert=True)
    await c.answer()
    if b["kind"] == "command":
        await run_target(bot, cbot, c.from_user, c.message.chat.id, b["value"])


@user_r.callback_query(F.data == "chk")
async def h_chk(c: CallbackQuery, bot, cbot):
    if await missing_channels(bot, cbot, c.from_user.id):
        return await c.answer("❗️ Hali hamma kanallarga obuna bo'lmagansiz", show_alert=True)
    await c.answer("✅")
    try:
        await c.message.delete()
    except Exception:
        pass
    await send_start(bot, cbot, c.from_user, c.message.chat.id)


# ============================== ADMIN PANEL: ekranlar ==============================
def lim_line(cbot, item):
    it = db.ITEMS[item]
    return f"{it['title']}: <b>{db.limit(cbot, item)}</b> (maks. {db.get_s(it['cap'])})"


def scr_home(cbot):
    cbot = db.get_bot(cbot["id"])
    text = (f"🛠 <b>{esc(cbot['name'])}</b> — boshqaruv paneli\n"
            f"👥 Foydalanuvchilar: {db.count_cusers(cbot['id'])}\n\n"
            "Pastdagi bo'limlardan birini tanlang.")
    kino_row = [[B("🎬 Kino bazasi", "k:home")]] if cbot["kind"] == "kino" else []
    return text, M(
        *kino_row,
        [B("📝 Start xabari", f"ce:{cbot['start_cid']}"), B("⌨️ Pastki tugmalar", "rbl")],
        [B("💬 Buyruqlar", "cml"), B("📢 Majburiy obuna", "a:sub")],
        [B("📣 Xabar yuborish", "a:bc"), B("📊 Statistika", "a:stat")],
        [B("🔑 Kalit so'zlar", "kwl"), B("🎁 Referal va bonus", "a:eco")],
        [B("💰 Balans", "a:bal"), B("🚫 Ban / Unban", "a:ban")],
        [B("📥 Foydalanuvchilar (CSV)", "a:exp"), B("👮 Adminlar", "a:adm")],
        [B("🔤 O'zgaruvchilar", "a:vars"), B("🛒 Do'kon / limitlar", "a:shop")],
    )


def bar(n, top, width=10):
    return "▇" * (max(1, round(n / top * width)) if n and top else 0)


def scr_stat(cbot):
    s = db.cstats(cbot["id"])
    series = db.join_series(cbot["id"], 7)
    top = max([n for _d, n in series] or [0])
    chart = "\n".join(f"<code>{d}</code> {bar(n, top)} {n}" for d, n in series)
    refs = db.top_referrers(cbot["id"], 5)
    ref_txt = ""
    if refs:
        ref_txt = "\n\n🏆 <b>Eng ko'p taklif qilganlar</b>\n" + "\n".join(
            f"{i}. {esc(r['name'] or r['user_id'])} — {r['refs']}" for i, r in enumerate(refs, 1))
    tb = db.top_buttons(cbot["id"], 5)
    if tb:
        ref_txt += "\n\n🖱 <b>Eng ko'p bosilgan tugmalar</b>\n" + "\n".join(
            f"{i}. {esc(r['text'])} — {r['clicks']}" for i, r in enumerate(tb, 1))
    if cbot["kind"] == "kino":
        import kino
        ref_txt += "\n" + kino.stat_line(cbot)
    return (f"📊 <b>Statistika</b>\n👥 Jami: <b>{s['total']}</b>\n✅ Faol (bloklamagan): {s['active']}\n"
            f"🆕 Bugun: {s['today']} · 7 kun: {s['week']}\n🕒 24 soatda faol: {s['online']}\n🚫 Ban: {s['banned']}\n\n"
            f"📈 <b>Oxirgi 7 kun (yangi foydalanuvchilar)</b>\n{chart}{ref_txt}",
            M([B("⬅️ Orqaga", "a:home")]))


def scr_eco(cbot):
    cbot = db.get_bot(cbot["id"])
    off = " (o'chirilgan)"
    return (f"🎁 <b>Referal va kunlik bonus</b>\n\n"
            f"👥 Taklif uchun mukofot: <b>{cbot['ref_reward']}</b>{off if not cbot['ref_reward'] else ''}\n"
            f"🎁 Kunlik bonus: <b>{cbot['daily_bonus']}</b>{off if not cbot['daily_bonus'] else ''}\n"
            f"👋 Xush kelibsiz bonusi: <b>{cbot['welcome_bonus']}</b>{off if not cbot['welcome_bonus'] else ''}\n"
            f"✉️ Murojaat (/contact): <b>{'yoqilgan' if cbot['contact_on'] else 'o\'chiq'}</b>\n\n"
            "Foydalanuvchilar uchun tayyor buyruqlar: <code>/ref</code> — taklif havolasi, <code>/bonus</code> — kunlik bonus, "
            "<code>/balance</code> — balans. Xabar matnida <code>%ref_link%</code> va <code>%referrals%</code> ishlaydi.\n"
            "Mukofot 0 bo'lsa — o'chirilgan (referal hisoblanadi, lekin balans berilmaydi).",
            M([B("✏️ Taklif mukofoti", "a:refr"), B("✏️ Kunlik bonus", "a:dbon")],
              [B("✏️ Xush kelibsiz bonusi", "a:wbon"),
               B("✉️ Murojaat: " + ("o'chirish" if cbot["contact_on"] else "yoqish"), "a:ctc")],
              [B("⬅️ Orqaga", "a:home")]))


def is_owner_level(cbot, uid):
    return uid == cbot["owner_id"] or uid in config.ADMIN_IDS


def scr_adm(cbot):
    ids = db.bot_admins(cbot["id"])
    rows = [[B(f"🗑 {uid}", f"adm:{uid}")] for uid in ids]
    rows += [[B("➕ Admin qo'shish", "adm+")], [B("⬅️ Orqaga", "a:home")]]
    return (f"👮 <b>Adminlar</b> ({len(ids)})\nQo'shilgan adminlar shu botni sozlay oladi (/admin), "
            "lekin boshqa adminlarni boshqara olmaydi.\nO'chirish uchun ID ustiga bosing.", M(*rows))


def scr_kwl(cbot):
    kws = db.keywords(cbot["id"])
    rows = [[B(f"🔑 {k['word']}"[:40], f"kw:{k['id']}")] for k in kws]
    rows += [[B("➕ Kalit so'z qo'shish", "kw+")], [B("⬅️ Orqaga", "a:home")]]
    return (f"🔑 <b>Kalit so'zlar</b> ({len(kws)}/{MAX_KEYWORDS})\nFoydalanuvchi aynan shu matnni yozsa "
            "(katta-kichik harf farqsiz), bot tayyor xabar bilan javob beradi. Har biriga xabar va inline tugmalar "
            "biriktiriladi."), M(*rows)


def scr_kw(kid):
    k = db.get_keyword(kid)
    return (f"🔑 <b>{esc(k['word'])}</b>\n🔘 Inline tugmalar: {db.count_ibuttons(k['content_id'])}"), M(
        [B("📝 Xabar va inline tugmalar", f"ce:{k['content_id']}")],
        [B("🗑 O'chirish", f"kwd:{kid}")],
        [B("⬅️ Orqaga", "kwl")])


def scr_shop(cbot):
    cbot = db.get_bot(cbot["id"])
    text = ("🛒 <b>Limitlar</b>\n" + "\n".join(lim_line(cbot, k) for k in db.ITEMS)
            + "\n\nQo'shimcha limitlarni asosiy botdagi «Do'kon» bo'limidan sotib olasiz.")
    rows = []
    if config.MASTER_USERNAME:
        rows.append([B("🛒 Do'konni ochish", url=f"https://t.me/{config.MASTER_USERNAME}?start=shop_{cbot['id']}")])
    rows.append([B("⬅️ Orqaga", "a:home")])
    return text, M(*rows)


def content_owner_back(cbot, cid):
    """Kontent tahrir ekranidan qayerga qaytish kerak."""
    if cbot["start_cid"] == cid:
        return "a:home"
    if cbot["sub_cid"] == cid:
        return "a:sub"
    with db.conn() as c:
        r = c.execute("SELECT id FROM rbuttons WHERE content_id=?", (cid,)).fetchone()
        if r:
            return f"rb:{r[0]}"
        r = c.execute("SELECT id FROM commands WHERE content_id=?", (cid,)).fetchone()
        if r:
            return f"cm:{r[0]}"
        r = c.execute("SELECT id FROM keywords WHERE content_id=?", (cid,)).fetchone()
        if r:
            return f"kw:{r[0]}"
    return "a:home"


def scr_ce(cbot, cid):
    cbot = db.get_bot(cbot["id"])
    c = db.get_content(cid)
    n, lim = db.count_ibuttons(cid), db.limit(cbot, "inline")
    text = (f"📝 <b>Xabar</b>\nTuri: {ct.TYPE_NAMES.get(c['type'], c['type'])}\n"
            f"🔘 Inline tugmalar: <b>{n}/{lim}</b>\n\n"
            "Matnda o'zgaruvchilar ishlaydi (masalan <code>%name%</code>, <code>%balance%</code>).")
    return text, M(
        [B("✏️ Xabarni almashtirish", f"cw:{cid}"), B("👁 Ko'rish", f"cp:{cid}")],
        [B("➕ Inline tugma", f"ia:{cid}"), B(f"🔘 Tugmalar ({n})", f"il:{cid}")],
        [B("⬅️ Orqaga", content_owner_back(cbot, cid))])


def scr_il(cbot, cid):
    rows = [[B(f"{style_icon(b['style'])}{b['text'][:30]} · {ct.KINDS[b['kind']][0].split(' ', 1)[0]}", f"ib:{b['id']}")]
            for b in db.ibuttons(cid)]
    rows.append([B("➕ Qo'shish", f"ia:{cid}")])
    rows.append([B("⬅️ Orqaga", f"ce:{cid}")])
    return f"🔘 <b>Inline tugmalar</b> ({db.count_ibuttons(cid)})\nTahrirlash uchun tugmani tanlang:", M(*rows)


def scr_ib(iid):
    b = db.get_ibutton(iid)
    kname = ct.KINDS[b["kind"]][0]
    val = b["value"]
    if b["kind"] == "command":
        val = "/start" if val == "start" else ("buyruq/tugma #" + val[1:])
    text = (f"🔘 <b>{esc(b['text'])}</b>\nTuri: {kname}\nQiymat: <code>{esc(val or '—')}</code>\n"
            f"Rang: {ct.STYLES.get(b['style'], '—')}")
    return text, M(
        [B("✏️ Matn", f"ibt:{iid}"), B("🎨 Rang", f"ibc:{iid}")],
        [B("🔗 Qiymat", f"ibv:{iid}"), B("🗑 O'chirish", f"ibd:{iid}")],
        [B("⬅️ Orqaga", f"il:{b['content_id']}")])


def scr_rbl(cbot):
    cbot = db.get_bot(cbot["id"])
    rbs = db.rbuttons(cbot["id"])
    rows = [[B(f"{style_icon(r['style'])}{r['text']}", f"rb:{r['id']}")] for r in rbs]
    rows.append([B("➕ Pastki tugma qo'shish", "rb+")])
    rows.append([B("⬅️ Orqaga", "a:home")])
    return (f"⌨️ <b>Pastki tugmalar</b> ({len(rbs)}/{db.limit(cbot, 'reply')})\n"
            "Har bir tugmaga xabar (har qanday turda) va inline tugmalar biriktiriladi.\n"
            "ℹ️ Foydalanuvchilarda yangi klaviatura /start bosganda yangilanadi."), M(*rows)


def scr_rb(rid):
    r = db.get_rbutton(rid)
    return (f"⌨️ <b>{esc(r['text'])}</b>\nRang: {ct.STYLES.get(r['style'], '—')}\n"
            f"🔘 Inline tugmalar: {db.count_ibuttons(r['content_id'])}"), M(
        [B("📝 Xabar va inline tugmalar", f"ce:{r['content_id']}")],
        [B("✏️ Nomi", f"rbn:{rid}"), B("🎨 Rang", f"rbc:{rid}")],
        [B("🗑 O'chirish", f"rbd:{rid}")],
        [B("⬅️ Orqaga", "rbl")])


def scr_cml(cbot):
    cmds = db.commands(cbot["id"])
    rows = [[B(f"/{c['name']}", f"cm:{c['id']}")] for c in cmds]
    rows.append([B("➕ Buyruq qo'shish", "cm+")])
    rows.append([B("⬅️ Orqaga", "a:home")])
    return ("💬 <b>Buyruqlar</b>\nOxiri /nom ko'rinishidagi buyruqlar. Har biriga xabar va inline tugmalar biriktiriladi; "
            "inline «⚡️ Buyruq» tugmasi ham ularni ochadi."), M(*rows)


def scr_cm(cid):
    c = db.get_command(cid)
    return (f"💬 <b>/{esc(c['name'])}</b>\n🔘 Inline tugmalar: {db.count_ibuttons(c['content_id'])}"), M(
        [B("📝 Xabar va inline tugmalar", f"ce:{c['content_id']}")],
        [B("🗑 O'chirish", f"cmd:{cid}")],
        [B("⬅️ Orqaga", "cml")])


def scr_sub(cbot):
    cbot = db.get_bot(cbot["id"])
    chs = db.channels(cbot["id"])
    rows = [[B(f"📢 {(ch['label'] or ch['title'] or ch['chat_id'])}"[:40], f"ch:{ch['id']}")] for ch in chs]
    rows += [[B("➕ Kanal qo'shish", "ch+")],
             [B("📝 Obuna xabari", f"ce:{cbot['sub_cid']}")],
             [B("✏️ Kanal tugmasi matni", "sj"), B("🎨 Rangi", "sst:j")],
             [B("✏️ Tekshirish tugmasi matni", "sc"), B("🎨 Rangi", "sst:c")],
             [B("⬅️ Orqaga", "a:home")]]
    return (f"📢 <b>Majburiy obuna</b> ({len(chs)}/{db.limit(cbot, 'channel')} kanal)\n"
            f"Kanal tugmasi: <code>{esc(cbot['join_text'])}</code>\nTekshirish tugmasi: <code>{esc(cbot['check_text'])}</code>\n"
            "ℹ️ Bot kanalda <b>administrator</b> bo'lishi kerak."), M(*rows)


def scr_ch(chid):
    ch = db.get_channel(chid)
    return (f"📢 <b>{esc(ch['title'] or ch['chat_id'])}</b>\nTugma nomi: {esc(ch['label'] or '(kanal nomi)')}\n"
            f"URL: {esc(ch['url'] or '—')}"), M(
        [B("✏️ Tugma nomi", f"chl:{chid}"), B("🔗 URL", f"chu:{chid}")],
        [B("🗑 O'chirish", f"chd:{chid}")],
        [B("⬅️ Orqaga", "a:sub")])


def style_picker(prefix, back):
    rows = [[B(label, f"{prefix}:{k or 'n'}")] for k, label in ct.STYLES.items()]
    rows.append([B("⬅️ Orqaga", back)])
    return M(*rows)


def sty(code):
    return "" if code == "n" else code


# ============================== ADMIN PANEL: so'rov/javob mexanizmi ==============================
ANSWERS = {}


def answer(act):
    def deco(fn):
        ANSWERS[act] = fn
        return fn
    return deco


async def _drop(m):
    try:
        await m.delete()
    except Exception:
        pass


async def _edit_or_send(m, pid, pchat, text, kb, keep=False):
    """Panel xabarini (pid) tahrirlaydi; iloji bo'lmasa yangisini yuboradi. (pid, chat_id) qaytaradi.
    Foydalanuvchining javob xabari chat toza qolishi uchun o'chiriladi (keep=True bo'lmasa)."""
    if pid and pchat == m.chat.id:
        try:
            await m.bot.edit_message_text(text, chat_id=pchat, message_id=pid, reply_markup=kb)
            ok = True
        except TelegramBadRequest as e:
            ok = "not modified" in str(e).lower()
        if ok:
            if not keep:
                await _drop(m)
            return pid, pchat
    sent = await m.answer(text, reply_markup=kb)
    return sent.message_id, m.chat.id


async def open_prompt(obj, old, prompt, cancel_cb):
    """So'rov matnini yangi xabar emas, joriy panel xabarida ko'rsatadi. (pid, chat_id) qaytaradi."""
    kb = M([B("❌ Bekor qilish", cancel_cb)])
    if isinstance(obj, CallbackQuery):
        msg = obj.message
        pid, pchat = msg.message_id, msg.chat.id
        try:
            await msg.edit_text(prompt, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                pid = (await msg.answer(prompt, reply_markup=kb)).message_id
        try:
            await obj.answer()
        except TelegramBadRequest:
            pass
        return pid, pchat
    return await _edit_or_send(obj, old.get("pid"), old.get("pchat"), prompt, kb)


async def say(m, d, text, reply_markup=None, keep=False):
    """Javobni alohida xabar sifatida emas, panel xabarini tahrirlab ko'rsatadi.
    Xato matni (❌/ℹ️/⚠️, tugmasiz) bo'lsa — so'rov matni qayta ko'rsatiladi."""
    if reply_markup is None and d.get("prompt") and text.lstrip().startswith(("❌", "ℹ️", "⚠️")):
        text = f"{text}\n\n{d['prompt']}"
        reply_markup = M([B("❌ Bekor qilish", d.get("cancel") or "x:a:home")])
    await _edit_or_send(m, d.get("pid"), d.get("pchat"), text, reply_markup, keep)


async def ask(obj, state, act, prompt, back, **data):
    old = await state.get_data()
    await state.clear()
    await state.set_state(S.ask)
    pid, pchat = await open_prompt(obj, old, prompt, "x:" + back)
    await state.update_data(act=act, back=back, cancel="x:" + back, prompt=prompt, pid=pid, pchat=pchat, **data)


@admin_r.message(IsAdmin(), Command("admin"))
async def h_admin(m: Message, state: FSMContext, cbot):
    await state.clear()
    text, kb = scr_home(cbot)
    await m.answer(text, reply_markup=kb)


@admin_r.message(IsAdmin(), Command("cancel"))
async def h_cancel(m: Message, state: FSMContext, cbot):
    await state.clear()
    text, kb = scr_home(cbot)
    await m.answer("Bekor qilindi.\n\n" + text, reply_markup=kb)


@admin_r.message(IsAdmin(), S.ask)
async def h_answer(m: Message, state: FSMContext, bot, cbot):
    d = await state.get_data()
    fn = ANSWERS.get(d.get("act"))
    if not fn:
        await state.clear()
        return
    await fn(m, state, d, bot, cbot)


def X(c, state, bot, cbot):
    return SimpleNamespace(c=c, state=state, bot=bot, cbot=cbot, p=c.data.split(":"), user=c.from_user)


@admin_r.callback_query(AdminCb())
async def h_admin_cb(c: CallbackQuery, state: FSMContext, bot, cbot):
    await route(c.data, c, state, bot, cbot)


async def route(data, c, state, bot, cbot):
    cbot = db.get_bot(cbot["id"]) or cbot
    p = data.split(":")
    fn = ROUTES.get(p[0])
    if not fn:
        return await c.answer()
    if p[0] not in KEEP_STATE:
        await state.clear()
    x = SimpleNamespace(c=c, state=state, bot=bot, cbot=cbot, p=p, user=c.from_user, data=data)
    try:
        await fn(x)
    except TelegramBadRequest as e:
        log.warning("admin route %s: %s", data, e)
        try:
            await c.answer("⚠️ Xatolik: " + str(e)[:150], show_alert=True)
        except TelegramBadRequest:
            pass


def own_content(x, cid):
    c = db.get_content(cid)
    return c if c and c["bot_id"] == x.cbot["id"] else None


# ============================== ADMIN PANEL: yo'nalishlar ==============================
async def r_x(x):  # bekor qilish: oldingi ekranga qaytish
    await route(":".join(x.p[1:]) or "a:home", x.c, x.state, x.bot, x.cbot)


async def r_a(x):
    sub = x.p[1] if len(x.p) > 1 else "home"
    if sub == "home":
        return await show(x.c, *scr_home(x.cbot))
    if sub == "stat":
        return await show(x.c, *scr_stat(x.cbot))
    if sub == "shop":
        return await show(x.c, *scr_shop(x.cbot))
    if sub == "vars":
        return await show(x.c, "🔤 <b>O'zgaruvchilar</b>\nXabar matnida va inline tugma matnida ishlaydi:\n\n" + util.VARS_HELP,
                          M([B("⬅️ Orqaga", "a:home")]))
    if sub == "sub":
        return await show(x.c, *scr_sub(x.cbot))
    if sub == "eco":
        return await show(x.c, *scr_eco(x.cbot))
    if sub == "refr":
        return await ask(x.c, x.state, "eco_ref", "👥 Har bir taklif qilingan do'st uchun mukofotni yuboring "
                                                  "(0 — mukofotsiz, maks. 1 000 000):", "a:eco")
    if sub == "dbon":
        return await ask(x.c, x.state, "eco_bonus", "🎁 Kunlik bonus miqdorini yuboring "
                                                    "(0 — o'chirish, maks. 1 000 000):", "a:eco")
    if sub == "wbon":
        return await ask(x.c, x.state, "eco_welcome", "👋 Yangi foydalanuvchiga beriladigan bonusni yuboring "
                                                      "(0 — o'chirish, maks. 1 000 000):", "a:eco")
    if sub == "ctc":
        db.update_bot(x.cbot["id"], contact_on=0 if x.cbot["contact_on"] else 1)
        return await show(x.c, *scr_eco(x.cbot))
    if sub == "adm":
        if not is_owner_level(x.cbot, x.user.id):
            return await x.c.answer("Adminlarni faqat bot egasi boshqaradi", show_alert=True)
        return await show(x.c, *scr_adm(x.cbot))
    if sub == "exp":
        return await export_users(x)
    if sub == "bc":
        if x.cbot["id"] in BCAST:
            return await x.c.answer("Xabar yuborish davom etmoqda", show_alert=True)
        return await ask(x.c, x.state, "bc_msg", "📣 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring "
                                                 "(har qanday turda: matn, rasm, video, fayl...).", "a:home")
    if sub == "bal":
        return await ask(x.c, x.state, "bal_id", "💰 Foydalanuvchi ID raqamini yuboring (u shu botga /start bosgan bo'lsin):",
                         "a:home")
    if sub == "ban":
        return await ask(x.c, x.state, "ban_id", "🚫 Ban/Unban qilinadigan foydalanuvchi ID raqamini yuboring:", "a:home")
    await x.c.answer()


# ---- kontent muharriri ----
async def r_ce(x):
    cid = cb_int(x.c.data, 1)
    if not own_content(x, cid):
        return await x.c.answer("Topilmadi", show_alert=True)
    await show(x.c, *scr_ce(x.cbot, cid))


async def r_cw(x):
    cid = cb_int(x.c.data, 1)
    if not own_content(x, cid):
        return await x.c.answer("Topilmadi", show_alert=True)
    await ask(x.c, x.state, "cw", "✏️ Yangi xabarni yuboring — har qanday turda (matn, rasm, video, GIF, fayl, audio, "
                                  "ovozli, stiker...). Shrift/format (qalin, kursiv, spoyler, mono...) saqlanadi.\n"
                                  "O'zgaruvchilar: <code>%name%</code>, <code>%balance%</code>, <code>%time%</code>...",
              f"ce:{cid}", cid=cid)


@answer("cw")
async def a_cw(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await say(m, d, "❌ Bu turdagi xabar qo'llab-quvvatlanmaydi. Matn, rasm, video, fayl, audio, GIF, stiker yuboring.")
    if not own_content_id(cbot, d["cid"]):
        return await state.clear()
    db.replace_content(d["cid"], new)
    await state.clear()
    text, kb = scr_ce(cbot, d["cid"])
    await say(m, d, "✅ Xabar yangilandi.\n\n" + text, reply_markup=kb)


def own_content_id(cbot, cid):
    c = db.get_content(cid)
    return c and c["bot_id"] == cbot["id"]


async def r_cp(x):
    cid = cb_int(x.c.data, 1)
    c = own_content(x, cid)
    if not c:
        return await x.c.answer("Topilmadi", show_alert=True)
    await x.c.answer()
    ctx = ctx_of(x.cbot, x.user)
    try:
        await ct.send_content(x.bot, x.c.message.chat.id, c, ctx, ct.inline_markup(x.cbot, cid, ctx))
    except TelegramBadRequest as e:
        await x.c.message.answer(f"⚠️ Xabar yuborilmadi: {esc(e)}")


# ---- inline tugma qo'shish ----
async def r_ia(x):
    cid = cb_int(x.c.data, 1)
    if not own_content(x, cid):
        return await x.c.answer("Topilmadi", show_alert=True)
    n, lim = db.count_ibuttons(cid), db.limit(x.cbot, "inline")
    if n >= lim:
        text, kb = scr_shop(x.cbot)
        return await show(x.c, f"⚠️ Inline tugmalar limiti to'ldi ({n}/{lim}). Ko'proq kerak bo'lsa, do'kondan sotib oling.\n\n" + text, kb)
    rows = [[B(v[0], f"ik:{cid}:{k}")] for k, v in ct.KINDS.items()]
    rows.append([B("⬅️ Orqaga", f"ce:{cid}")])
    await show(x.c, f"➕ <b>Inline tugma turi</b> ({n}/{lim}):", M(*rows))


def target_picker(cbot, back):
    rows = [[B("▶️ /start (bosh xabar)", "it:start")]]
    rows += [[B(f"💬 /{c['name']}", f"it:c{c['id']}")] for c in db.commands(cbot["id"])]
    rows += [[B(f"⌨️ {r['text']}", f"it:r{r['id']}")] for r in db.rbuttons(cbot["id"])]
    rows.append([B("⬅️ Orqaga", back)])
    return M(*rows)


async def r_ik(x):
    cid, kind = cb_int(x.c.data, 1), x.p[2] if len(x.p) > 2 else ""
    if not own_content(x, cid) or kind not in ct.KINDS:
        return await x.c.answer("Topilmadi", show_alert=True)
    if kind == "command":
        await x.state.set_data({"flow": "ib", "cid": cid, "kind": "command"})
        return await show(x.c, "⚡️ Tugma bosilganda qaysi xabar ochilsin?", target_picker(x.cbot, f"ia:{cid}"))
    await ask(x.c, x.state, "ib_value", ct.KINDS[kind][1], f"ia:{cid}", flow="ib", cid=cid, kind=kind)


def check_value(kind, text):
    """(qiymat, xato)"""
    t = (text or "").strip()
    if kind == "url":
        return (t, None) if re.fullmatch(r"(https?://|tg://)\S{3,500}", t) else (None, "URL <code>https://</code> yoki <code>tg://</code> bilan boshlansin.")
    if kind == "webapp":
        return (t, None) if re.fullmatch(r"https://\S{3,500}", t) else (None, "Web App manzili <code>https://</code> bilan boshlansin.")
    if kind == "copy":
        return (t, None) if 1 <= len(t) <= 256 else (None, "Matn 1–256 belgi bo'lsin.")
    if kind == "popup":
        return (t, None) if 1 <= len(t) <= 200 else (None, "Matn 1–200 belgi bo'lsin.")
    if kind in ("forward", "inline", "inline_here"):
        t = "" if t == "-" else t
        return (t, None) if len(t) <= 256 else (None, "Maksimal 256 belgi.")
    return None, "Noma'lum tur."


@answer("ib_value")
async def a_ib_value(m, state, d, bot, cbot):
    val, err = check_value(d["kind"], m.text if m.text else "")
    if err:
        return await say(m, d, "❌ " + err)
    await ask(m, state, "ib_text", "✏️ Tugma matnini yuboring (maks. 64 belgi). O'zgaruvchilar mumkin: <code>%name%</code>, "
                                    "<code>%balance%</code>...", f"ia:{d['cid']}", flow="ib", cid=d["cid"], kind=d["kind"], value=val)


@answer("ib_text")
async def a_ib_text(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin.")
    await state.set_state(None)
    await state.update_data(text=t, act=None)
    await say(m, d, "🎨 Tugma rangini tanlang:", reply_markup=style_picker("is", f"ia:{d['cid']}"))


async def r_it(x):  # «Buyruq» tugmasi uchun nishon tanlandi
    d = await x.state.get_data()
    val = x.p[1] if len(x.p) > 1 else ""
    if d.get("flow") == "ib_edit":
        db.update_ibutton(d["iid"], value=val)
        await x.state.clear()
        return await show(x.c, *scr_ib(d["iid"]))
    if d.get("flow") != "ib":
        return await x.c.answer("Seans tugagan", show_alert=True)
    await x.state.update_data(value=val)
    await ask(x.c, x.state, "ib_text", "✏️ Tugma matnini yuboring (maks. 64 belgi):", f"ia:{d['cid']}",
              flow="ib", cid=d["cid"], kind="command", value=val)


async def r_is(x):  # rang tanlandi
    d = await x.state.get_data()
    style = sty(x.p[1] if len(x.p) > 1 else "n")
    flow = d.get("flow")
    if flow not in ("ib", "rb"):
        return await x.c.answer("Seans tugagan", show_alert=True)
    await x.state.update_data(style=style)
    existing = db.count_ibuttons(d["cid"]) if flow == "ib" else len(db.rbuttons(x.cbot["id"]))
    if existing:
        return await show(x.c, "📐 Joylashuv:", M([B("⬇️ Yangi qatorga", "lay:n"), B("↔️ Oxirgi qatorga", "lay:s")]))
    await finish_flow(x, True)


async def r_lay(x):
    await finish_flow(x, (x.p[1] if len(x.p) > 1 else "n") == "n")


async def finish_flow(x, new_row):
    d = await x.state.get_data()
    flow, cbot = d.get("flow"), x.cbot
    if flow == "ib":
        cid = d["cid"]
        if not own_content(x, cid) or db.count_ibuttons(cid) >= db.limit(cbot, "inline"):
            await x.state.clear()
            return await x.c.answer("Limit to'lgan yoki xabar topilmadi", show_alert=True)
        db.add_ibutton(cid, d["text"], d["kind"], d.get("value", ""), d.get("style", ""), new_row)
        await x.state.clear()
        text, kb = scr_ce(cbot, cid)
        return await show(x.c, "✅ Inline tugma qo'shildi.\n\n" + text, kb)
    if flow == "rb":
        if len(db.rbuttons(cbot["id"])) >= db.limit(cbot, "reply") or db.rbutton_by_text(cbot["id"], d["name"]):
            await x.state.clear()
            return await x.c.answer("Limit to'lgan yoki nom band", show_alert=True)
        cid = db.add_content(cbot["id"], d["content"])
        rid = db.add_rbutton(cbot["id"], d["name"], d.get("style", ""), cid, new_row)
        await x.state.clear()
        text, kb = scr_rb(rid)
        return await show(x.c, "✅ Pastki tugma qo'shildi.\n\n" + text, kb)
    await x.c.answer("Seans tugagan", show_alert=True)


async def r_il(x):
    cid = cb_int(x.c.data, 1)
    if not own_content(x, cid):
        return await x.c.answer("Topilmadi", show_alert=True)
    await show(x.c, *scr_il(x.cbot, cid))


def own_ib(x, iid):
    b = db.get_ibutton(iid)
    return b if b and own_content(x, b["content_id"]) else None


async def r_ib(x):
    iid = cb_int(x.c.data, 1)
    if not own_ib(x, iid):
        return await x.c.answer("Topilmadi", show_alert=True)
    await show(x.c, *scr_ib(iid))


async def r_ibt(x):
    iid = cb_int(x.c.data, 1)
    if own_ib(x, iid):
        await ask(x.c, x.state, "ibt", "✏️ Yangi tugma matnini yuboring:", f"ib:{iid}", iid=iid)


@answer("ibt")
async def a_ibt(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin.")
    db.update_ibutton(d["iid"], text=t)
    await state.clear()
    text, kb = scr_ib(d["iid"])
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_ibc(x):
    iid = cb_int(x.c.data, 1)
    if own_ib(x, iid):
        rows = [[B(label, f"ibs:{iid}:{k or 'n'}")] for k, label in ct.STYLES.items()]
        rows.append([B("⬅️ Orqaga", f"ib:{iid}")])
        await show(x.c, "🎨 Rangni tanlang:", M(*rows))


async def r_ibs(x):
    iid = cb_int(x.c.data, 1)
    if own_ib(x, iid):
        db.update_ibutton(iid, style=sty(x.p[2] if len(x.p) > 2 else "n"))
        await show(x.c, *scr_ib(iid))


async def r_ibv(x):
    iid = cb_int(x.c.data, 1)
    b = own_ib(x, iid)
    if not b:
        return
    if b["kind"] == "command":
        await x.state.set_data({"flow": "ib_edit", "iid": iid})
        return await show(x.c, "⚡️ Qaysi xabar ochilsin?", target_picker(x.cbot, f"ib:{iid}"))
    await ask(x.c, x.state, "ibv", ct.KINDS[b["kind"]][1], f"ib:{iid}", iid=iid, kind=b["kind"])


@answer("ibv")
async def a_ibv(m, state, d, bot, cbot):
    val, err = check_value(d["kind"], m.text if m.text else "")
    if err:
        return await say(m, d, "❌ " + err)
    db.update_ibutton(d["iid"], value=val)
    await state.clear()
    text, kb = scr_ib(d["iid"])
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_ibd(x):
    iid = cb_int(x.c.data, 1)
    b = own_ib(x, iid)
    if b:
        db.del_ibutton(iid)
        await x.c.answer("O'chirildi")
        await show(x.c, *scr_il(x.cbot, b["content_id"]))


# ---- pastki tugmalar ----
async def r_rbl(x):
    await show(x.c, *scr_rbl(x.cbot))


def own_rb(x, rid):
    r = db.get_rbutton(rid)
    return r if r and r["bot_id"] == x.cbot["id"] else None


async def r_rb(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        await show(x.c, *scr_rb(rid))


async def r_rb_add(x):
    n, lim = len(db.rbuttons(x.cbot["id"])), db.limit(x.cbot, "reply")
    if n >= lim:
        text, kb = scr_shop(x.cbot)
        return await show(x.c, f"⚠️ Pastki tugmalar limiti to'ldi ({n}/{lim}).\n\n" + text, kb)
    await ask(x.c, x.state, "rb_name", "⌨️ Pastki tugma matnini yuboring (maks. 64 belgi, o'zgaruvchisiz):", "rbl")


@answer("rb_name")
async def a_rb_name(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64 or t.startswith("/"):
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin va «/» bilan boshlanmasin.")
    if db.rbutton_by_text(cbot["id"], t):
        return await say(m, d, "❌ Bunday tugma allaqachon bor. Boshqa nom yuboring.")
    await ask(m, state, "rb_msg", "📝 Endi shu tugma bosilganda yuboriladigan xabarni yuboring (har qanday turda).", "rbl", name=t)


@answer("rb_msg")
async def a_rb_msg(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await say(m, d, "❌ Bu turdagi xabar qo'llab-quvvatlanmaydi.")
    await state.set_state(None)
    await state.update_data(act=None, flow="rb", content=new, cid=0)
    await say(m, d, "🎨 Tugma rangini tanlang:", reply_markup=style_picker("is", "rbl"))


async def r_rbn(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        await ask(x.c, x.state, "rbn", "✏️ Yangi nomni yuboring:", f"rb:{rid}", rid=rid)


@answer("rbn")
async def a_rbn(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    ex = db.rbutton_by_text(cbot["id"], t)
    if not 1 <= len(t) <= 64 or t.startswith("/") or (ex and ex["id"] != d["rid"]):
        return await say(m, d, "❌ Nom 1–64 belgi, «/»siz va takrorlanmas bo'lsin.")
    db.update_rbutton(d["rid"], text=t)
    await state.clear()
    text, kb = scr_rb(d["rid"])
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_rbc(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        rows = [[B(label, f"rbs:{rid}:{k or 'n'}")] for k, label in ct.STYLES.items()]
        rows.append([B("⬅️ Orqaga", f"rb:{rid}")])
        await show(x.c, "🎨 Rangni tanlang:", M(*rows))


async def r_rbs(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        db.update_rbutton(rid, style=sty(x.p[2] if len(x.p) > 2 else "n"))
        await show(x.c, *scr_rb(rid))


async def r_rbd(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        await show(x.c, "🗑 Bu pastki tugma va uning xabari o'chiriladi. Davom etamizmi?",
                   M([B("✅ Ha", f"rbdo:{rid}"), B("⬅️ Yo'q", f"rb:{rid}")]))


async def r_rbdo(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        db.del_rbutton(rid)
        await x.c.answer("O'chirildi")
        await show(x.c, *scr_rbl(x.cbot))


# ---- buyruqlar ----
async def r_cml(x):
    await show(x.c, *scr_cml(x.cbot))


def own_cm(x, cid):
    r = db.get_command(cid)
    return r if r and r["bot_id"] == x.cbot["id"] else None


async def r_cm(x):
    cid = cb_int(x.c.data, 1)
    if own_cm(x, cid):
        await show(x.c, *scr_cm(cid))


async def r_cm_add(x):
    await ask(x.c, x.state, "cm_name", "💬 Buyruq nomini yuboring (faqat <code>a-z 0-9 _</code>, maks. 32; masalan <code>help</code>):", "cml")


@answer("cm_name")
async def a_cm_name(m, state, d, bot, cbot):
    t = (m.text or "").strip().lstrip("/").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,32}", t) or t in RESERVED:
        return await say(m, d, "❌ Nom noto'g'ri yoki band (start, admin, cancel mumkin emas).")
    if db.command_by_name(cbot["id"], t):
        return await say(m, d, "❌ Bunday buyruq bor.")
    await ask(m, state, "cm_msg", f"📝 /{t} yuborilganda chiqadigan xabarni yuboring (har qanday turda).", "cml", name=t)


@answer("cm_msg")
async def a_cm_msg(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await say(m, d, "❌ Bu turdagi xabar qo'llab-quvvatlanmaydi.")
    cid = db.add_content(cbot["id"], new)
    cmid = db.add_command(cbot["id"], d["name"], cid)
    if not cmid:
        db.delete_content(cid)
        return await say(m, d, "❌ Bunday buyruq allaqachon bor.")
    await state.clear()
    text, kb = scr_cm(cmid)
    await say(m, d, "✅ Buyruq qo'shildi.\n\n" + text, reply_markup=kb)


async def r_cmd(x):
    cid = cb_int(x.c.data, 1)
    if own_cm(x, cid):
        await show(x.c, "🗑 Buyruq va uning xabari o'chiriladi. Davom etamizmi?",
                   M([B("✅ Ha", f"cmdo:{cid}"), B("⬅️ Yo'q", f"cm:{cid}")]))


async def r_cmdo(x):
    cid = cb_int(x.c.data, 1)
    if own_cm(x, cid):
        db.del_command(cid)
        await x.c.answer("O'chirildi")
        await show(x.c, *scr_cml(x.cbot))


# ---- majburiy obuna ----
def chat_ref(m):
    fo = getattr(m, "forward_origin", None)
    if fo is not None and getattr(fo, "type", None) == "channel":
        return fo.chat.id
    t = (m.text or "").strip()
    if re.fullmatch(r"-100\d{5,}", t):
        return int(t)
    if re.fullmatch(r"@[A-Za-z]\w{3,}", t):
        return t
    mm = re.fullmatch(r"(?:https?://)?t\.me/([A-Za-z]\w{3,})/?", t)
    return "@" + mm.group(1) if mm else None


async def r_ch_add(x):
    n, lim = len(db.channels(x.cbot["id"])), db.limit(x.cbot, "channel")
    if n >= lim:
        text, kb = scr_shop(x.cbot)
        return await show(x.c, f"⚠️ Kanallar limiti to'ldi ({n}/{lim}).\n\n" + text, kb)
    await ask(x.c, x.state, "ch_add", "📢 Kanalni yuboring: <code>@username</code>, <code>-100...</code> ID yoki kanaldan "
                                       "istalgan postni forward qiling.\n⚠️ Bot kanalda <b>administrator</b> bo'lishi shart.", "a:sub")


@answer("ch_add")
async def a_ch_add(m, state, d, bot, cbot):
    if len(db.channels(cbot["id"])) >= db.limit(db.get_bot(cbot["id"]), "channel"):
        await state.clear()
        return await say(m, d, "⚠️ Kanallar limiti to'lgan.")
    ref = chat_ref(m)
    if ref is None:
        return await say(m, d, "❌ Tushunmadim. @username, -100... ID yuboring yoki kanaldan post forward qiling.")
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        return await say(m, d, "❌ Kanal topilmadi. Botni kanalga administrator qilib qo'shganingizni tekshiring.")
    if getattr(chat.type, "value", chat.type) not in ("channel", "supergroup", "group"):
        return await say(m, d, "❌ Bu kanal yoki guruh emas.")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_admin = getattr(me.status, "value", me.status) in ("administrator", "creator")
    except Exception:
        is_admin = False
    if not is_admin:
        return await say(m, d, "❌ Bot bu kanalda administrator emas. Avval botni kanalga admin qiling, so'ng qayta yuboring.")
    url = f"https://t.me/{chat.username}" if chat.username else ""
    if not url:
        try:
            url = (await bot.create_chat_invite_link(chat.id)).invite_link
        except Exception:
            return await say(m, d, "❌ Kanal yopiq: botga «Taklif havolalari» huquqini bering yoki kanalni ommaviy qiling.")
    if not db.add_channel(cbot["id"], chat.id, chat.title, chat.username, url):
        return await say(m, d, "❌ Bu kanal allaqachon qo'shilgan.")
    await state.clear()
    text, kb = scr_sub(cbot)
    await say(m, d, "✅ Kanal qo'shildi.\n\n" + text, reply_markup=kb)


def own_ch(x, chid):
    ch = db.get_channel(chid)
    return ch if ch and ch["bot_id"] == x.cbot["id"] else None


async def r_ch(x):
    chid = cb_int(x.c.data, 1)
    if own_ch(x, chid):
        await show(x.c, *scr_ch(chid))


async def r_chl(x):
    chid = cb_int(x.c.data, 1)
    if own_ch(x, chid):
        await ask(x.c, x.state, "chl", "✏️ Tugmada ko'rinadigan nomni yuboring (<code>-</code> — kanalning o'z nomi):",
                  f"ch:{chid}", chid=chid)


@answer("chl")
async def a_chl(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not t or len(t) > 64:
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin.")
    db.update_channel(d["chid"], label="" if t == "-" else t)
    await state.clear()
    text, kb = scr_ch(d["chid"])
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_chu(x):
    chid = cb_int(x.c.data, 1)
    if own_ch(x, chid):
        await ask(x.c, x.state, "chu", "🔗 Tugma ochadigan URL manzilini yuboring (<code>https://...</code>):", f"ch:{chid}", chid=chid)


@answer("chu")
async def a_chu(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not re.fullmatch(r"(https?://|tg://)\S{3,500}", t):
        return await say(m, d, "❌ URL <code>https://</code> yoki <code>tg://</code> bilan boshlansin.")
    db.update_channel(d["chid"], url=t)
    await state.clear()
    text, kb = scr_ch(d["chid"])
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_chd(x):
    chid = cb_int(x.c.data, 1)
    if own_ch(x, chid):
        db.del_channel(chid)
        await x.c.answer("O'chirildi")
        await show(x.c, *scr_sub(x.cbot))


async def r_sj(x):
    await ask(x.c, x.state, "sj", "✏️ Kanal tugmasi matnini yuboring. <code>%channel%</code> — kanal nomi o'rniga qo'yiladi "
                                  "(masalan: <code>📢 %channel%</code>):", "a:sub")


async def r_sc(x):
    await ask(x.c, x.state, "sc", "✏️ «Tekshirish» tugmasi matnini yuboring:", "a:sub")


@answer("sj")
async def a_sj(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin.")
    db.update_bot(cbot["id"], join_text=t)
    await state.clear()
    text, kb = scr_sub(cbot)
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


@answer("sc")
async def a_sc(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await say(m, d, "❌ Matn 1–64 belgi bo'lsin.")
    db.update_bot(cbot["id"], check_text=t)
    await state.clear()
    text, kb = scr_sub(cbot)
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_sst(x):
    which = x.p[1] if len(x.p) > 1 else "j"
    if len(x.p) > 2:
        db.update_bot(x.cbot["id"], **{("join_style" if which == "j" else "check_style"): sty(x.p[2])})
        return await show(x.c, *scr_sub(x.cbot))
    rows = [[B(label, f"sst:{which}:{k or 'n'}")] for k, label in ct.STYLES.items()]
    rows.append([B("⬅️ Orqaga", "a:sub")])
    await show(x.c, "🎨 " + ("Kanal tugmalari" if which == "j" else "«Tekshirish» tugmasi") + " rangi:", M(*rows))


# ---- xabar yuborish ----
BCAST = set()
BCANCEL = set()
_TASKS = set()
STOP_KB = M([B("⛔ To'xtatish", "bcx")])


@answer("bc_msg")
async def a_bc_msg(m, state, d, bot, cbot):
    n = len(db.cuser_ids(cbot["id"]))
    await state.set_state(None)
    await state.update_data(act=None, src_chat=m.chat.id, src_msg=m.message_id)
    await say(m, d, f"📣 Xabar <b>{n}</b> ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
              reply_markup=M([B("✅ Yuborish", "bcgo")], [B("❌ Bekor qilish", "x:a:home")]), keep=True)


async def r_bcgo(x):
    d = await x.state.get_data()
    if not d.get("src_msg"):
        return await x.c.answer("Seans tugagan", show_alert=True)
    if x.cbot["id"] in BCAST:
        return await x.c.answer("Allaqachon yuborilmoqda", show_alert=True)
    await x.state.clear()
    await x.c.answer("Boshlandi")
    status = await x.c.message.edit_text("⏳ Yuborilmoqda...", reply_markup=STOP_KB)
    BCAST.add(x.cbot["id"])
    BCANCEL.discard(x.cbot["id"])
    t = asyncio.create_task(run_broadcast(x.bot, x.cbot["id"], d["src_chat"], d["src_msg"], status))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def run_broadcast(bot, bot_id, src_chat, src_msg, status):
    ids = db.cuser_ids(bot_id)
    ok = bad = 0
    stopped = False
    try:
        for i, uid in enumerate(ids, 1):
            if bot_id in BCANCEL:
                stopped = True
                break
            for attempt in (1, 2):
                try:
                    await bot.copy_message(uid, src_chat, src_msg)
                    ok += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                except TelegramForbiddenError:
                    db.cuser_inactive(bot_id, uid)
                    bad += 1
                except Exception:
                    bad += 1
                break
            if i % 25 == 0:
                await asyncio.sleep(1)
            if i % 100 == 0:
                try:
                    await status.edit_text(f"⏳ Yuborilmoqda... {i}/{len(ids)}", reply_markup=STOP_KB)
                except Exception:
                    pass
    finally:
        BCAST.discard(bot_id)
        BCANCEL.discard(bot_id)
        try:
            head = "⛔ To'xtatildi." if stopped else "✅ Yuborish tugadi."
            await status.edit_text(f"{head}\n📨 Yetkazildi: <b>{ok}</b>\n🚫 Yetmadi (bloklagan va h.k.): {bad}")
        except Exception:
            pass


# ---- balans / ban ----
@answer("bal_id")
async def a_bal_id(m, state, d, bot, cbot):
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await say(m, d, "❌ ID raqam yuboring.")
    u = db.get_cuser(cbot["id"], uid)
    if not u:
        return await say(m, d, "❌ Bu foydalanuvchi shu botga hali /start bosmagan.")
    await ask(m, state, "bal_amt", f"💰 {esc(u['name'])} — hozirgi balans: <b>{u['balance']}</b>\n"
                                   "Miqdorni yuboring: <code>+50</code> qo'shish, <code>-20</code> ayirish, <code>=100</code> aniq qiymat.",
              "a:home", uid=uid)


@answer("bal_amt")
async def a_bal_amt(m, state, d, bot, cbot):
    t = (m.text or "").strip().replace(" ", "")
    mode = "set" if t.startswith("=") else "add"
    try:
        amount = int(t.lstrip("="))
    except ValueError:
        return await say(m, d, "❌ Masalan: <code>+50</code>, <code>-20</code>, <code>=100</code>")
    nb = db.change_cbalance(cbot["id"], d["uid"], mode, amount)
    await state.clear()
    if nb is None:
        return await say(m, d, "❌ Foydalanuvchi topilmadi.")
    text, kb = scr_home(cbot)
    await say(m, d, f"✅ Yangi balans: <b>{nb}</b>\n\n" + text, reply_markup=kb)


@answer("ban_id")
async def a_ban_id(m, state, d, bot, cbot):
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await say(m, d, "❌ ID raqam yuboring.")
    u = db.get_cuser(cbot["id"], uid)
    if not u:
        return await say(m, d, "❌ Bu foydalanuvchi shu botga hali /start bosmagan.")
    if uid == cbot["owner_id"] or uid in config.ADMIN_IDS or db.is_bot_admin(cbot["id"], uid):
        return await say(m, d, "❌ Adminni ban qilib bo'lmaydi.")
    flag = not u["banned"]
    db.set_cban(cbot["id"], uid, flag)
    await state.clear()
    text, kb = scr_home(cbot)
    await say(m, d, ("🚫 Ban qilindi: " if flag else "✅ Bandan chiqarildi: ") + f"<code>{uid}</code>\n\n" + text, reply_markup=kb)


# ---- referal va bonus ----
@answer("eco_ref")
async def a_eco_ref(m, state, d, bot, cbot):
    try:
        v = util.parse_int(m.text, 0, 1_000_000)
    except ValueError:
        return await say(m, d, "❌ 0 dan 1 000 000 gacha butun son yuboring.")
    db.update_bot(cbot["id"], ref_reward=v)
    await state.clear()
    text, kb = scr_eco(cbot)
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


@answer("eco_bonus")
async def a_eco_bonus(m, state, d, bot, cbot):
    try:
        v = util.parse_int(m.text, 0, 1_000_000)
    except ValueError:
        return await say(m, d, "❌ 0 dan 1 000 000 gacha butun son yuboring.")
    db.update_bot(cbot["id"], daily_bonus=v)
    await state.clear()
    text, kb = scr_eco(cbot)
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


@answer("eco_welcome")
async def a_eco_welcome(m, state, d, bot, cbot):
    try:
        v = util.parse_int(m.text, 0, 1_000_000)
    except ValueError:
        return await say(m, d, "❌ 0 dan 1 000 000 gacha butun son yuboring.")
    db.update_bot(cbot["id"], welcome_bonus=v)
    await state.clear()
    text, kb = scr_eco(cbot)
    await say(m, d, "✅ Saqlandi.\n\n" + text, reply_markup=kb)


# ---- foydalanuvchilar eksporti ----
async def export_users(x):
    rows = db.cusers_export(x.cbot["id"])
    if not rows:
        return await x.c.answer("Hali foydalanuvchi yo'q", show_alert=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "ism", "username", "balans", "takliflar", "taklif_qilgan", "ban", "faol", "qo'shilgan", "oxirgi_faollik"])
    for r in rows:
        w.writerow([r["user_id"], util.csv_cell(r["name"]), util.csv_cell(r["username"]), r["balance"], r["refs"],
                    r["referrer_id"] or "", r["banned"], r["active"], util.fmt_dt(r["joined"]), util.fmt_dt(r["last_seen"])])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM: Excel'da o'zbekcha harflar to'g'ri ochiladi
    await x.c.answer()
    await x.bot.send_document(x.c.message.chat.id, BufferedInputFile(data, filename=f"users_{x.cbot['id']}.csv"),
                              caption=f"📥 Foydalanuvchilar: {len(rows)} ta")


# ---- adminlar (faqat egasi) ----
async def r_adm(x):
    if not is_owner_level(x.cbot, x.user.id):
        return await x.c.answer("Faqat bot egasi", show_alert=True)
    uid = cb_int(x.c.data, 1)
    if uid in db.bot_admins(x.cbot["id"]):
        await show(x.c, f"🗑 <code>{uid}</code> adminlikdan olinsinmi?",
                   M([B("✅ Ha", f"admd:{uid}"), B("⬅️ Yo'q", "a:adm")]))


async def r_adm_add(x):
    if not is_owner_level(x.cbot, x.user.id):
        return await x.c.answer("Faqat bot egasi", show_alert=True)
    await ask(x.c, x.state, "adm_add", "👮 Yangi admin ID raqamini yuboring (u shu botga /start bosgan bo'lishi kerak):", "a:adm")


@answer("adm_add")
async def a_adm_add(m, state, d, bot, cbot):
    if not is_owner_level(cbot, m.from_user.id):
        return await state.clear()
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await say(m, d, "❌ ID raqam yuboring.")
    if not db.get_cuser(cbot["id"], uid):
        return await say(m, d, "❌ Bu foydalanuvchi shu botga hali /start bosmagan.")
    if is_owner_level(cbot, uid):
        return await say(m, d, "ℹ️ Bu foydalanuvchi allaqachon admin.")
    if len(db.bot_admins(cbot["id"])) >= 10:
        return await say(m, d, "❌ Maksimal 10 ta qo'shimcha admin.")
    ok = db.add_bot_admin(cbot["id"], uid)
    await state.clear()
    text, kb = scr_adm(cbot)
    await say(m, d, ("✅ Admin qo'shildi.\n\n" if ok else "ℹ️ Bu admin allaqachon qo'shilgan.\n\n") + text, reply_markup=kb)


async def r_admd(x):
    if not is_owner_level(x.cbot, x.user.id):
        return await x.c.answer("Faqat bot egasi", show_alert=True)
    db.del_bot_admin(x.cbot["id"], cb_int(x.c.data, 1))
    await x.c.answer("Olib tashlandi")
    await show(x.c, *scr_adm(x.cbot))


# ---- kalit so'zlar ----
async def r_kwl(x):
    await show(x.c, *scr_kwl(x.cbot))


def own_kw(x, kid):
    k = db.get_keyword(kid)
    return k if k and k["bot_id"] == x.cbot["id"] else None


async def r_kw(x):
    kid = cb_int(x.c.data, 1)
    if own_kw(x, kid):
        await show(x.c, *scr_kw(kid))


async def r_kw_add(x):
    n = len(db.keywords(x.cbot["id"]))
    if n >= MAX_KEYWORDS:
        return await x.c.answer(f"Maksimal {MAX_KEYWORDS} ta kalit so'z", show_alert=True)
    await ask(x.c, x.state, "kw_word", "🔑 Kalit so'zni yuboring (1–64 belgi, «/» bilan boshlanmasin). "
                                       "Foydalanuvchi aynan shuni yozsa bot javob beradi:", "kwl")


@answer("kw_word")
async def a_kw_word(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64 or t.startswith("/"):
        return await say(m, d, "❌ 1–64 belgi bo'lsin va «/» bilan boshlanmasin.")
    if db.keyword_by_word(cbot["id"], t):
        return await say(m, d, "❌ Bunday kalit so'z allaqachon bor.")
    if db.rbutton_by_text(cbot["id"], t):
        return await say(m, d, "❌ Bu matn pastki tugma nomi bilan bir xil — pastki tugma ustun turadi. Boshqa so'z yuboring.")
    await ask(m, state, "kw_msg", f"📝 «{esc(t)}» yozilganda chiqadigan xabarni yuboring (har qanday turda).", "kwl", word=t)


@answer("kw_msg")
async def a_kw_msg(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await say(m, d, "❌ Bu turdagi xabar qo'llab-quvvatlanmaydi.")
    if len(db.keywords(cbot["id"])) >= MAX_KEYWORDS:
        await state.clear()
        return await say(m, d, f"❌ Maksimal {MAX_KEYWORDS} ta kalit so'z.")
    cid = db.add_content(cbot["id"], new)
    kid = db.add_keyword(cbot["id"], d["word"], cid)
    if not kid:
        db.delete_content(cid)
        return await say(m, d, "❌ Bunday kalit so'z allaqachon bor.")
    await state.clear()
    text, kb = scr_kw(kid)
    await say(m, d, "✅ Kalit so'z qo'shildi.\n\n" + text, reply_markup=kb)


async def r_kwd(x):
    kid = cb_int(x.c.data, 1)
    if own_kw(x, kid):
        await show(x.c, "🗑 Kalit so'z va uning xabari o'chiriladi. Davom etamizmi?",
                   M([B("✅ Ha", f"kwdo:{kid}"), B("⬅️ Yo'q", f"kw:{kid}")]))


async def r_kwdo(x):
    kid = cb_int(x.c.data, 1)
    if own_kw(x, kid):
        db.del_keyword(kid)
        await x.c.answer("O'chirildi")
        await show(x.c, *scr_kwl(x.cbot))


# ---- xabar yuborishni to'xtatish ----
async def r_bcx(x):
    if x.cbot["id"] in BCAST:
        BCANCEL.add(x.cbot["id"])
        await x.c.answer("To'xtatilmoqda...")
    else:
        await x.c.answer("Yuborish faol emas")


ROUTES = {
    "x": r_x, "a": r_a, "ce": r_ce, "cw": r_cw, "cp": r_cp, "ia": r_ia, "ik": r_ik, "it": r_it, "is": r_is,
    "lay": r_lay, "il": r_il, "ib": r_ib, "ibt": r_ibt, "ibc": r_ibc, "ibs": r_ibs, "ibv": r_ibv, "ibd": r_ibd,
    "rbl": r_rbl, "rb": r_rb, "rb+": r_rb_add, "rbn": r_rbn, "rbc": r_rbc, "rbs": r_rbs, "rbd": r_rbd, "rbdo": r_rbdo,
    "cml": r_cml, "cm": r_cm, "cm+": r_cm_add, "cmd": r_cmd, "cmdo": r_cmdo,
    "ch": r_ch, "ch+": r_ch_add, "chl": r_chl, "chu": r_chu, "chd": r_chd, "sj": r_sj, "sc": r_sc, "sst": r_sst,
    "bcgo": r_bcgo, "bcx": r_bcx, "kwl": r_kwl, "kw": r_kw, "kw+": r_kw_add, "kwd": r_kwd, "kwdo": r_kwdo,
    "adm": r_adm, "adm+": r_adm_add, "admd": r_admd,
}


import kino  # noqa: E402,F401  (Kino shabloni: yo'nalishlar va handlerlarni ro'yxatdan o'tkazadi)
