"""Kino Bot shabloni — Telegram qismi: qidiruv, yuborish, baza kanali indeksi va admin bo'limi (`k:` tugmalari).

Mantiq va indeks — kino_core.py. Fayllar baza kanalida turadi, foydalanuvchiga copy_message bilan
(«forward» belgisisiz, kanal nomi/muallif izisiz) uzatiladi; sarlavhani egasi o'zi belgilaydi.
"""
import logging
from urllib.parse import quote

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

import child as ch
import db
import kino_core as kc
from util import esc

log = logging.getLogger("builder.kino")
B, M = ch.B, ch.M
MEDIA = ("video", "document", "animation")
PICK_PER_PAGE = 30


def media_of(m):
    """(tur, file_id) — video/fayl/GIF bo'lsa, aks holda (None, None)."""
    for a in MEDIA:
        obj = getattr(m, a, None)
        if obj:
            return a, obj.file_id
    return None, None


# ============================== foydalanuvchi qismi ==============================
def caption_for(cbot, t, n, total):
    cap = f"🎬 <b>{esc(t['title'])}</b>"
    if total > 1:
        cap += f" · {n}-qism"
    cap += f"\n🆔 Kod: <code>{t['code']}</code>"
    if cbot["kino_footer"]:
        cap += "\n\n" + cbot["kino_footer"]
    return cap[:1024]


def share_kb(cbot, t, n, total):
    link = f"https://t.me/{cbot['username']}?start=k{t['code']}"
    rows = [[B("📤 Ulashish", url="https://t.me/share/url?url=" + quote(link, safe=""))]]
    if total > 1:
        nav = []
        if n > 1:
            nav.append(B("◀️", f"kp:{t['code']}:{n - 1}"))
        nav.append(B(f"{n}/{total}", f"kv:{t['code']}:{(n - 1) // PICK_PER_PAGE}"))
        if n < total:
            nav.append(B("▶️", f"kp:{t['code']}:{n + 1}"))
        rows.insert(0, nav)
    return M(*rows)


def _gone(e):
    low = str(e).lower()
    return any(s in low for s in ("message to copy not found", "message_id_invalid", "message can't be copied",
                                  "message to forward not found"))


async def send_part(bot, cbot, chat_id, t, n):
    """Bitta qismni baza kanalidan nusxalaydi. True — yuborildi."""
    ids = kc.parts(cbot["id"], t["tag"])
    if not ids:
        await bot.send_message(chat_id, "😔 Bu kinoning fayli topilmadi.")
        return False
    n = min(max(n, 1), len(ids))
    kw = dict(chat_id=chat_id, from_chat_id=cbot["kino_chat"], message_id=ids[n - 1],
              protect_content=bool(cbot["kino_protect"]), reply_markup=share_kb(cbot, t, n, len(ids)))
    try:
        try:
            await bot.copy_message(caption=caption_for(cbot, t, n, len(ids)), **kw)
        except TelegramBadRequest as e:
            if _gone(e):
                raise
            await bot.copy_message(**kw)  # sarlavha qabul qilinmadi (masalan, footer HTML xato): sarlavhasiz
    except TelegramBadRequest as e:
        if _gone(e):
            kc.delete_post(cbot["id"], ids[n - 1])
            await bot.send_message(chat_id, "😔 Bu fayl kanaldan o'chirilgan.")
            return False
        raise
    return True


async def pick_parts(bot, cbot, chat_id, t, page=0, edit=None):
    ids = kc.parts(cbot["id"], t["tag"])
    pages = max(1, -(-len(ids) // PICK_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    nums = list(range(page * PICK_PER_PAGE + 1, min(len(ids), (page + 1) * PICK_PER_PAGE) + 1))
    rows = [[B(str(n), f"kp:{t['code']}:{n}:1") for n in nums[i:i + 5]] for i in range(0, len(nums), 5)]
    nav = []
    if page > 0:
        nav.append(B("◀️", f"kv:{t['code']}:{page - 1}"))
    if page < pages - 1:
        nav.append(B("▶️", f"kv:{t['code']}:{page + 1}"))
    if nav:
        rows.append(nav)
    text = f"🎬 <b>{esc(t['title'])}</b>\n🆔 Kod: <code>{t['code']}</code> · {len(ids)} qism\n\nQismni tanlang:"
    if edit is not None:
        try:
            return await edit.edit_text(text, reply_markup=M(*rows))
        except TelegramBadRequest:
            pass
    await bot.send_message(chat_id, text, reply_markup=M(*rows))


async def send_title(bot, cbot, chat_id, t, part=None):
    ids = kc.parts(cbot["id"], t["tag"])
    if not ids:
        return await bot.send_message(chat_id, "😔 Bu kinoning fayli topilmadi.")
    if len(ids) > 1 and part is None:
        return await pick_parts(bot, cbot, chat_id, t)
    if await send_part(bot, cbot, chat_id, t, part or 1):
        kc.bump_views(cbot["id"], t["tag"])


def results_kb(rows):
    return M(*[[B(f"🎬 {r['title']}"[:56] + f" · #{r['code']}", f"kv:{r['code']}")] for r in rows])


async def search(m: Message, bot, cbot, text):
    rows = kc.search(cbot["id"], text)
    if not rows:
        return await m.answer("😔 Hech narsa topilmadi.\nKino nomini boshqacha yozing yoki kodini yuboring. "
                              "/top va /new buyruqlarini ham sinab ko'ring.")
    if len(rows) == 1 or rows[0]["search"] == kc.norm(text):
        return await send_title(bot, cbot, m.chat.id, rows[0])
    await m.answer(f"🔎 <b>{len(rows)} ta natija</b> — kerakli kinoni tanlang:", reply_markup=results_kb(rows))


async def lists(m: Message, cbot, name):
    """/top, /new, /random — Kino botda tayyor buyruqlar."""
    bot = m.bot
    if name == "random":
        t = kc.random_title(cbot["id"])
        if not t:
            return await m.answer("😔 Hozircha kino qo'shilmagan.")
        return await send_title(bot, cbot, m.chat.id, t)
    rows = kc.top(cbot["id"]) if name == "top" else kc.latest(cbot["id"])
    title = "🔥 <b>Mashhur kinolar</b>" if name == "top" else "🆕 <b>Yangi kinolar</b>"
    if name == "top" and not rows:
        rows, title = kc.latest(cbot["id"]), "🆕 <b>Yangi kinolar</b>"
    if not rows:
        return await m.answer("😔 Hozircha kino qo'shilmagan.")
    await m.answer(title, reply_markup=results_kb(rows))


async def deep_link(m: Message, bot, cbot, code):
    """/start k<kod> — ulashilgan havola orqali kinoni ochish. True — kino yuborildi."""
    t = kc.title_by_code(cbot["id"], code)
    if not t:
        return False
    await send_title(bot, cbot, m.chat.id, t)
    return True


async def _user_cb_gate(c: CallbackQuery, bot, cbot, admin):
    if admin:
        return True
    miss = await ch.missing_channels(bot, cbot, c.from_user.id)
    if miss:
        await c.answer("❗️ Avval kanallarga obuna bo'ling", show_alert=True)
        await ch.send_gate(bot, cbot, c.from_user, c.message.chat.id, miss)
        return False
    return True


@ch.user_r.callback_query(F.data.startswith("kv:"))
async def cb_view(c: CallbackQuery, bot, cbot, admin):
    if cbot["kind"] != "kino" or not await _user_cb_gate(c, bot, cbot, admin):
        return await c.answer()
    p = c.data.split(":")
    t = kc.title_by_code(cbot["id"], ch.cb_int(c.data, 1))
    if not t:
        return await c.answer("Kino topilmadi", show_alert=True)
    await c.answer()
    if len(kc.parts(cbot["id"], t["tag"])) > 1:
        if len(p) > 2:  # sahifa almashtirish yoki «n/total» tugmasi: ro'yxatni tahrirlaymiz yoki yangisini yuboramiz
            return await pick_parts(bot, cbot, c.message.chat.id, t, ch.cb_int(c.data, 2), edit=c.message)
    await send_title(bot, cbot, c.message.chat.id, t)


@ch.user_r.callback_query(F.data.startswith("kp:"))
async def cb_part(c: CallbackQuery, bot, cbot, admin):
    if cbot["kind"] != "kino" or not await _user_cb_gate(c, bot, cbot, admin):
        return await c.answer()
    t = kc.title_by_code(cbot["id"], ch.cb_int(c.data, 1))
    if not t:
        return await c.answer("Kino topilmadi", show_alert=True)
    await c.answer()
    n = ch.cb_int(c.data, 2, 1)
    if await send_part(bot, cbot, c.message.chat.id, t, n) and ch.cb_int(c.data, 3) == 1:
        kc.bump_views(cbot["id"], t["tag"])  # ko'rish faqat qism ro'yxatidan tanlanganda sanaladi


# ============================== baza kanali: yangi postlarni avto-indekslash ==============================
@ch.user_r.channel_post()
async def h_channel_post(p: Message, cbot):
    if cbot["kind"] != "kino" or not cbot["kino_chat"] or p.chat.id != cbot["kino_chat"]:
        return
    kind, _fid = media_of(p)
    tag = kc.first_tag(p.caption)
    if not kind or not tag:
        return
    kc.add_post(cbot["id"], p.message_id, tag, kind)


# ============================== admin bo'limi (k:...) ==============================
def scr_k_home(cbot, note=""):
    cbot = db.get_bot(cbot["id"])
    titles, posts, views = kc.counts(cbot["id"])
    chan = esc(cbot["kino_title"] or cbot["kino_chat"]) if cbot["kino_chat"] else "❗️ ulanmagan"
    footer = esc(cbot["kino_footer"][:60]) if cbot["kino_footer"] else "—"
    text = (f"{note}🎬 <b>Kino bazasi</b>\n📡 Baza kanali: <b>{chan}</b>\n"
            f"🎞 Kinolar: <b>{titles}</b> · 📎 Postlar: <b>{posts}</b> · 👁 Ko'rishlar: <b>{views}</b>\n"
            f"🔒 Nusxalash/forwarddan himoya: <b>{'yoqilgan' if cbot['kino_protect'] else 'o‘chiq'}</b>\n"
            f"✏️ Fayl ostidagi matn: {footer}\n\n"
            "ℹ️ Kanalga tashlangan fayl sarlavhasining <b>birinchi hashtagi</b> kino nomi bo'lsin: "
            "<code>#kino_nomi</code>. Bir xil hashtagli postlar bitta kinoning qismlari bo'ladi.")
    return text, M(
        [B("➕ Kino qo'shish", "k:add"), B("📋 Kinolar", "k:l:0")],
        [B("📡 Baza kanali", "k:ch"), B("🔁 Eski postlarni indekslash", "k:sync")],
        [B("🔒 Himoya: " + ("o'chirish" if cbot["kino_protect"] else "yoqish"), "k:prot"),
         B("✏️ Fayl ostidagi matn", "k:ft")],
        [B("⬅️ Orqaga", "a:home")])


def scr_k_title(cbot, t, note=""):
    ids = kc.parts(cbot["id"], t["tag"])
    link = f"https://t.me/{cbot['username']}?start=k{t['code']}"
    text = (f"{note}🎬 <b>{esc(t['title'])}</b>\n🆔 Kod: <code>{t['code']}</code> · Hashtag: <code>{esc(t['tag'])}</code>\n"
            f"📎 Qismlar: {len(ids)} · 👁 Ko'rishlar: {t['views']}\n🔗 <code>{esc(link)}</code>")
    return text, M(
        [B("➕ Qism qo'shish", f"k:addp:{t['code']}")],
        [B("🗑 O'chirish", f"k:del:{t['code']}")],
        [B("⬅️ Orqaga", "k:l:0")])


def scr_k_list(cbot, page):
    total = kc.counts(cbot["id"])[0]
    pages = max(1, -(-total // 8))
    page = min(max(page, 0), pages - 1)
    rows = [[B(f"#{t['code']} · {t['title']}"[:56], f"k:v:{t['code']}")] for t in kc.list_titles(cbot["id"], page * 8, 8)]
    nav = []
    if page > 0:
        nav.append(B("◀️", f"k:l:{page - 1}"))
    if pages > 1:
        nav.append(B(f"{page + 1}/{pages}", "k:l:%d" % page))
    if page < pages - 1:
        nav.append(B("▶️", f"k:l:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("⬅️ Orqaga", "k:home")])
    return (f"📋 <b>Kinolar</b> ({total})" if total else "📋 Hali kino yo'q. «➕ Kino qo'shish» ni bosing."), M(*rows)


async def r_k(x):
    if x.cbot["kind"] != "kino":
        return await x.c.answer("Bu bot Kino shabloni emas", show_alert=True)
    sub = x.p[1] if len(x.p) > 1 else "home"
    cbot = x.cbot
    if sub == "home":
        return await ch.show(x.c, *scr_k_home(cbot))
    if sub == "l":
        return await ch.show(x.c, *scr_k_list(cbot, ch.cb_int(x.c.data, 2)))
    if sub == "v":
        t = kc.title_by_code(cbot["id"], ch.cb_int(x.c.data, 2))
        return await ch.show(x.c, *scr_k_title(cbot, t)) if t else await x.c.answer("Topilmadi", show_alert=True)
    if sub == "ch":
        warn = ("\n\n⚠️ Boshqa kanal ulansa, hozirgi indeks tozalanadi." if cbot["kino_chat"] else "")
        return await ch.ask(x.c, x.state, "k_ch",
                            "📡 Baza kanalini yuboring: @username, -100... ID yoki kanaldan bitta post forward qiling.\n"
                            "Bot kanalda <b>administrator</b> bo'lishi (xabar yuborish va o'chirish huquqi bilan) shart. "
                            "Kanalni yopiq (private) qilib qo'ying — fayllar faqat bot orqali tarqalsin." + warn, "k:home")
    if sub in ("add", "addp"):
        if not cbot["kino_chat"]:
            return await x.c.answer("Avval baza kanalini ulang", show_alert=True)
        code = ch.cb_int(x.c.data, 2) if sub == "addp" else 0
        t = kc.title_by_code(cbot["id"], code) if code else None
        if sub == "addp" and not t:
            return await x.c.answer("Kino topilmadi", show_alert=True)
        prompt = (f"📎 «{esc(t['title'])}» ga yangi qism — video yoki faylni yuboring."
                  if t else "🎬 Kinoni yuboring (video, fayl yoki GIF). Keyin nomini so'rayman.")
        return await ch.ask(x.c, x.state, "k_add", prompt, f"k:v:{code}" if t else "k:home", code=code)
    if sub == "del":
        t = kc.title_by_code(cbot["id"], ch.cb_int(x.c.data, 2))
        if not t:
            return await x.c.answer("Topilmadi", show_alert=True)
        return await ch.show(x.c, f"🗑 «{esc(t['title'])}» ni o'chiramizmi?", M(
            [B("🗑 Faqat botdan", f"k:deldo:{t['code']}:0")],
            [B("🗑 Botdan va kanaldan", f"k:deldo:{t['code']}:1")],
            [B("⬅️ Yo'q", f"k:v:{t['code']}")]))
    if sub == "deldo":
        t = kc.title_by_code(cbot["id"], ch.cb_int(x.c.data, 2))
        if not t:
            return await x.c.answer("Topilmadi", show_alert=True)
        ids = kc.delete_title(cbot["id"], t["tag"])
        if ch.cb_int(x.c.data, 3) == 1 and cbot["kino_chat"]:
            for mid in ids:
                try:
                    await x.bot.delete_message(cbot["kino_chat"], mid)
                except Exception:
                    pass
        await x.c.answer("O'chirildi")
        return await ch.show(x.c, *scr_k_list(cbot, 0))
    if sub == "prot":
        db.update_bot(cbot["id"], kino_protect=0 if cbot["kino_protect"] else 1)
        return await ch.show(x.c, *scr_k_home(cbot))
    if sub == "ft":
        return await ch.ask(x.c, x.state, "k_footer",
                            "✏️ Har bir fayl ostiga qo'shiladigan matnni yuboring (masalan, kanalingiz havolasi; "
                            "formatlash saqlanadi). O'chirish uchun <code>-</code> yuboring.", "k:home")
    if sub == "sync":
        if not cbot["kino_chat"]:
            return await x.c.answer("Avval baza kanalini ulang", show_alert=True)
        return await ch.ask(x.c, x.state, "k_sync",
                            "🔁 Baza kanalidagi eski postlarni shu yerga <b>forward</b> qiling (bir nechtasini birdan "
                            "belgilab yuborsa bo'ladi). Faqat sarlavhasida <code>#kino_nomi</code> hashtagi bor "
                            "video/fayllar indekslanadi. Tugagach «✅ Tugatish» ni bosing.", "k:home", n=0, skipped=0)
    await x.c.answer()


@ch.answer("k_ch")
async def a_k_ch(m, state, d, bot, cbot):
    ref = ch.chat_ref(m)
    if ref is None:
        return await ch.say(m, d, "❌ Tushunmadim. @username, -100... ID yuboring yoki kanaldan post forward qiling.")
    try:
        chat = await bot.get_chat(ref)
    except Exception:
        return await ch.say(m, d, "❌ Kanal topilmadi. Botni kanalga administrator qilib qo'shganingizni tekshiring.")
    if getattr(chat.type, "value", chat.type) not in ("channel", "supergroup"):
        return await ch.say(m, d, "❌ Bu kanal emas.")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        st = getattr(me.status, "value", me.status)
        ok = st == "creator" or (st == "administrator" and getattr(me, "can_post_messages", True) is not False)
    except Exception:
        ok = False
    if not ok:
        return await ch.say(m, d, "❌ Bot bu kanalda administrator emas yoki xabar yuborish huquqi yo'q.")
    old = db.get_bot(cbot["id"])
    if old["kino_chat"] and old["kino_chat"] != chat.id:
        kc.clear_index(cbot["id"])
    db.update_bot(cbot["id"], kino_chat=chat.id, kino_title=chat.title)
    await state.clear()
    text, kb = scr_k_home(cbot, "✅ Baza kanali ulandi. Endi kanalga tashlangan yangi postlar o'zi indekslanadi; "
                                "eskilari uchun «🔁 Eski postlarni indekslash» dan foydalaning.\n\n")
    await ch.say(m, d, text, reply_markup=kb)


async def _post(m, state, d, bot, cbot, title, kind, file_id):
    tag = kc.tag_of(title)
    code = kc.peek_code(cbot["id"], tag)
    cap = f"{tag}\n\n🎬 <b>{esc(title)}</b>\n🆔 Kod: <code>{code}</code>"
    try:
        sent = await getattr(bot, "send_" + kind)(cbot["kino_chat"], file_id, caption=cap)
    except Exception as e:
        return await ch.say(m, d, f"❌ Kanalga yuborib bo'lmadi: {esc(str(e)[:150])}\n"
                                  "Bot kanalda administrator va xabar yuborish huquqiga egami?")
    res = kc.add_post(cbot["id"], sent.message_id, tag, kind, title=title, code=code)
    await state.clear()
    t = kc.title_by_tag(cbot["id"], tag)
    part = f" · {res[2]}-qism" if res and not res[1] else ""
    text, kb = scr_k_title(cbot, t, f"✅ Kanalga joylandi{part}.\n\n")
    await ch.say(m, d, text, reply_markup=kb)


@ch.answer("k_add")
async def a_k_add(m, state, d, bot, cbot):
    cbot = db.get_bot(cbot["id"])
    kind, fid = media_of(m)
    if not kind:
        return await ch.say(m, d, "❌ Video, fayl (document) yoki GIF yuboring.")
    if not cbot["kino_chat"]:
        await state.clear()
        return await ch.say(m, d, "⚠️ Avval baza kanalini ulang.")
    if d.get("code"):
        t = kc.title_by_code(cbot["id"], d["code"])
        if not t:
            await state.clear()
            return await ch.say(m, d, "⚠️ Kino topilmadi.")
        return await _post(m, state, d, bot, cbot, t["title"], kind, fid)
    await ch.ask(m, state, "k_name", "✏️ Kino nomini yuboring (2–100 belgi). Hashtag nomdan o'zi yasaladi: "
                                     "«Tez va Jahldor 5» → <code>#tez_va_jahldor_5</code>.\n"
                                     "Shu nom bilan kino bor bo'lsa, fayl uning yangi qismi bo'ladi.",
                 "k:home", kind=kind, file_id=fid)


@ch.answer("k_name")
async def a_k_name(m, state, d, bot, cbot):
    title = (m.text or "").strip()
    if not 2 <= len(title) <= 100 or not kc.tag_of(title) or len(kc.norm(title)) < 2:
        return await ch.say(m, d, "❌ Nom 2–100 belgi bo'lsin va harf yoki raqam bo'lsin.")
    await _post(m, state, d, bot, db.get_bot(cbot["id"]), title, d["kind"], d["file_id"])


@ch.answer("k_footer")
async def a_k_footer(m, state, d, bot, cbot):
    if not m.text:
        return await ch.say(m, d, "❌ Matn yuboring.")
    v = "" if m.text.strip() == "-" else m.html_text
    if len(v) > 300:
        return await ch.say(m, d, "❌ Juda uzun (maks. 300 belgi).")
    db.update_bot(cbot["id"], kino_footer=v)
    await state.clear()
    text, kb = scr_k_home(cbot, "✅ Saqlandi.\n\n")
    await ch.say(m, d, text, reply_markup=kb)


@ch.answer("k_sync")
async def a_k_sync(m, state, d, bot, cbot):
    cbot = db.get_bot(cbot["id"])
    done_kb = M([B("✅ Tugatish", "x:k:home")])
    fo = getattr(m, "forward_origin", None)
    n, skipped = d.get("n", 0), d.get("skipped", 0)
    if fo is None or getattr(fo, "type", None) != "channel" or fo.chat.id != cbot["kino_chat"]:
        return await ch.say(m, d, f"ℹ️ Faqat baza kanalidan forward qilingan postlar qabul qilinadi.\n"
                                  f"Indekslandi: {n} · o'tkazib yuborildi: {skipped}", reply_markup=done_kb)
    kind, _fid = media_of(m)
    tag = kc.first_tag(m.caption)
    if kind and tag and kc.add_post(cbot["id"], fo.message_id, tag, kind):
        n += 1
    else:
        skipped += 1  # hashtag/fayl yo'q yoki allaqachon indekslangan
    await state.update_data(n=n, skipped=skipped)
    await ch.say(m, d, f"🔁 Indekslandi: <b>{n}</b> · o'tkazib yuborildi: {skipped}\nYana forward qiling yoki tugating.",
                 reply_markup=done_kb)


def stat_line(cbot):
    titles, posts, views = kc.counts(cbot["id"])
    return f"\n🎬 Kinolar: {titles} · Postlar: {posts} · 👁 Ko'rishlar: {views}"


ch.PREFIXES.add("k")
ch.ROUTES["k"] = r_k
