"""Asosiy (builder) bot: bot yaratish, botlarim, do'kon, hamyon (Stars), promokod."""
import logging
import re
from types import SimpleNamespace

from aiogram import BaseMiddleware, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import config
import db
import util
from child import B, M, cb_int, show
from storage import SqliteStorage
from util import esc

log = logging.getLogger("builder.master")
mgr = None  # manager.Manager (main.py o'rnatadi)

dp = Dispatcher(storage=SqliteStorage())
router = Router()
admin_router = Router()
TOKEN_RE = re.compile(r"\d{6,12}:[\w-]{30,}")


class S(StatesGroup):
    ask = State()


class MasterMw(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        data["is_admin"] = False
        if user and not user.is_bot:
            row = db.upsert_user(user)
            data["is_admin"] = user.id in config.ADMIN_IDS
            if row["banned"] and not data["is_admin"]:
                return None
            data["me"] = row
        return await handler(event, data)


class IsAdminF(Filter):
    async def __call__(self, event, is_admin: bool = False) -> bool:
        return bool(is_admin)


class AdminCbF(Filter):
    async def __call__(self, event: CallbackQuery, is_admin: bool = False) -> bool:
        return bool(is_admin and event.data and event.data.split(":")[0] == "ad")


dp.update.outer_middleware(MasterMw())
dp.include_router(admin_router)
dp.include_router(router)
router.message.filter(F.chat.type == "private")
admin_router.message.filter(F.chat.type == "private")

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
    await msg.answer(prompt, reply_markup=M([B("❌ Bekor qilish", back)]))
    if isinstance(obj, CallbackQuery):
        try:
            await obj.answer()
        except TelegramBadRequest:
            pass


@router.message(S.ask)
@admin_router.message(S.ask, IsAdminF())
async def h_answer(m: Message, state: FSMContext, bot, is_admin, me):
    d = await state.get_data()
    fn = ANSWERS.get(d.get("act"))
    if not fn:
        await state.clear()
        return
    await fn(m, state, d, bot)


# ============================== ekranlar ==============================
def scr_home(me, is_admin):
    name, sym = db.cur()
    me = db.get_user(me["user_id"])
    text = (f"👋 <b>{esc(config.BRAND)}</b>\n\nTokenni yuborib o'z Telegram botingizni yarating: bot ichida pastki va inline "
            "tugmalar, har qanday turdagi xabarlar, /start, majburiy obuna va boshqalar.\n\n"
            f"💰 Hamyon: <b>{me['balance']} {sym}</b> ({esc(name)})")
    rows = [[B("➕ Bot yaratish", "new"), B("📂 Botlarim", "my:0")],
            [B("🛒 Do'kon", "shop"), B("💰 Hamyon", "wal")],
            [B("🎟 Promokod", "promo"), B("ℹ️ Yordam", "help")]]
    if is_admin:
        rows.append([B("🛡 Admin panel", "ad")])
    return text, M(*rows)


def scr_help():
    return ("ℹ️ <b>Yordam</b>\n\n"
            "1) «➕ Bot yaratish» → @BotFather'dan olgan tokeningizni yuboring.\n"
            "2) Yaratilgan botingizga kirib <code>/admin</code> yuboring — shu yerda start xabari, pastki va inline "
            "tugmalar, buyruqlar, majburiy obuna, xabar yuborish va statistikani sozlaysiz.\n"
            "3) Standart limitlar: har xabarga 2 ta inline tugma, 1 ta majburiy kanal. Ko'proq kerak bo'lsa — «🛒 Do'kon».\n"
            "4) Hamyon Telegram Stars (⭐) orqali to'ldiriladi yoki promokod bilan.\n\n"
            "🔤 <b>Xabar o'zgaruvchilari</b>\n" + util.VARS_HELP), M([B("⬅️ Menyu", "home")])


def bot_status(row):
    if not row["active"]:
        return "⏸ to'xtatilgan"
    return "🟢 ishlayapti" if mgr and mgr.is_running(row["id"]) else "🔴 ishlamayapti"


def can_manage(row, uid, is_admin):
    return bool(row) and (row["owner_id"] == uid or is_admin)


def scr_bot(row, is_admin=False):
    row = db.get_bot(row["id"])
    lim = db.limits(row)
    text = (f"🤖 <b>{esc(row['name'])}</b> · @{esc(row['username'])}\nHolat: {bot_status(row)}\n"
            f"👥 Foydalanuvchilar: {db.count_cusers(row['id'])}\n"
            f"🔘 Inline: {lim['inline']} · ⌨️ Pastki: {lim['reply']} · 📢 Kanal: {lim['channel']}\n\n"
            "Sozlash: botga kirib <code>/admin</code> yuboring.")
    rows = [[B("🔗 Botni ochish", url=f"https://t.me/{row['username']}")],
            [B("🛒 Do'kon", f"shop:{row['id']}"), B("▶️ Yoqish" if not row["active"] else "⏸ To'xtatish", f"bt:{row['id']}")],
            [B("🔑 Token", f"bk:{row['id']}"), B("🗑 O'chirish", f"bd:{row['id']}")]]
    if is_admin:
        rows.append([B("🎁 Slot berish", f"ad:gr:{row['id']}")])
    rows.append([B("⬅️ Botlarim", "my:0")])
    return text, M(*rows)


def scr_shop(row, me):
    row = db.get_bot(row["id"])
    me = db.get_user(me["user_id"])
    name, sym = db.cur()
    lines = [f"🛒 <b>Do'kon</b> · @{esc(row['username'])}", f"💰 Hamyon: <b>{me['balance']} {sym}</b>\n"]
    rows = []
    for k, it in db.ITEMS.items():
        price, cap, now = db.get_s(it["price"]), db.get_s(it["cap"]), db.limit(row, k)
        if price <= 0:
            lines.append(f"{it['title']}: {now}/{cap} — sotuvda yo'q")
            continue
        lines.append(f"{it['title']}: <b>{now}</b>/{cap} · 1 ta = {price} {sym}")
        btns = [B(f"+{q} · {price * q} {sym}", f"buy:{row['id']}:{k}:{q}") for q in (1, 5) if now + q <= cap]
        if btns:
            rows.append([B(it["title"].split(" ")[0] + " ", "noop")] + btns)
    rows.append([B("💰 Hamyonni to'ldirish", "wal"), B("⬅️ Orqaga", f"bot:{row['id']}")])
    return "\n".join(lines), M(*rows)


def scr_wallet(me):
    me = db.get_user(me["user_id"])
    name, sym = db.cur()
    rate = db.get_s("coins_per_star")
    text = (f"💰 <b>Hamyon</b>\nBalans: <b>{me['balance']} {sym}</b> ({esc(name)})\n"
            f"Kurs: 1 ⭐ = {rate} {sym}\n\nTo'ldirish uchun paketni tanlang (Telegram Stars orqali):")
    pk = db.packages()
    rows, line = [], []
    for p in pk:
        line.append(B(f"{p} ⭐ → {p * rate} {sym}", f"tp:{p}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([B("✍️ Boshqa miqdor", "tp:c")])
    rows.append([B("⬅️ Menyu", "home")])
    return text, M(*rows)


# ============================== start / menyu ==============================
@router.message(CommandStart())
async def h_start(m: Message, command: CommandObject, state: FSMContext, me, is_admin):
    await state.clear()
    args = (command.args or "").strip()
    if args.startswith("shop_") and args[5:].isdigit():
        row = db.get_bot(int(args[5:]))
        if can_manage(row, m.from_user.id, is_admin):
            text, kb = scr_shop(row, me)
            return await m.answer(text, reply_markup=kb)
    text, kb = scr_home(me, is_admin)
    await m.answer(text, reply_markup=kb)


@router.message(Command("cancel"))
async def h_cancel(m: Message, state: FSMContext, me, is_admin):
    await state.clear()
    text, kb = scr_home(me, is_admin)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data == "home")
async def cb_home(c: CallbackQuery, state: FSMContext, me, is_admin):
    await state.clear()
    await show(c, *scr_home(me, is_admin))


@router.callback_query(F.data == "help")
async def cb_help(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, *scr_help())


# ============================== bot yaratish ==============================
@router.callback_query(F.data == "new")
async def cb_new(c: CallbackQuery, state: FSMContext, is_admin):
    mx = db.get_s("max_bots")
    if not is_admin and db.count_bots(c.from_user.id) >= mx:
        return await c.answer(f"Siz maksimal {mx} ta bot yarata olasiz.", show_alert=True)
    await ask(c, state, "token", "🤖 Yangi bot tokenini yuboring.\n\nToken yo'q bo'lsa: @BotFather → /newbot → nom va "
                                 "username kiriting → token nusxalang.", "home")


@answer("token")
async def a_token(m: Message, state, d, bot):
    tok = (m.text or "").strip()
    try:
        await m.delete()  # token chatda qolmasin
    except Exception:
        pass
    if not TOKEN_RE.fullmatch(tok):
        return await m.answer("❌ Token formati noto'g'ri. Qayta yuboring yoki bekor qiling.")
    mx = db.get_s("max_bots")
    if m.from_user.id not in config.ADMIN_IDS and db.count_bots(m.from_user.id) >= mx:
        await state.clear()
        return await m.answer(f"❌ Bot limiti ({mx}) to'lgan.")
    try:
        me = await mgr.make_bot(tok).get_me()
    except TelegramUnauthorizedError:
        return await m.answer("❌ Token ishlamayapti (bekor qilingan yoki noto'g'ri). Qayta yuboring.")
    except Exception as e:
        return await m.answer(f"❌ Tokenni tekshirib bo'lmadi: {esc(e)}")
    if db.get_bot(me.id):
        return await m.answer("❌ Bu bot allaqachon ulangan.")
    row = db.create_bot_with_defaults(me.id, m.from_user.id, tok, me.username, me.full_name)
    await mgr.start_bot(row)
    await state.clear()
    await m.answer(f"✅ <b>@{esc(me.username)}</b> yaratildi va ishga tushdi!\n\n"
                   "Endi botga kirib <code>/start</code>, so'ng <code>/admin</code> yuboring — u yerda hamma narsani sozlaysiz.",
                   reply_markup=M([B("🔗 Botni ochish", url=f"https://t.me/{me.username}")],
                                  [B("📂 Botlarim", "my:0"), B("⬅️ Menyu", "home")]))


# ============================== botlarim ==============================
@router.callback_query(F.data.startswith("my:"))
async def cb_my(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows_ = db.bots_of(c.from_user.id)
    page = cb_int(c.data, 1)
    per = 8
    pages = util.pages(len(rows_), per)
    page = min(max(page, 0), pages - 1)
    rows = [[B(f"{'🟢' if r['active'] else '⏸'} @{r['username']}", f"bot:{r['id']}")] for r in rows_[page * per:(page + 1) * per]]
    nav = []
    if page > 0:
        nav.append(B("◀️", f"my:{page - 1}"))
    if pages > 1:
        nav.append(B(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(B("▶️", f"my:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("➕ Bot yaratish", "new"), B("⬅️ Menyu", "home")])
    await show(c, f"📂 <b>Botlarim</b> ({len(rows_)})" if rows_ else "📂 Hali botingiz yo'q. «➕ Bot yaratish» ni bosing.", M(*rows))


def owned(c, is_admin, idx=1):
    row = db.get_bot(cb_int(c.data, idx))
    return row if can_manage(row, c.from_user.id, is_admin) else None


@router.callback_query(F.data.startswith("bot:"))
async def cb_bot(c: CallbackQuery, state: FSMContext, is_admin):
    await state.clear()
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await show(c, *scr_bot(row, is_admin))


@router.callback_query(F.data.startswith("bt:"))
async def cb_toggle(c: CallbackQuery, is_admin):
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    if row["active"]:
        db.update_bot(row["id"], active=0)
        await mgr.stop_bot(row["id"])
    else:
        db.update_bot(row["id"], active=1)
        await mgr.start_bot(db.get_bot(row["id"]))
    await c.answer("Saqlandi")
    await show(c, *scr_bot(row, is_admin))


@router.callback_query(F.data.startswith("bk:"))
async def cb_token(c: CallbackQuery, state: FSMContext, is_admin):
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await ask(c, state, "retoken", "🔑 Shu bot uchun yangi tokenni yuboring (@BotFather → /token orqali olingan).\n"
                                   "Faqat shu botning tokeni qabul qilinadi.", f"bot:{row['id']}", bot_id=row["id"])


@answer("retoken")
async def a_retoken(m: Message, state, d, bot):
    tok = (m.text or "").strip()
    try:
        await m.delete()
    except Exception:
        pass
    if not TOKEN_RE.fullmatch(tok):
        return await m.answer("❌ Token formati noto'g'ri.")
    try:
        me = await mgr.make_bot(tok).get_me()
    except Exception:
        return await m.answer("❌ Token ishlamayapti.")
    if me.id != d["bot_id"]:
        return await m.answer("❌ Bu boshqa botning tokeni. Faqat shu botning tokenini yuboring.")
    db.update_bot(me.id, token=tok, active=1)
    await mgr.start_bot(db.get_bot(me.id))
    await state.clear()
    await m.answer("✅ Token yangilandi, bot qayta ishga tushdi.", reply_markup=M([B("⬅️ Bot", f"bot:{me.id}")]))


@router.callback_query(F.data.startswith("bd:"))
async def cb_del_ask(c: CallbackQuery, is_admin):
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await show(c, f"🗑 <b>@{esc(row['username'])}</b> va uning barcha sozlamalari, foydalanuvchilari butunlay o'chiriladi.\n"
                  "Sotib olingan limitlar qaytarilmaydi. Davom etamizmi?",
               M([B("✅ Ha, o'chirish", f"bdo:{row['id']}"), B("⬅️ Yo'q", f"bot:{row['id']}")]))


@router.callback_query(F.data.startswith("bdo:"))
async def cb_del(c: CallbackQuery, is_admin):
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await mgr.stop_bot(row["id"])
    db.delete_bot(row["id"])
    await c.answer("O'chirildi")
    await show(c, "🗑 Bot o'chirildi.", M([B("📂 Botlarim", "my:0"), B("⬅️ Menyu", "home")]))


# ============================== do'kon ==============================
@router.callback_query(F.data == "shop")
async def cb_shop_pick(c: CallbackQuery, state: FSMContext, me, is_admin):
    await state.clear()
    bots = db.bots_of(c.from_user.id)
    if not bots:
        return await show(c, "🛒 Do'kon botlaringiz uchun limit sotadi. Avval bot yarating.",
                          M([B("➕ Bot yaratish", "new"), B("⬅️ Menyu", "home")]))
    if len(bots) == 1:
        return await show(c, *scr_shop(bots[0], me))
    rows = [[B(f"@{b['username']}", f"shop:{b['id']}")] for b in bots[:20]]
    rows.append([B("⬅️ Menyu", "home")])
    await show(c, "🛒 Qaysi bot uchun xarid qilasiz?", M(*rows))


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(c: CallbackQuery, state: FSMContext, me, is_admin):
    await state.clear()
    row = owned(c, is_admin)
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await show(c, *scr_shop(row, me))


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery, me, is_admin):
    p = c.data.split(":")
    row = owned(c, is_admin)
    if not row or len(p) < 4 or not p[3].isdigit():
        return await c.answer("Topilmadi", show_alert=True)
    st, cost = db.buy(c.from_user.id, row["id"], p[2], int(p[3]))
    msgs = {"cap": "Maksimal limitga yetgan.", "unavailable": "Bu mahsulot hozir sotuvda yo'q.",
            "nofunds": f"Mablag' yetarli emas (kerak: {db.money(cost)}). Hamyonni to'ldiring.", "nobot": "Bot topilmadi."}
    if st != "ok":
        return await c.answer(msgs.get(st, "Xatolik"), show_alert=True)
    await c.answer(f"✅ Sotib olindi (-{db.money(cost)})")
    await show(c, *scr_shop(row, me))


# ============================== hamyon (Stars) ==============================
@router.callback_query(F.data == "wal")
async def cb_wallet(c: CallbackQuery, state: FSMContext, me):
    await state.clear()
    await show(c, *scr_wallet(me))


async def send_topup(bot, uid, stars):
    rate = db.get_s("coins_per_star")
    name, sym = db.cur()
    await bot.send_invoice(chat_id=uid, title=f"{stars * rate} {name}",
                           description=f"Hamyonni to'ldirish: {stars} ⭐ → {stars * rate} {sym}",
                           payload=f"topup:{uid}:{stars}", currency="XTR",
                           prices=[LabeledPrice(label=name, amount=stars)])


@router.callback_query(F.data.startswith("tp:"))
async def cb_topup(c: CallbackQuery, state: FSMContext, bot):
    arg = c.data.split(":")[1]
    if arg == "c":
        mx = db.get_s("topup_max")
        return await ask(c, state, "topup", f"✍️ Necha ⭐ bilan to'ldirasiz? (1–{mx})", "wal")
    if not arg.isdigit() or not 1 <= int(arg) <= db.get_s("topup_max"):
        return await c.answer("Noto'g'ri miqdor", show_alert=True)
    await c.answer()
    await send_topup(bot, c.from_user.id, int(arg))


@answer("topup")
async def a_topup(m: Message, state, d, bot):
    try:
        stars = util.parse_int(m.text, 1, db.get_s("topup_max"))
    except ValueError:
        return await m.answer(f"❌ 1 dan {db.get_s('topup_max')} gacha butun son yuboring.")
    await state.clear()
    await send_topup(bot, m.from_user.id, stars)


@dp.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery):
    ok = q.currency == "XTR" and (q.invoice_payload or "").startswith(f"topup:{q.from_user.id}:")
    await q.answer(ok=ok, error_message=None if ok else "To'lovni tasdiqlab bo'lmadi")


@router.message(F.successful_payment)
async def on_paid(m: Message, me):
    sp = m.successful_payment
    if sp.currency != "XTR":
        return
    stars = sp.total_amount
    coins = stars * db.get_s("coins_per_star")
    if db.credit_payment(sp.telegram_payment_charge_id, m.from_user.id, stars, coins):
        text, kb = scr_wallet(me)
        await m.answer(f"✅ To'lov qabul qilindi: +{db.money(coins)}\n\n" + text, reply_markup=kb)


# ============================== promokod ==============================
@router.callback_query(F.data == "promo")
async def cb_promo(c: CallbackQuery, state: FSMContext):
    await ask(c, state, "promo", "🎟 Promokodni yuboring (masalan: <code>A8BXG67H</code>):", "home")


PROMO_MSG = {
    "notfound": "❌ Bunday promokod topilmadi.", "inactive": "❌ Bu promokod o'chirilgan.",
    "notstarted": "⏳ Bu promokod hali boshlanmagan.", "expired": "⌛️ Promokod muddati tugagan.",
    "exhausted": "😕 Promokod ishlatilish limitiga yetgan.", "already": "ℹ️ Siz bu promokodni allaqachon ishlatgansiz.",
}


@answer("promo")
async def a_promo(m: Message, state, d, bot):
    code = re.sub(r"[^A-Za-z0-9]", "", (m.text or "")).upper()
    st, reward = db.promo_redeem(code, m.from_user.id) if code else ("notfound", 0)
    if st != "ok":
        return await m.answer(PROMO_MSG.get(st, "❌ Xatolik") + "\nBoshqa kod yuboring yoki bekor qiling.")
    await state.clear()
    me = db.get_user(m.from_user.id)
    await m.answer(f"🎉 Promokod qabul qilindi: <b>+{db.money(reward)}</b>\n💰 Balans: {db.money(me['balance'])}",
                   reply_markup=M([B("🛒 Do'kon", "shop"), B("⬅️ Menyu", "home")]))
