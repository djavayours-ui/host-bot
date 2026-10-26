"""Kino Bot shabloni — mantiqiy qism (aiogram'siz).

Baza — Telegram kanal. Har bir kino posti sarlavhasining BIRINCHI hashtagi kino nomi bo'ladi: `#kino_nomi`.
Bir xil hashtagli postlar bitta kinoning qismlari hisoblanadi (xabar ID bo'yicha tartiblanadi).
Bot Telegram'da kanal tarixini qidira olmaydi, shuning uchun postlar indeksi (xabar ID + hashtag) builder.db da
saqlanadi: kanalga kelgan yangi postlar o'zi indekslanadi, eskilari kanaldan forward qilib qo'shiladi.
Fayllarning o'zi faqat kanalda turadi va foydalanuvchiga o'sha yerdan nusxalanadi.
"""
import difflib
import re

import db

_APOS = "ʻʼ’‘`'´"


def norm(s):
    """Qidiruv uchun normallashtirish: kichik harf, apostrofsiz, belgilarsiz, bitta bo'shliq."""
    s = (s or "").lower()
    for ch in _APOS:
        s = s.replace(ch, "")
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


def tag_of(title):
    """Kino nomidan hashtag: «Tez va Jahldor 5» → #tez_va_jahldor_5 (bo'sh bo'lsa '')."""
    n = norm(title)
    return ("#" + n.replace(" ", "_")[:60]) if n else ""


def first_tag(text):
    """Matndagi birinchi hashtag (kichik harfda, # bilan) yoki ''."""
    m = re.search(r"#(\w{2,64})", text or "", re.UNICODE)
    return ("#" + m.group(1).lower()) if m else ""


def title_from_tag(tag):
    t = tag.lstrip("#").replace("_", " ").strip()
    return t[:1].upper() + t[1:]


# ----------------------------- indeks -----------------------------
def title_by_tag(bot_id, tag):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? AND tag=?", (bot_id, tag)).fetchone()


def title_by_code(bot_id, code):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? AND code=?", (bot_id, code)).fetchone()


def peek_code(bot_id, tag):
    """Shu hashtagli kinoning kodi; yangi bo'lsa — beriladigan keyingi kod."""
    t = title_by_tag(bot_id, tag)
    if t:
        return t["code"]
    with db.conn() as c:
        return c.execute("SELECT COALESCE(MAX(code),0)+1 FROM kino_titles WHERE bot_id=?", (bot_id,)).fetchone()[0]


def add_post(bot_id, msg_id, tag, kind, title=None, code=None):
    """Postni indekslaydi. (kod, yangi_kino_mi, qism_raqami) yoki None (bu post allaqachon indekslangan)."""
    import time
    with db.conn(tx=True) as c:
        if c.execute("SELECT 1 FROM kino_posts WHERE bot_id=? AND msg_id=?", (bot_id, msg_id)).fetchone():
            return None
        t = c.execute("SELECT code FROM kino_titles WHERE bot_id=? AND tag=?", (bot_id, tag)).fetchone()
        new = t is None
        if new:
            title = (title or title_from_tag(tag))[:100]
            nxt = c.execute("SELECT COALESCE(MAX(code),0)+1 FROM kino_titles WHERE bot_id=?", (bot_id,)).fetchone()[0]
            taken = code and c.execute("SELECT 1 FROM kino_titles WHERE bot_id=? AND code=?", (bot_id, code)).fetchone()
            code = code if code and not taken else nxt
            c.execute("INSERT INTO kino_titles(bot_id,tag,code,title,search,views,created) VALUES(?,?,?,?,?,0,?)",
                      (bot_id, tag, code, title, norm(title), time.time()))
        else:
            code = t["code"]
        c.execute("INSERT INTO kino_posts(bot_id,msg_id,tag,kind,at) VALUES(?,?,?,?,?)",
                  (bot_id, msg_id, tag, kind, time.time()))
        part = c.execute("SELECT COUNT(*) FROM kino_posts WHERE bot_id=? AND tag=? AND msg_id<=?",
                         (bot_id, tag, msg_id)).fetchone()[0]
    return code, new, part


def parts(bot_id, tag):
    with db.conn() as c:
        return [r[0] for r in c.execute("SELECT msg_id FROM kino_posts WHERE bot_id=? AND tag=? ORDER BY msg_id",
                                        (bot_id, tag))]


def delete_post(bot_id, msg_id):
    """Post indeksdan olib tashlanadi; qismi qolmasa kinoning o'zi ham o'chadi."""
    with db.conn(tx=True) as c:
        r = c.execute("SELECT tag FROM kino_posts WHERE bot_id=? AND msg_id=?", (bot_id, msg_id)).fetchone()
        if not r:
            return
        c.execute("DELETE FROM kino_posts WHERE bot_id=? AND msg_id=?", (bot_id, msg_id))
        if not c.execute("SELECT 1 FROM kino_posts WHERE bot_id=? AND tag=?", (bot_id, r["tag"])).fetchone():
            c.execute("DELETE FROM kino_titles WHERE bot_id=? AND tag=?", (bot_id, r["tag"]))


def delete_title(bot_id, tag):
    """Kinoni indeksdan o'chiradi, uning xabar ID'lari ro'yxatini qaytaradi."""
    ids = parts(bot_id, tag)
    with db.conn(tx=True) as c:
        c.execute("DELETE FROM kino_posts WHERE bot_id=? AND tag=?", (bot_id, tag))
        c.execute("DELETE FROM kino_titles WHERE bot_id=? AND tag=?", (bot_id, tag))
    return ids


def clear_index(bot_id):
    with db.conn(tx=True) as c:
        c.execute("DELETE FROM kino_posts WHERE bot_id=?", (bot_id,))
        c.execute("DELETE FROM kino_titles WHERE bot_id=?", (bot_id,))


def bump_views(bot_id, tag):
    with db.conn() as c:
        c.execute("UPDATE kino_titles SET views=views+1 WHERE bot_id=? AND tag=?", (bot_id, tag))


# ----------------------------- qidiruv va ro'yxatlar -----------------------------
def search(bot_id, query, limit=10):
    """Kod (raqam) yoki nom bo'yicha qidiradi: avval aniq moslik, so'ng ko'p ko'rilganlar; xato yozilsa taxminiy."""
    q = norm(query)
    if not q:
        return []
    if q.isdigit():
        r = title_by_code(bot_id, int(q))
        return [r] if r else []
    words = q.split()[:6]
    sql = "SELECT * FROM kino_titles WHERE bot_id=?" + " AND search LIKE ?" * len(words) + " LIMIT 200"
    with db.conn() as c:
        rows = c.execute(sql, (bot_id, *[f"%{w}%" for w in words])).fetchall()
        if not rows:  # imlo xatosi: eng yaqin nomlar
            allr = c.execute("SELECT * FROM kino_titles WHERE bot_id=? LIMIT 3000", (bot_id,)).fetchall()
            close = difflib.get_close_matches(q, [r["search"] for r in allr], n=limit, cutoff=0.6)
            rows = [r for r in allr if r["search"] in close]

    def rank(r):
        s = r["search"]
        return (0 if s == q else 1 if s.startswith(q) else 2, -r["views"], r["code"])
    return sorted(rows, key=rank)[:limit]


def top(bot_id, limit=10):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? AND views>0 ORDER BY views DESC, code DESC LIMIT ?",
                         (bot_id, limit)).fetchall()


def latest(bot_id, limit=10):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? ORDER BY code DESC LIMIT ?", (bot_id, limit)).fetchall()


def random_title(bot_id):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? ORDER BY RANDOM() LIMIT 1", (bot_id,)).fetchone()


def list_titles(bot_id, offset=0, limit=8):
    with db.conn() as c:
        return c.execute("SELECT * FROM kino_titles WHERE bot_id=? ORDER BY code DESC LIMIT ? OFFSET ?",
                         (bot_id, limit, offset)).fetchall()


def counts(bot_id):
    """(kinolar, postlar, ko'rishlar)"""
    with db.conn() as c:
        t = c.execute("SELECT COUNT(*), COALESCE(SUM(views),0) FROM kino_titles WHERE bot_id=?", (bot_id,)).fetchone()
        p = c.execute("SELECT COUNT(*) FROM kino_posts WHERE bot_id=?", (bot_id,)).fetchone()[0]
    return t[0], p, t[1]
