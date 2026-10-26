"""Yaratilgan botlar: foydalanuvchi qismi (start, tugmalar, majburiy obuna) va bot ichidagi admin panel (/admin)."""
import asyncio
import logging
import re
from types import SimpleNamespace

from aiogram import BaseMiddleware, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import content as ct
import db
import util
from storage import SqliteStorage
from util import esc

log = logging.getLogger("builder.child")
RESERVED = {"start", "admin", "cancel"}


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
    return util.make_ctx(cbot, user, cu["balance"] if cu else 0, db.count_cusers(cbot["id"]))


def style_icon(s):
    return {"primary": "🔵 ", "success": "🟢 ", "danger": "🔴 "}.get(s, "")


# ============================== dispatcher va middleware ==============================
dp = Dispatcher(storage=SqliteStorage())
admin_r = Router()
user_r = Router()


class CtxMw(BaseMiddleware):
    """Bot yozuvini topadi, foydalanuvchini ro'yxatga oladi, adminlikni aniqlaydi (egasi yoki asosiy admin)."""

    async def __call__(self, handler, event, data):
        bot = data["bot"]
        row = db.get_bot(bot.id)
        if not row or not row["active"]:
            return None
        data["cbot"] = row
        user = data.get("event_from_user")
        data["admin"] = False
        if user and not user.is_bot:
            data["admin"] = user.id == row["owner_id"] or user.id in config.ADMIN_IDS
            cu = db.cuser_touch(row["id"], user)
            if cu["banned"] and not data["admin"]:
                return None
        return await handler(event, data)


class IsAdmin(Filter):
    async def __call__(self, event, admin: bool = False) -> bool:
        return bool(admin)


PREFIXES = {"a", "x", "ce", "cw", "cp", "ia", "ik", "it", "is", "lay", "il", "ib", "ibt", "ibc", "ibs", "ibv", "ibd",
            "rbl", "rb", "rb+", "rbn", "rbc", "rbs", "rbd", "rbdo", "cml", "cm", "cm+", "cmd", "cmdo", "ch", "ch+",
            "chl", "chu", "chd", "sj", "sc", "sst", "bcgo"}
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


@user_r.message(CommandStart())
async def h_start(m: Message, bot, cbot, admin):
    if not admin and not await passes_gate(bot, cbot, m.from_user, m.chat.id):
        return
    await send_start(bot, cbot, m.from_user, m.chat.id)
    if m.from_user.id == cbot["owner_id"]:
        await m.answer("🛠 Botni sozlash paneli: /admin")


@user_r.message(F.text)
async def h_text(m: Message, bot, cbot, admin):
    t = m.text.strip()
    if t.startswith("/"):
        name = t[1:].split()[0].split("@")[0].lower() if len(t) > 1 else ""
        row = db.command_by_name(cbot["id"], name)
    else:
        row = db.rbutton_by_text(cbot["id"], m.text)
    if not row:
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
    return text, M(
        [B("📝 Start xabari", f"ce:{cbot['start_cid']}"), B("⌨️ Pastki tugmalar", "rbl")],
        [B("💬 Buyruqlar", "cml"), B("📢 Majburiy obuna", "a:sub")],
        [B("📣 Xabar yuborish", "a:bc"), B("📊 Statistika", "a:stat")],
        [B("💰 Balans", "a:bal"), B("🚫 Ban / Unban", "a:ban")],
        [B("🔤 O'zgaruvchilar", "a:vars"), B("🛒 Do'kon / limitlar", "a:shop")],
    )


def scr_stat(cbot):
    s = db.cstats(cbot["id"])
    return (f"📊 <b>Statistika</b>\n👥 Jami: <b>{s['total']}</b>\n✅ Faol (bloklamagan): {s['active']}\n"
            f"🆕 Bugun: {s['today']} · 7 kun: {s['week']}\n🕒 24 soatda faol: {s['online']}\n🚫 Ban: {s['banned']}",
            M([B("⬅️ Orqaga", "a:home")]))


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


async def ask(obj, state, act, prompt, back, **data):
    await state.clear()
    await state.set_state(S.ask)
    await state.update_data(act=act, back=back, **data)
    msg = obj.message if isinstance(obj, CallbackQuery) else obj
    await msg.answer(prompt, reply_markup=M([B("❌ Bekor qilish", "x:" + back)]))
    if isinstance(obj, CallbackQuery):
        await obj.answer()


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
        return await m.answer("❌ Bu turdagi xabar qo'llab-quvvatlanmaydi. Matn, rasm, video, fayl, audio, GIF, stiker yuboring.")
    if not own_content_id(cbot, d["cid"]):
        return await state.clear()
    db.replace_content(d["cid"], new)
    await state.clear()
    text, kb = scr_ce(cbot, d["cid"])
    await m.answer("✅ Xabar yangilandi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ " + err)
    await ask(m, state, "ib_text", "✏️ Tugma matnini yuboring (maks. 64 belgi). O'zgaruvchilar mumkin: <code>%name%</code>, "
                                    "<code>%balance%</code>...", f"ia:{d['cid']}", flow="ib", cid=d["cid"], kind=d["kind"], value=val)


@answer("ib_text")
async def a_ib_text(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await m.answer("❌ Matn 1–64 belgi bo'lsin.")
    await state.set_state(None)
    await state.update_data(text=t, act=None)
    await m.answer("🎨 Tugma rangini tanlang:", reply_markup=style_picker("is", f"ia:{d['cid']}"))


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
        return await m.answer("❌ Matn 1–64 belgi bo'lsin.")
    db.update_ibutton(d["iid"], text=t)
    await state.clear()
    text, kb = scr_ib(d["iid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ " + err)
    db.update_ibutton(d["iid"], value=val)
    await state.clear()
    text, kb = scr_ib(d["iid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ Matn 1–64 belgi bo'lsin va «/» bilan boshlanmasin.")
    if db.rbutton_by_text(cbot["id"], t):
        return await m.answer("❌ Bunday tugma allaqachon bor. Boshqa nom yuboring.")
    await ask(m, state, "rb_msg", "📝 Endi shu tugma bosilganda yuboriladigan xabarni yuboring (har qanday turda).", "rbl", name=t)


@answer("rb_msg")
async def a_rb_msg(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await m.answer("❌ Bu turdagi xabar qo'llab-quvvatlanmaydi.")
    await state.set_state(None)
    await state.update_data(act=None, flow="rb", content=new, cid=0)
    await m.answer("🎨 Tugma rangini tanlang:", reply_markup=style_picker("is", "rbl"))


async def r_rbn(x):
    rid = cb_int(x.c.data, 1)
    if own_rb(x, rid):
        await ask(x.c, x.state, "rbn", "✏️ Yangi nomni yuboring:", f"rb:{rid}", rid=rid)


@answer("rbn")
async def a_rbn(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    ex = db.rbutton_by_text(cbot["id"], t)
    if not 1 <= len(t) <= 64 or t.startswith("/") or (ex and ex["id"] != d["rid"]):
        return await m.answer("❌ Nom 1–64 belgi, «/»siz va takrorlanmas bo'lsin.")
    db.update_rbutton(d["rid"], text=t)
    await state.clear()
    text, kb = scr_rb(d["rid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ Nom noto'g'ri yoki band (start, admin, cancel mumkin emas).")
    if db.command_by_name(cbot["id"], t):
        return await m.answer("❌ Bunday buyruq bor.")
    await ask(m, state, "cm_msg", f"📝 /{t} yuborilganda chiqadigan xabarni yuboring (har qanday turda).", "cml", name=t)


@answer("cm_msg")
async def a_cm_msg(m, state, d, bot, cbot):
    new = ct.extract(m)
    if not new:
        return await m.answer("❌ Bu turdagi xabar qo'llab-quvvatlanmaydi.")
    cid = db.add_content(cbot["id"], new)
    cmid = db.add_command(cbot["id"], d["name"], cid)
    if not cmid:
        db.delete_content(cid)
        return await m.answer("❌ Bunday buyruq allaqachon bor.")
    await state.clear()
    text, kb = scr_cm(cmid)
    await m.answer("✅ Buyruq qo'shildi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("⚠️ Kanallar limiti to'lgan.")
    ref = chat_ref(m)
    if ref is None:
        return await m.answer("❌ Tushunmadim. @username, -100... ID yuboring yoki kanaldan post forward qiling.")
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        return await m.answer("❌ Kanal topilmadi. Botni kanalga administrator qilib qo'shganingizni tekshiring.")
    if getattr(chat.type, "value", chat.type) not in ("channel", "supergroup", "group"):
        return await m.answer("❌ Bu kanal yoki guruh emas.")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_admin = getattr(me.status, "value", me.status) in ("administrator", "creator")
    except Exception:
        is_admin = False
    if not is_admin:
        return await m.answer("❌ Bot bu kanalda administrator emas. Avval botni kanalga admin qiling, so'ng qayta yuboring.")
    url = f"https://t.me/{chat.username}" if chat.username else ""
    if not url:
        try:
            url = (await bot.create_chat_invite_link(chat.id)).invite_link
        except Exception:
            return await m.answer("❌ Kanal yopiq: botga «Taklif havolalari» huquqini bering yoki kanalni ommaviy qiling.")
    if not db.add_channel(cbot["id"], chat.id, chat.title, chat.username, url):
        return await m.answer("❌ Bu kanal allaqachon qo'shilgan.")
    await state.clear()
    text, kb = scr_sub(cbot)
    await m.answer("✅ Kanal qo'shildi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ Matn 1–64 belgi bo'lsin.")
    db.update_channel(d["chid"], label="" if t == "-" else t)
    await state.clear()
    text, kb = scr_ch(d["chid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


async def r_chu(x):
    chid = cb_int(x.c.data, 1)
    if own_ch(x, chid):
        await ask(x.c, x.state, "chu", "🔗 Tugma ochadigan URL manzilini yuboring (<code>https://...</code>):", f"ch:{chid}", chid=chid)


@answer("chu")
async def a_chu(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not re.fullmatch(r"(https?://|tg://)\S{3,500}", t):
        return await m.answer("❌ URL <code>https://</code> yoki <code>tg://</code> bilan boshlansin.")
    db.update_channel(d["chid"], url=t)
    await state.clear()
    text, kb = scr_ch(d["chid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


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
        return await m.answer("❌ Matn 1–64 belgi bo'lsin.")
    db.update_bot(cbot["id"], join_text=t)
    await state.clear()
    text, kb = scr_sub(cbot)
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


@answer("sc")
async def a_sc(m, state, d, bot, cbot):
    t = (m.text or "").strip()
    if not 1 <= len(t) <= 64:
        return await m.answer("❌ Matn 1–64 belgi bo'lsin.")
    db.update_bot(cbot["id"], check_text=t)
    await state.clear()
    text, kb = scr_sub(cbot)
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


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
_TASKS = set()


@answer("bc_msg")
async def a_bc_msg(m, state, d, bot, cbot):
    n = len(db.cuser_ids(cbot["id"]))
    await state.set_state(None)
    await state.update_data(act=None, src_chat=m.chat.id, src_msg=m.message_id)
    await m.answer(f"📣 Xabar <b>{n}</b> ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
                   reply_markup=M([B("✅ Yuborish", "bcgo")], [B("❌ Bekor qilish", "x:a:home")]))


async def r_bcgo(x):
    d = await x.state.get_data()
    if not d.get("src_msg"):
        return await x.c.answer("Seans tugagan", show_alert=True)
    if x.cbot["id"] in BCAST:
        return await x.c.answer("Allaqachon yuborilmoqda", show_alert=True)
    await x.state.clear()
    await x.c.answer("Boshlandi")
    status = await x.c.message.edit_text("⏳ Yuborilmoqda...")
    BCAST.add(x.cbot["id"])
    t = asyncio.create_task(run_broadcast(x.bot, x.cbot["id"], d["src_chat"], d["src_msg"], status))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def run_broadcast(bot, bot_id, src_chat, src_msg, status):
    ids = db.cuser_ids(bot_id)
    ok = bad = 0
    try:
        for i, uid in enumerate(ids, 1):
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
                    await status.edit_text(f"⏳ Yuborilmoqda... {i}/{len(ids)}")
                except Exception:
                    pass
    finally:
        BCAST.discard(bot_id)
        try:
            await status.edit_text(f"✅ Yuborish tugadi.\n📨 Yetkazildi: <b>{ok}</b>\n🚫 Yetmadi (bloklagan va h.k.): {bad}")
        except Exception:
            pass


# ---- balans / ban ----
@answer("bal_id")
async def a_bal_id(m, state, d, bot, cbot):
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await m.answer("❌ ID raqam yuboring.")
    u = db.get_cuser(cbot["id"], uid)
    if not u:
        return await m.answer("❌ Bu foydalanuvchi shu botga hali /start bosmagan.")
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
        return await m.answer("❌ Masalan: <code>+50</code>, <code>-20</code>, <code>=100</code>")
    nb = db.change_cbalance(cbot["id"], d["uid"], mode, amount)
    await state.clear()
    if nb is None:
        return await m.answer("❌ Foydalanuvchi topilmadi.")
    text, kb = scr_home(cbot)
    await m.answer(f"✅ Yangi balans: <b>{nb}</b>\n\n" + text, reply_markup=kb)


@answer("ban_id")
async def a_ban_id(m, state, d, bot, cbot):
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await m.answer("❌ ID raqam yuboring.")
    u = db.get_cuser(cbot["id"], uid)
    if not u:
        return await m.answer("❌ Bu foydalanuvchi shu botga hali /start bosmagan.")
    if uid == cbot["owner_id"] or uid in config.ADMIN_IDS:
        return await m.answer("❌ Adminni ban qilib bo'lmaydi.")
    flag = not u["banned"]
    db.set_cban(cbot["id"], uid, flag)
    await state.clear()
    text, kb = scr_home(cbot)
    await m.answer(("🚫 Ban qilindi: " if flag else "✅ Bandan chiqarildi: ") + f"<code>{uid}</code>\n\n" + text, reply_markup=kb)


ROUTES = {
    "x": r_x, "a": r_a, "ce": r_ce, "cw": r_cw, "cp": r_cp, "ia": r_ia, "ik": r_ik, "it": r_it, "is": r_is,
    "lay": r_lay, "il": r_il, "ib": r_ib, "ibt": r_ibt, "ibc": r_ibc, "ibs": r_ibs, "ibv": r_ibv, "ibd": r_ibd,
    "rbl": r_rbl, "rb": r_rb, "rb+": r_rb_add, "rbn": r_rbn, "rbc": r_rbc, "rbs": r_rbs, "rbd": r_rbd, "rbdo": r_rbdo,
    "cml": r_cml, "cm": r_cm, "cm+": r_cm_add, "cmd": r_cmd, "cmdo": r_cmdo,
    "ch": r_ch, "ch+": r_ch_add, "chl": r_chl, "chu": r_chu, "chd": r_chd, "sj": r_sj, "sc": r_sc, "sst": r_sst,
    "bcgo": r_bcgo,
}
