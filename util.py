"""Yordamchi funksiyalar: o'zgaruvchilar, sana, kod generatsiyasi."""
import html
import re
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import config

WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
CODE_ALPHABET = string.ascii_uppercase + string.digits

VARS_HELP = (
    "<code>%time%</code> — soat:daqiqa\n"
    "<code>%date%</code> — sana (KK.OO.YYYY)\n"
    "<code>%datetime%</code> — sana va vaqt\n"
    "<code>%weekday%</code> — hafta kuni\n"
    "<code>%name%</code> — ism (profilga havola bilan)\n"
    "<code>%nickname%</code> — @username (bo'lmasa ism)\n"
    "<code>%fullname%</code> — to'liq ism\n"
    "<code>%first_name%</code>, <code>%last_name%</code>\n"
    "<code>%username%</code> — @siz\n"
    "<code>%id%</code> — Telegram ID\n"
    "<code>%balance%</code> — foydalanuvchining shu botdagi balansi\n"
    "<code>%users%</code> — botdagi foydalanuvchilar soni\n"
    "<code>%referrals%</code> — foydalanuvchi taklif qilgan do'stlar soni\n"
    "<code>%ref_link%</code> — foydalanuvchining shaxsiy taklif havolasi\n"
    "<code>%botname%</code>, <code>%botusername%</code>\n"
    "<code>%channel%</code> — kanal nomi (faqat majburiy obuna tugmalarida)"
)

_VAR = re.compile(r"%([a-zA-Z_]+)%")


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=False)


def tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.TZ_NAME)
    except Exception:
        return timezone(timedelta(hours=5))


def now_local():
    return datetime.now(tz())


def fmt_dt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz()).strftime("%d.%m.%Y %H:%M")


def midnight_ts():
    n = now_local()
    return n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def make_ctx(cbot, user, balance=0, users=0, refs=0):
    """Xabardagi %o'zgaruvchilar% uchun qiymatlar."""
    n = now_local()
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    uname = getattr(user, "username", None)
    return {
        "time": n.strftime("%H:%M"), "date": n.strftime("%d.%m.%Y"), "datetime": n.strftime("%d.%m.%Y %H:%M"),
        "weekday": WEEKDAYS[n.weekday()],
        "first_name": first, "last_name": last, "fullname": (first + " " + last).strip(),
        "nickname": f"@{uname}" if uname else first, "username": f"@{uname}" if uname else "",
        "id": getattr(user, "id", 0), "balance": balance, "users": users,
        "referrals": refs,
        "ref_link": (f"https://t.me/{cbot['username']}?start=ref_{getattr(user, 'id', 0)}"
                     if cbot and cbot["username"] else ""),
        "botname": cbot["name"] if cbot else "", "botusername": f"@{cbot['username']}" if cbot and cbot["username"] else "",
    }


def render(text, ctx, mode="html"):
    """mode: html (xabar matni), plain (tugma matni), url (havola ichida)."""
    if not text:
        return ""

    def sub(m):
        k = m.group(1).lower()
        if k == "name":
            fn = ctx.get("first_name", "") or "do'st"
            if mode == "html":
                return f'<a href="tg://user?id={ctx.get("id", 0)}">{html.escape(fn, quote=False)}</a>'
            return quote(fn, safe="") if mode == "url" else fn
        if k not in ctx:
            return m.group(0)
        v = str(ctx[k])
        if mode == "html":
            return html.escape(v, quote=False)
        return quote(v, safe="") if mode == "url" else v

    return _VAR.sub(sub, text)


def gen_code(n=8):
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))


INF_WORDS = {"0", "inf", "infinity", "∞", "cheksiz", "yoq", "yo'q", "-", "hozir", "now"}


def parse_when(s):
    """None — cheksiz/hozir. Son — hozirdan shuncha KUN keyin. Yoki sana: 31.12.2026, 2026-12-31 23:59.
    Noto'g'ri bo'lsa ValueError."""
    s = (s or "").strip().lower()
    if s in INF_WORDS:
        return None
    if s.isdigit():
        return time.time() + int(s) * 86400
    for fmt, day_only in (("%Y-%m-%d %H:%M", False), ("%d.%m.%Y %H:%M", False),
                          ("%Y-%m-%d", True), ("%d.%m.%Y", True)):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=tz())
        except ValueError:
            continue
        if day_only:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.timestamp()
    raise ValueError("sana formati noto'g'ri")


def parse_int(s, lo=None, hi=None):
    s = (s or "").strip().replace(" ", "")
    if not re.fullmatch(r"-?\d{1,12}", s):
        raise ValueError("raqam kerak")
    v = int(s)
    if lo is not None and v < lo or hi is not None and v > hi:
        raise ValueError(f"{lo}…{hi} oralig'ida bo'lsin")
    return v


def valid_url(u):
    return bool(re.fullmatch(r"(https?://|tg://)[^\s]{3,500}", (u or "").strip()))


def pages(total, per):
    return max(1, -(-total // per))


def csv_cell(v):
    """CSV/Excel formula in'ektsiyasidan himoya: =,+,-,@ bilan boshlangan matn oldiga ' qo'yiladi."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def fmt_dur(sec):
    """Davomiylik: '5 soat 12 daqiqa' · '7 daqiqa 05 soniya' · '42 soniya'."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} soat {m:02d} daqiqa"
    if m >= 10:
        return f"{m} daqiqa"
    if m:
        return f"{m} daqiqa {s:02d} soniya"
    return f"{s} soniya"
