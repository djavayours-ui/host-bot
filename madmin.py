"""Asosiy admin paneli (faqat ADMIN_IDS): sozlamalar, do'kon narxlari, promokodlar, foydalanuvchilar, botlar, xabar."""
import asyncio
import re
import secrets
from types import SimpleNamespace

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import config
import db
import util
from child import B, M, cb_int, show
from master import AdminCbF, admin_router, answer, ask, can_manage, scr_bot
from util import esc

# (kalit, nom, tur, minimal)
GROUPS = {
    "price": ("🛒 Do'kon narxlari (1 ta uchun)", [
        ("price_inline", "🔘 Inline tugma", "int", 0),
        ("price_reply", "⌨️ Pastki tugma", "int", 0),
        ("price_channel", "📢 Majburiy kanal", "int", 0)]),
    "limit": ("📏 Limitlar", [
        ("base_inline", "🔘 Inline: standart (har xabarga)", "int", 0),
        ("cap_inline", "🔘 Inline: maksimal", "int", 1),
        ("base_reply", "⌨️ Pastki tugma: standart", "int", 0),
        ("cap_reply", "⌨️ Pastki tugma: maksimal", "int", 1),
        ("base_channels", "📢 Kanal: standart", "int", 0),
        ("cap_channels", "📢 Kanal: maksimal", "int", 1),
        ("max_bots", "🤖 Foydalanuvchi yarata oladigan botlar", "int", 1)]),
    "cur": ("💱 Valyuta", [
        ("currency_name", "Valyuta nomi", "str", 1),
        ("currency_symbol", "Valyuta belgisi", "str", 1),
        ("coins_per_star", "1 ⭐ = necha birlik", "int", 1),
        ("topup_packages", "To'ldirish paketlari (⭐, vergul bilan)", "pkgs", 1),
        ("topup_max", "Bir to'lov maksimumi (⭐)", "int", 1)]),
}
FIELD = {k: (label, kind, mn) for _g, (_t, items) in GROUPS.items() for k, label, kind, mn in items}


def scr_home():
    return ("🛡 <b>Admin panel</b>\nBu bo'lim faqat sizga ko'rinadi. Siz barcha yaratilgan botlarda ham yashirin adminsiz "
            "(botga kirib /admin yuboring)."), M(
        [B("📊 Statistika", "ad:stat"), B("⚙️ Sozlamalar", "ad:set")],
        [B("🎟 Promokodlar", "ad:pr:0"), B("👥 Foydalanuvchilar", "ad:us")],
        [B("🤖 Botlar", "ad:bots:0"), B("📣 Xabar yuborish", "ad:bc")],
        [B("⬅️ Menyu", "home")])


def scr_stat():
    s = db.master_stats()
    name, sym = db.cur()
    return (f"📊 <b>Statistika</b>\n👥 Foydalanuvchilar: <b>{s['users']}</b> (bugun +{s['today']}, ban {s['banned']})\n"
            f"🤖 Botlar: <b>{s['bots']}</b> (faol {s['bots_on']})\n👤 Yaratilgan botlardagi foydalanuvchilar: {s['cusers']}\n"
            f"⭐ Qabul qilingan Stars: <b>{s['stars']}</b> ({s['payments']} ta to'lov)\n"
            f"🛒 Do'konda sarflangan: {s['spent']} {sym}\n🎟 Promokodlar: {s['promos']} (ishlatilgan {s['promo_uses']})"), M(
        [B("⬅️ Admin panel", "ad")])


def scr_settings(group):
    title, items = GROUPS[group]
    lines = [f"⚙️ <b>{title}</b>\nO'zgartirish uchun tugmani bosing.\n"]
    rows = []
    for key, label, _kind, _mn in items:
        v = db.get_s(key)
        lines.append(f"• {label}: <b>{esc(v)}</b>")
        rows.append([B(f"{label}: {str(v)[:20]}", f"ad:sv:{key}")])
    if group == "price":
        lines.append("\n<i>Narx 0 bo'lsa — mahsulot sotuvda yo'q.</i>")
    rows.append([B("⬅️ Sozlamalar", "ad:set")])
    return "\n".join(lines), M(*rows)


def scr_promos(page):
    per = 8
    total = db.promo_count()
    pages = util.pages(total, per)
    page = min(max(page, 0), pages - 1)
    _n, sym = db.cur()
    rows = []
    for p in db.promo_list(page * per, per):
        uses = f"{p['used']}/{p['max_uses'] if p['max_uses'] is not None else '∞'}"
        icon = "✅" if db.promo_state(p) == "faol" else "⏸"
        rows.append([B(f"{icon} {p['code']} · {p['reward']}{sym} · {uses}", f"ad:pc:{p['code']}")])
    nav = []
    if page > 0:
        nav.append(B("◀️", f"ad:pr:{page - 1}"))
    if pages > 1:
        nav.append(B(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(B("▶️", f"ad:pr:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("➕ Yaratish", "ad:pn"), B("📤 Hammasi (.txt)", "ad:pe")])
    rows.append([B("⬅️ Admin panel", "ad")])
    return f"🎟 <b>Promokodlar</b> ({total})", M(*rows)


def scr_promo(code):
    p = db.promo_get(code)
    uses = f"{p['used']} / {p['max_uses'] if p['max_uses'] is not None else '∞'}"
    return (f"🎟 <code>{p['code']}</code>\nMukofot: <b>{db.money(p['reward'])}</b>\nIshlatilgan: {uses}\n"
            f"Boshlanish: {util.fmt_dt(p['starts']) if p['starts'] else 'darhol'}\n"
            f"Tugash: {util.fmt_dt(p['expires']) if p['expires'] else '∞ cheksiz'}\nHolat: {db.promo_state(p)}"), M(
        [B("⏸ O'chirish / ✅ Yoqish", f"ad:pt:{code}"), B("🗑 Butunlay", f"ad:pd:{code}")],
        [B("⬅️ Ro'yxat", "ad:pr:0")])


def scr_user(uid):
    u = db.get_user(uid)
    return (f"👤 <b>{esc(u['name'])}</b> {('@' + u['username']) if u['username'] else ''}\nID: <code>{uid}</code>\n"
            f"💰 Balans: <b>{db.money(u['balance'])}</b>\n🤖 Botlari: {db.count_bots(uid)}\n"
            f"Holat: {'🚫 ban' if u['banned'] else '✅ faol'}"), M(
        [B("💰 Balansni o'zgartirish", f"ad:ua:{uid}"), B("🚫 Ban / Unban", f"ad:ub:{uid}")],
        *[[B(f"🤖 @{b['username']}", f"bot:{b['id']}")] for b in db.bots_of(uid)[:10]],
        [B("⬅️ Admin panel", "ad")])


def parse_delta(text, current):
    """'+50' qo'shish, '-20' ayirish, '=100' aniq qiymat -> yangi qiymat (>=0)."""
    t = (text or "").strip().replace(" ", "")
    base = int(t.lstrip("="))
    return max(0, base if t.startswith("=") else current + base)


# ============================== yo'naltirish ==============================
KEEP = set()


@admin_router.callback_query(AdminCbF())
async def h_ad(c: CallbackQuery, state: FSMContext, bot):
    p = c.data.split(":")
    sub = p[1] if len(p) > 1 else "home"
    if sub not in KEEP:
        await state.clear()
    fn = AD.get(sub)
    if fn:
        await fn(c, p, state, bot)
    else:
        await c.answer()


async def ad_home(c, p, state, bot):
    await show(c, *scr_home())


async def ad_stat(c, p, state, bot):
    await show(c, *scr_stat())


async def ad_set(c, p, state, bot):
    rows = [[B(t, f"ad:sg:{g}")] for g, (t, _i) in GROUPS.items()]
    rows.append([B("⬅️ Admin panel", "ad")])
    await show(c, "⚙️ <b>Sozlamalar</b>\nQaysi bo'limni o'zgartiramiz?", M(*rows))


async def ad_sg(c, p, state, bot):
    if len(p) > 2 and p[2] in GROUPS:
        await show(c, *scr_settings(p[2]))


async def ad_sv(c, p, state, bot):
    key = p[2] if len(p) > 2 else ""
    if key not in FIELD:
        return await c.answer()
    label, kind, mn = FIELD[key]
    group = next(g for g, (_t, items) in GROUPS.items() if any(i[0] == key for i in items))
    hint = {"int": f"butun son (≥ {mn})", "str": "matn", "pkgs": "butun sonlar vergul bilan, masalan <code>10,50,100,500</code>"}[kind]
    await ask(c, state, "sv", f"✏️ <b>{label}</b>\nHozirgi: <b>{esc(db.get_s(key))}</b>\nYangi qiymat ({hint}):",
              f"ad:sg:{group}", key=key, group=group)


@answer("sv")
async def a_sv(m: Message, state, d, bot):
    label, kind, mn = FIELD[d["key"]]
    t = (m.text or "").strip()
    if kind == "int":
        try:
            val = util.parse_int(t, mn, 10**9)
        except ValueError:
            return await m.answer(f"❌ {mn} yoki undan katta butun son yuboring.")
    elif kind == "pkgs":
        parts = [x.strip() for x in t.split(",") if x.strip()]
        if not parts or not all(x.isdigit() and 1 <= int(x) <= db.get_s("topup_max") for x in parts):
            return await m.answer(f"❌ 1–{db.get_s('topup_max')} oralig'ida sonlarni vergul bilan yuboring.")
        val = ",".join(str(int(x)) for x in parts[:8])
    else:
        if not 1 <= len(t) <= 20:
            return await m.answer("❌ 1–20 belgi bo'lsin.")
        val = t
    db.set_s(d["key"], val)
    await state.clear()
    text, kb = scr_settings(d["group"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


# ---------------- promokodlar ----------------
async def ad_pr(c, p, state, bot):
    await show(c, *scr_promos(cb_int(c.data, 2)))


async def ad_pc(c, p, state, bot):
    if len(p) > 2 and db.promo_get(p[2]):
        await show(c, *scr_promo(p[2]))
    else:
        await c.answer("Topilmadi", show_alert=True)


async def ad_pt(c, p, state, bot):
    if len(p) > 2 and db.promo_get(p[2]):
        db.promo_toggle(p[2])
        await show(c, *scr_promo(p[2]))


async def ad_pd(c, p, state, bot):
    if len(p) > 2:
        db.promo_delete(p[2])
        await c.answer("O'chirildi")
        await show(c, *scr_promos(0))


async def ad_pe(c, p, state, bot):
    rows = db.promo_list(0, 100000)
    lines = [f"{r['code']}\t{r['reward']}\t{r['used']}/{r['max_uses'] if r['max_uses'] is not None else '∞'}\t"
             f"{util.fmt_dt(r['expires']) if r['expires'] else '∞'}\t{db.promo_state(r)}" for r in rows]
    await c.answer()
    await c.message.answer_document(BufferedInputFile(("\n".join(lines) or "bo'sh").encode(), filename="promokodlar.txt"))


async def ad_pn(c, p, state, bot):
    await ask(c, state, "pr_reward", "🎟 <b>Yangi promokod</b>\n1/5. Har bir kod nechta birlik beradi? (butun son)", "ad:pr:0")


@answer("pr_reward")
async def a_pr_reward(m, state, d, bot):
    try:
        reward = util.parse_int(m.text, 1, 10**9)
    except ValueError:
        return await m.answer("❌ 1 yoki undan katta butun son yuboring.")
    await ask(m, state, "pr_qty", "2/5. Nechta kod yaratilsin? (1–500)\nYoki o'zingiz kod yozing (A–Z, 0–9, 4–16 belgi) — "
                                  "bitta kod yaratiladi. Avtomatik kodlar <code>A8BXG67H</code> ko'rinishida (8 belgi).",
              "ad:pr:0", reward=reward)


@answer("pr_qty")
async def a_pr_qty(m, state, d, bot):
    t = (m.text or "").strip()
    custom = None
    if t.isdigit():
        qty = int(t)
        if not 1 <= qty <= 500:
            return await m.answer("❌ 1–500 oralig'ida bo'lsin.")
    elif re.fullmatch(r"[A-Za-z0-9]{4,16}", t):
        qty, custom = 1, t.upper()
        if db.promo_get(custom):
            return await m.answer("❌ Bunday kod allaqachon bor.")
    else:
        return await m.answer("❌ Son (1–500) yoki kod (A–Z, 0–9, 4–16 belgi) yuboring.")
    await ask(m, state, "pr_uses", "3/5. Har bir kod necha marta ishlatilsin? Butun son yoki <code>0</code> / <code>inf</code> — cheksiz.\n"
                                   "(Har foydalanuvchi bitta kodni faqat 1 marta ishlata oladi.)", "ad:pr:0",
              reward=d["reward"], qty=qty, custom=custom)


@answer("pr_uses")
async def a_pr_uses(m, state, d, bot):
    t = (m.text or "").strip().lower()
    if t in util.INF_WORDS:
        uses = None
    else:
        try:
            uses = util.parse_int(t, 1, 10**9)
        except ValueError:
            return await m.answer("❌ Butun son yoki 0 / inf yuboring.")
    await ask(m, state, "pr_expiry", "4/5. Amal qilish muddati: kun soni (masalan <code>7</code>), sana (<code>31.12.2026</code> "
                                     "yoki <code>2026-12-31 23:59</code>) yoki <code>0</code> — cheksiz.", "ad:pr:0",
              reward=d["reward"], qty=d["qty"], custom=d.get("custom"), uses=uses, uses_inf=uses is None)


@answer("pr_expiry")
async def a_pr_expiry(m, state, d, bot):
    try:
        exp = util.parse_when(m.text)
    except ValueError:
        return await m.answer("❌ Sana formati noto'g'ri. Masalan: <code>7</code>, <code>31.12.2026</code> yoki <code>0</code>.")
    await ask(m, state, "pr_start", "5/5. Qachondan boshlansin? <code>0</code> — darhol, yoki kun soni / sana (yuqoridagi formatda).",
              "ad:pr:0", reward=d["reward"], qty=d["qty"], custom=d.get("custom"), uses=d.get("uses"), expires=exp)


@answer("pr_start")
async def a_pr_start(m, state, d, bot):
    try:
        start = util.parse_when(m.text)
    except ValueError:
        return await m.answer("❌ Sana formati noto'g'ri.")
    exp = d.get("expires")
    if start and exp and start >= exp:
        return await m.answer("❌ Boshlanish sanasi tugash sanasidan oldin bo'lishi kerak.")
    batch = secrets.token_hex(4)
    codes = []
    if d.get("custom"):
        if db.promo_create(d["custom"], d["reward"], d.get("uses"), start, exp, batch):
            codes.append(d["custom"])
    else:
        guard = 0
        while len(codes) < d["qty"] and guard < d["qty"] * 20:
            guard += 1
            code = util.gen_code(8)
            if db.promo_create(code, d["reward"], d.get("uses"), start, exp, batch):
                codes.append(code)
    await state.clear()
    if not codes:
        return await m.answer("❌ Kod yaratib bo'lmadi (band bo'lishi mumkin).", reply_markup=M([B("⬅️ Promokodlar", "ad:pr:0")]))
    uses = d.get("uses")
    head = (f"✅ <b>{len(codes)}</b> ta promokod yaratildi.\nMukofot: {db.money(d['reward'])} · ishlatish: "
            f"{uses if uses is not None else '∞'} · tugash: {util.fmt_dt(exp) if exp else '∞'} · "
            f"boshlanish: {util.fmt_dt(start) if start else 'darhol'}")
    kb = M([B("🎟 Promokodlar", "ad:pr:0")])
    if len(codes) <= 30:
        await m.answer(head + "\n\n" + "\n".join(f"<code>{c}</code>" for c in codes), reply_markup=kb)
    else:
        await m.answer(head, reply_markup=kb)
        await m.answer_document(BufferedInputFile("\n".join(codes).encode(), filename="promokodlar.txt"))


# ---------------- foydalanuvchilar ----------------
async def ad_us(c, p, state, bot):
    await ask(c, state, "us_id", "👥 Foydalanuvchi Telegram ID raqamini yuboring (u asosiy botga /start bosgan bo'lsin):", "ad")


@answer("us_id")
async def a_us_id(m, state, d, bot):
    try:
        uid = util.parse_int(m.text, 1)
    except ValueError:
        return await m.answer("❌ ID raqam yuboring.")
    if not db.get_user(uid):
        return await m.answer("❌ Bunday foydalanuvchi topilmadi.")
    await state.clear()
    text, kb = scr_user(uid)
    await m.answer(text, reply_markup=kb)


async def ad_u(c, p, state, bot):
    uid = cb_int(c.data, 2)
    if db.get_user(uid):
        await show(c, *scr_user(uid))


async def ad_ub(c, p, state, bot):
    uid = cb_int(c.data, 2)
    u = db.get_user(uid)
    if not u:
        return await c.answer("Topilmadi", show_alert=True)
    if uid in config.ADMIN_IDS:
        return await c.answer("Adminni ban qilib bo'lmaydi", show_alert=True)
    db.set_ban(uid, not u["banned"])
    await show(c, *scr_user(uid))


async def ad_ua(c, p, state, bot):
    uid = cb_int(c.data, 2)
    u = db.get_user(uid)
    if u:
        await ask(c, state, "ua", f"💰 Hozirgi balans: <b>{u['balance']}</b>\nMiqdor: <code>+50</code> qo'shish, "
                                  "<code>-20</code> ayirish, <code>=100</code> aniq qiymat.", f"ad:u:{uid}", uid=uid)


@answer("ua")
async def a_ua(m, state, d, bot):
    u = db.get_user(d["uid"])
    try:
        nb = parse_delta(m.text, u["balance"])
    except ValueError:
        return await m.answer("❌ Masalan: <code>+50</code>, <code>-20</code>, <code>=100</code>")
    db.add_balance(d["uid"], nb - u["balance"])
    await state.clear()
    text, kb = scr_user(d["uid"])
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


# ---------------- botlar ----------------
async def ad_bots(c, p, state, bot):
    page = cb_int(c.data, 2)
    per = 10
    total = db.count_bots()
    pages = util.pages(total, per)
    page = min(max(page, 0), pages - 1)
    rows = [[B(f"{'🟢' if b['active'] else '⏸'} @{b['username']} · {db.count_cusers(b['id'])}👥", f"bot:{b['id']}")]
            for b in db.all_bots(page * per, per)]
    nav = []
    if page > 0:
        nav.append(B("◀️", f"ad:bots:{page - 1}"))
    if pages > 1:
        nav.append(B(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(B("▶️", f"ad:bots:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("⬅️ Admin panel", "ad")])
    await show(c, f"🤖 <b>Barcha botlar</b> ({total})", M(*rows))


async def ad_gr(c, p, state, bot):
    row = db.get_bot(cb_int(c.data, 2))
    if not row:
        return await c.answer("Topilmadi", show_alert=True)
    await ask(c, state, "gr", f"🎁 @{esc(row['username'])} uchun bepul slot.\nFormat: <code>inline 3</code>, <code>reply 2</code>, "
                              "<code>channel 1</code> (manfiy son — ayirish).", f"bot:{row['id']}", bot_id=row["id"])


@answer("gr")
async def a_gr(m, state, d, bot):
    mt = re.fullmatch(r"(inline|reply|channel)\s+(-?\d{1,4})", (m.text or "").strip().lower())
    if not mt:
        return await m.answer("❌ Masalan: <code>inline 3</code>")
    col = db.ITEMS[mt.group(1)]["col"]
    row = db.get_bot(d["bot_id"])
    db.update_bot(row["id"], **{col: max(0, row[col] + int(mt.group(2)))})
    await state.clear()
    text, kb = scr_bot(row, True)
    await m.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


# ---------------- xabar yuborish (asosiy bot foydalanuvchilariga) ----------------
BCAST = {"on": False}
_TASKS = set()


async def ad_bc(c, p, state, bot):
    if BCAST["on"]:
        return await c.answer("Xabar yuborish davom etmoqda", show_alert=True)
    await ask(c, state, "bc", "📣 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (har qanday turda).", "ad")


@answer("bc")
async def a_bc(m, state, d, bot):
    n = len(db.user_ids())
    await state.set_state(None)
    await state.update_data(act=None, src_chat=m.chat.id, src_msg=m.message_id)
    await m.answer(f"📣 Xabar <b>{n}</b> ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
                   reply_markup=M([B("✅ Yuborish", "ad:bcgo")], [B("❌ Bekor qilish", "ad")]))


KEEP.add("bcgo")


async def ad_bcgo(c, p, state, bot):
    d = await state.get_data()
    if not d.get("src_msg"):
        return await c.answer("Seans tugagan", show_alert=True)
    if BCAST["on"]:
        return await c.answer("Allaqachon yuborilmoqda", show_alert=True)
    await state.clear()
    await c.answer("Boshlandi")
    status = await c.message.edit_text("⏳ Yuborilmoqda...")
    BCAST["on"] = True
    t = asyncio.create_task(run_broadcast(bot, d["src_chat"], d["src_msg"], status))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def run_broadcast(bot, src_chat, src_msg, status):
    ids = db.user_ids()
    ok = bad = 0
    try:
        for i, uid in enumerate(ids, 1):
            for _ in (1, 2):
                try:
                    await bot.copy_message(uid, src_chat, src_msg)
                    ok += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                except TelegramForbiddenError:
                    db.mark_inactive(uid)
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
        BCAST["on"] = False
        try:
            await status.edit_text(f"✅ Tugadi.\n📨 Yetkazildi: <b>{ok}</b>\n🚫 Yetmadi: {bad}")
        except Exception:
            pass


AD = {"home": ad_home, "stat": ad_stat, "set": ad_set, "sg": ad_sg, "sv": ad_sv, "pr": ad_pr, "pc": ad_pc, "pt": ad_pt,
      "pd": ad_pd, "pe": ad_pe, "pn": ad_pn, "us": ad_us, "u": ad_u, "ub": ad_ub, "ua": ad_ua, "bots": ad_bots,
      "gr": ad_gr, "bc": ad_bc, "bcgo": ad_bcgo}
