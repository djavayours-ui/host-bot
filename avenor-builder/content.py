"""Xabar kontenti (har qanday turdagi) va klaviaturalar: saqlash, o'zgaruvchilar bilan yuborish."""
from urllib.parse import quote

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup, WebAppInfo)

import db
import util

STYLES = {"": "⚪ Standart", "primary": "🔵 Ko'k", "success": "🟢 Yashil", "danger": "🔴 Qizil"}
TYPE_NAMES = {"text": "Matn", "photo": "Rasm", "video": "Video", "animation": "GIF", "document": "Fayl",
              "audio": "Audio", "voice": "Ovozli xabar", "video_note": "Video-dumaloq", "sticker": "Stiker"}
# Inline tugma turlari: (nom, qiymat so'rovi). None — qiymat so'ralmaydi
KINDS = {
    "url": ("🔗 URL havola", "Havolani yuboring (<code>https://...</code> yoki <code>tg://...</code>):"),
    "copy": ("📋 Nusxalash (copy)", "Bosilganda nusxalanadigan matnni yuboring (maks. 256 belgi):"),
    "popup": ("💬 Pop-up xabar", "Bosilganda chiqadigan ogohlantirish matnini yuboring (maks. 200 belgi):"),
    "command": ("⚡️ Buyruq / xabar ochish", None),
    "forward": ("📤 Ulashish (forward)", "Ulashiladigan matn yoki havolani yuboring "
                                         "(<code>-</code> yuborsangiz — shu botning havolasi ulashiladi):"),
    "inline": ("🔎 Inline rejim (chat tanlash)", "Inline so'rov matnini yuboring (bo'sh bo'lsa <code>-</code>). "
                                                  "Eslatma: @BotFather'da bot uchun inline mode yoqilgan bo'lsin."),
    "inline_here": ("✍️ Inline rejim (shu chatda)", "Inline so'rov matnini yuboring (bo'sh bo'lsa <code>-</code>):"),
    "webapp": ("🌐 Web App", "Web App manzilini yuboring (faqat <code>https://</code>):"),
}


def extract(m):
    """Xabardan saqlanadigan kontent: dict yoki None (qo'llab-quvvatlanmaydigan tur)."""
    html = ""
    if m.text or m.caption:
        try:
            html = m.html_text
        except TypeError:
            html = ""
    if m.text:
        return dict(type="text", html=html, file_id=None, file_uid=None, spoiler=0)
    for attr in ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker"):
        obj = getattr(m, attr, None)
        if obj:
            f = obj[-1] if attr == "photo" else obj
            return dict(type=attr, html=html, file_id=f.file_id, file_uid=f.file_unique_id,
                        spoiler=int(bool(getattr(m, "has_media_spoiler", False))))
    return None


async def send_content(bot, chat_id, c, ctx, markup=None):
    """Kontentni o'zgaruvchilarni almashtirib yuboradi."""
    text = util.render(c["html"], ctx)
    t = c["type"]
    if t == "text":
        return await bot.send_message(chat_id, text or "…", reply_markup=markup)
    fid = c["file_id"]
    if t in ("sticker", "video_note"):
        msg = await getattr(bot, "send_" + t)(chat_id, fid, reply_markup=markup)
        if text:
            await bot.send_message(chat_id, text)
        return msg
    kw = {"caption": text or None, "reply_markup": markup}
    if t in ("photo", "video", "animation"):
        kw["has_spoiler"] = bool(c["spoiler"])
    try:
        return await getattr(bot, "send_" + t)(chat_id, fid, **kw)
    except TelegramBadRequest as e:
        if "caption" in str(e).lower() and text:  # sarlavha 1024 belgidan uzun: media va matn alohida
            kw.pop("caption")
            kw["reply_markup"] = None
            msg = await getattr(bot, "send_" + t)(chat_id, fid, **{k: v for k, v in kw.items() if v is not None})
            await bot.send_message(chat_id, text, reply_markup=markup)
            return msg
        raise


def make_ibutton(b, cbot, ctx):
    """Bazadagi yozuvdan InlineKeyboardButton (yaroqsiz bo'lsa None)."""
    text = (util.render(b["text"], ctx, "plain") or "•")[:64]
    kind, val = b["kind"], b["value"] or ""
    kw = {"style": b["style"]} if b["style"] in ("primary", "success", "danger") else {}
    if kind == "url":
        u = util.render(val, ctx, "url")
        return InlineKeyboardButton(text=text, url=u, **kw) if util.valid_url(u) else None
    if kind == "copy":
        return InlineKeyboardButton(text=text, copy_text=CopyTextButton(text=util.render(val, ctx, "plain")[:256]), **kw)
    if kind in ("popup", "command"):
        return InlineKeyboardButton(text=text, callback_data=f"i:{b['id']}", **kw)
    if kind == "forward":
        me = f"https://t.me/{cbot['username']}" if cbot and cbot["username"] else ""
        if val.startswith(("http://", "https://")):
            url = "https://t.me/share/url?url=" + quote(util.render(val, ctx, "plain"), safe="")
        else:
            url = "https://t.me/share/url?url=" + quote(me, safe="") + "&text=" + quote(util.render(val, ctx, "plain"), safe="")
        return InlineKeyboardButton(text=text, url=url, **kw)
    if kind == "inline":
        return InlineKeyboardButton(text=text, switch_inline_query=val, **kw)
    if kind == "inline_here":
        return InlineKeyboardButton(text=text, switch_inline_query_current_chat=val, **kw)
    if kind == "webapp":
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=val), **kw)
    return None


def inline_markup(cbot, cid, ctx, top=None, bottom=None):
    """Kontentga biriktirilgan inline tugmalar. top/bottom — qo'shimcha tayyor qatorlar."""
    rows = {}
    for b in db.ibuttons(cid):
        btn = make_ibutton(b, cbot, ctx)
        if btn:
            rows.setdefault(b["rw"], []).append(btn)
    kb = list(top or []) + [rows[k] for k in sorted(rows)] + list(bottom or [])
    return InlineKeyboardMarkup(inline_keyboard=kb) if kb else None


def reply_markup(bot_id):
    rows = {}
    for r in db.rbuttons(bot_id):
        kw = {"style": r["style"]} if r["style"] in ("primary", "success", "danger") else {}
        rows.setdefault(r["rw"], []).append(KeyboardButton(text=r["text"][:64], **kw))
    if not rows:
        return None
    return ReplyKeyboardMarkup(keyboard=[rows[k] for k in sorted(rows)], resize_keyboard=True, is_persistent=True)
