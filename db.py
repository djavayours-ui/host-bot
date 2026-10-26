"""SQLite: barcha jadvallar va so'rovlar. Har chaqiriqda qisqa ulanish (WAL rejimi)."""
import os
import sqlite3
import time
from contextlib import contextmanager

import config
import util


def path():
    return os.path.join(config.DATA_DIR, "builder.db")


@contextmanager
def conn(tx=False):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    c = sqlite3.connect(path(), timeout=20, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        if tx:
            c.execute("BEGIN IMMEDIATE")
        yield c
        if tx:
            c.execute("COMMIT")
    except BaseException:
        if tx:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, balance INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, joined REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS bots(
    id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, token TEXT NOT NULL, username TEXT, name TEXT,
    active INTEGER NOT NULL DEFAULT 1, created REAL,
    extra_inline INTEGER NOT NULL DEFAULT 0, extra_reply INTEGER NOT NULL DEFAULT 0,
    extra_channels INTEGER NOT NULL DEFAULT 0,
    start_cid INTEGER, sub_cid INTEGER,
    join_text TEXT NOT NULL DEFAULT '📢 %channel%', check_text TEXT NOT NULL DEFAULT '✅ Tekshirish',
    join_style TEXT NOT NULL DEFAULT '', check_style TEXT NOT NULL DEFAULT 'success');
CREATE INDEX IF NOT EXISTS bots_owner ON bots(owner_id);
CREATE TABLE IF NOT EXISTS contents(
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, type TEXT NOT NULL, html TEXT NOT NULL DEFAULT '',
    file_id TEXT, file_uid TEXT, spoiler INTEGER NOT NULL DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS ibuttons(
    id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER NOT NULL, rw INTEGER NOT NULL DEFAULT 0,
    pos INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ib_content ON ibuttons(content_id);
CREATE TABLE IF NOT EXISTS rbuttons(
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, rw INTEGER NOT NULL DEFAULT 0,
    pos INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL, style TEXT NOT NULL DEFAULT '', content_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS rb_bot ON rbuttons(bot_id);
CREATE TABLE IF NOT EXISTS commands(
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, name TEXT NOT NULL, content_id INTEGER NOT NULL,
    UNIQUE(bot_id, name));
CREATE TABLE IF NOT EXISTS channels(
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, title TEXT,
    username TEXT, url TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ch_bot ON channels(bot_id);
CREATE TABLE IF NOT EXISTS cusers(
    bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT, username TEXT,
    balance INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    joined REAL, last_seen REAL, PRIMARY KEY(bot_id, user_id));
CREATE TABLE IF NOT EXISTS promos(
    code TEXT PRIMARY KEY, reward INTEGER NOT NULL, max_uses INTEGER, used INTEGER NOT NULL DEFAULT 0,
    starts REAL, expires REAL, active INTEGER NOT NULL DEFAULT 1, created REAL, batch TEXT);
CREATE TABLE IF NOT EXISTS promo_uses(code TEXT NOT NULL, user_id INTEGER NOT NULL, at REAL NOT NULL,
    PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS payments(charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, stars INTEGER NOT NULL,
    coins INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    bot_id INTEGER NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL, cost INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fsm(key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT '{}', updated REAL NOT NULL);
"""


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.execute("DELETE FROM fsm WHERE updated < ?", (time.time() - 7 * 86400,))
    _CACHE.clear()


# ----------------------------- sozlamalar -----------------------------
DEFAULTS = {
    "currency_name": "Tanga", "currency_symbol": "🪙", "coins_per_star": 1,
    "price_inline": 5, "price_reply": 3, "price_channel": 10,
    "base_inline": 2, "base_reply": 6, "base_channels": 1,
    "cap_inline": 20, "cap_reply": 50, "cap_channels": 10,
    "max_bots": 3, "topup_packages": "10,50,100,500", "topup_max": 10000,
}
_CACHE = {}


def get_s(key):
    if key in _CACHE:
        return _CACHE[key]
    d = DEFAULTS[key]
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    v = d
    if r is not None:
        try:
            v = int(r["value"]) if isinstance(d, int) else r["value"]
        except ValueError:
            v = d
    _CACHE[key] = v
    return v


def set_s(key, val):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(val)))
    _CACHE.pop(key, None)


def cur():
    """(valyuta nomi, belgisi)"""
    return get_s("currency_name"), get_s("currency_symbol")


def money(n):
    return f"{n} {get_s('currency_symbol')}"


def packages():
    out = []
    for p in str(get_s("topup_packages")).split(","):
        p = p.strip()
        if p.isdigit() and 1 <= int(p) <= get_s("topup_max"):
            out.append(int(p))
    return out


# Do'kon mahsulotlari: bot darajasida limitni oshiradi
ITEMS = {
    "inline": dict(col="extra_inline", price="price_inline", base="base_inline", cap="cap_inline",
                   title="🔘 Inline tugma (har bir xabarga)"),
    "reply": dict(col="extra_reply", price="price_reply", base="base_reply", cap="cap_reply",
                  title="⌨️ Pastki tugma"),
    "channel": dict(col="extra_channels", price="price_channel", base="base_channels", cap="cap_channels",
                    title="📢 Majburiy obuna kanali"),
}


def limit(b, item):
    it = ITEMS[item]
    return min(get_s(it["cap"]), get_s(it["base"]) + b[it["col"]])


def limits(b):
    return {k: limit(b, k) for k in ITEMS}


def buy(user_id, bot_id, item, qty):
    """Atomik xarid. (holat, narx): ok | nofunds | cap | unavailable | nobot"""
    it = ITEMS.get(item)
    if not it or qty < 1:
        return "unavailable", 0
    price = get_s(it["price"])
    cap, base = get_s(it["cap"]), get_s(it["base"])
    if price <= 0:
        return "unavailable", 0
    with conn(tx=True) as c:
        b = c.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if not b:
            return "nobot", 0
        if base + b[it["col"]] + qty > cap:
            return "cap", 0
        cost = price * qty
        u = c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not u or u["balance"] < cost:
            return "nofunds", cost
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (cost, user_id))
        c.execute(f"UPDATE bots SET {it['col']}={it['col']}+? WHERE id=?", (qty, bot_id))
        c.execute("INSERT INTO purchases(user_id,bot_id,item,qty,cost,at) VALUES(?,?,?,?,?,?)",
                  (user_id, bot_id, item, qty, cost, time.time()))
        return "ok", cost


# ----------------------------- asosiy bot foydalanuvchilari -----------------------------
def upsert_user(u):
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT INTO users(user_id,name,username,joined,last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username, "
            "last_seen=excluded.last_seen, active=1",
            (u.id, (u.full_name or "")[:64], u.username, now, now))
        return c.execute("SELECT * FROM users WHERE user_id=?", (u.id,)).fetchone()


def get_user(uid):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def set_ban(uid, flag):
    with conn() as c:
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if flag else 0, uid))


def add_balance(uid, delta):
    """Balansni o'zgartiradi (0 dan pastga tushmaydi). Yangi balans yoki None (foydalanuvchi yo'q)."""
    with conn(tx=True) as c:
        u = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u:
            return None
        nb = max(0, u["balance"] + delta)
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (nb, uid))
        return nb


def user_ids(active_only=True):
    with conn() as c:
        q = "SELECT user_id FROM users" + (" WHERE active=1 AND banned=0" if active_only else "")
        return [r[0] for r in c.execute(q)]


def mark_inactive(uid):
    with conn() as c:
        c.execute("UPDATE users SET active=0 WHERE user_id=?", (uid,))


def credit_payment(charge_id, uid, stars, coins):
    """To'lovni bir marta hisoblaydi. True — yangi to'lov."""
    with conn(tx=True) as c:
        try:
            c.execute("INSERT INTO payments(charge_id,user_id,stars,coins,at) VALUES(?,?,?,?,?)",
                      (charge_id, uid, stars, coins, time.time()))
        except sqlite3.IntegrityError:
            return False
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (coins, uid))
        return True


def master_stats():
    with conn() as c:
        q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731
        day = util.midnight_ts()
        return {
            "users": q("SELECT COUNT(*) FROM users"), "today": q("SELECT COUNT(*) FROM users WHERE joined>=?", day),
            "banned": q("SELECT COUNT(*) FROM users WHERE banned=1"),
            "bots": q("SELECT COUNT(*) FROM bots"), "bots_on": q("SELECT COUNT(*) FROM bots WHERE active=1"),
            "cusers": q("SELECT COUNT(*) FROM cusers"),
            "stars": q("SELECT COALESCE(SUM(stars),0) FROM payments"), "payments": q("SELECT COUNT(*) FROM payments"),
            "spent": q("SELECT COALESCE(SUM(cost),0) FROM purchases"),
            "promos": q("SELECT COUNT(*) FROM promos"), "promo_uses": q("SELECT COUNT(*) FROM promo_uses"),
        }


# ----------------------------- yaratilgan botlar -----------------------------
def add_bot(bid, owner, token, username, name):
    with conn() as c:
        c.execute("INSERT INTO bots(id,owner_id,token,username,name,created) VALUES(?,?,?,?,?,?)",
                  (bid, owner, token, username, name, time.time()))


def get_bot(bid):
    with conn() as c:
        return c.execute("SELECT * FROM bots WHERE id=?", (bid,)).fetchone()


def bots_of(owner):
    with conn() as c:
        return c.execute("SELECT * FROM bots WHERE owner_id=? ORDER BY created", (owner,)).fetchall()


def all_bots(offset=0, limit=1000):
    with conn() as c:
        return c.execute("SELECT * FROM bots ORDER BY created LIMIT ? OFFSET ?", (limit, offset)).fetchall()


def count_bots(owner=None):
    with conn() as c:
        if owner is None:
            return c.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM bots WHERE owner_id=?", (owner,)).fetchone()[0]


_BOT_FIELDS = {"token", "username", "name", "active", "extra_inline", "extra_reply", "extra_channels", "start_cid",
               "sub_cid", "join_text", "check_text", "join_style", "check_style"}


def update_bot(bid, **f):
    f = {k: v for k, v in f.items() if k in _BOT_FIELDS}
    if not f:
        return
    with conn() as c:
        c.execute(f"UPDATE bots SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), bid))


def delete_bot(bid):
    with conn(tx=True) as c:
        cids = [r[0] for r in c.execute("SELECT id FROM contents WHERE bot_id=?", (bid,))]
        for cid in cids:
            c.execute("DELETE FROM ibuttons WHERE content_id=?", (cid,))
        for t in ("contents", "rbuttons", "commands", "channels", "cusers"):
            c.execute(f"DELETE FROM {t} WHERE bot_id=?", (bid,))
        c.execute("DELETE FROM bots WHERE id=?", (bid,))


def create_bot_with_defaults(bid, owner, token, username, name):
    """Botni va standart /start, majburiy obuna xabarlarini yaratadi."""
    add_bot(bid, owner, token, username, name)
    start = add_content(bid, dict(type="text", html="👋 Salom, %name%!\n\nBotimizga xush kelibsiz.", file_id=None,
                                  file_uid=None, spoiler=0))
    sub = add_content(bid, dict(type="text", html="❗️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling "
                                                  "va «Tekshirish» tugmasini bosing:", file_id=None, file_uid=None,
                                spoiler=0))
    update_bot(bid, start_cid=start, sub_cid=sub)
    return get_bot(bid)


# ----------------------------- xabarlar (kontent) va inline tugmalar -----------------------------
def add_content(bot_id, d):
    with conn() as c:
        cur_ = c.execute("INSERT INTO contents(bot_id,type,html,file_id,file_uid,spoiler,created) VALUES(?,?,?,?,?,?,?)",
                         (bot_id, d["type"], d.get("html", ""), d.get("file_id"), d.get("file_uid"),
                          int(d.get("spoiler", 0)), time.time()))
        return cur_.lastrowid


def get_content(cid):
    with conn() as c:
        return c.execute("SELECT * FROM contents WHERE id=?", (cid,)).fetchone()


def replace_content(cid, d):
    with conn() as c:
        c.execute("UPDATE contents SET type=?, html=?, file_id=?, file_uid=?, spoiler=? WHERE id=?",
                  (d["type"], d.get("html", ""), d.get("file_id"), d.get("file_uid"), int(d.get("spoiler", 0)), cid))


def delete_content(cid):
    with conn() as c:
        c.execute("DELETE FROM ibuttons WHERE content_id=?", (cid,))
        c.execute("DELETE FROM contents WHERE id=?", (cid,))


def _place(c, table, col, val, new_row, maxn):
    rows = c.execute(f"SELECT rw, COUNT(*) n FROM {table} WHERE {col}=? GROUP BY rw ORDER BY rw", (val,)).fetchall()
    if not rows:
        return 0, 0
    last = rows[-1]
    if new_row or last["n"] >= maxn:
        return last["rw"] + 1, 0
    return last["rw"], last["n"]


def ibuttons(cid):
    with conn() as c:
        return c.execute("SELECT * FROM ibuttons WHERE content_id=? ORDER BY rw, pos, id", (cid,)).fetchall()


def count_ibuttons(cid):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM ibuttons WHERE content_id=?", (cid,)).fetchone()[0]


def add_ibutton(cid, text, kind, value, style, new_row=True):
    with conn(tx=True) as c:
        rw, pos = _place(c, "ibuttons", "content_id", cid, new_row, 8)
        return c.execute("INSERT INTO ibuttons(content_id,rw,pos,text,kind,value,style) VALUES(?,?,?,?,?,?,?)",
                         (cid, rw, pos, text, kind, value, style)).lastrowid


def get_ibutton(iid):
    with conn() as c:
        return c.execute("SELECT * FROM ibuttons WHERE id=?", (iid,)).fetchone()


def update_ibutton(iid, **f):
    f = {k: v for k, v in f.items() if k in ("text", "kind", "value", "style")}
    if f:
        with conn() as c:
            c.execute(f"UPDATE ibuttons SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), iid))


def del_ibutton(iid):
    with conn() as c:
        c.execute("DELETE FROM ibuttons WHERE id=?", (iid,))


# ----------------------------- pastki (reply) tugmalar, buyruqlar -----------------------------
def rbuttons(bot_id):
    with conn() as c:
        return c.execute("SELECT * FROM rbuttons WHERE bot_id=? ORDER BY rw, pos, id", (bot_id,)).fetchall()


def get_rbutton(rid):
    with conn() as c:
        return c.execute("SELECT * FROM rbuttons WHERE id=?", (rid,)).fetchone()


def rbutton_by_text(bot_id, text):
    with conn() as c:
        return c.execute("SELECT * FROM rbuttons WHERE bot_id=? AND text=?", (bot_id, text)).fetchone()


def add_rbutton(bot_id, text, style, content_id, new_row=True):
    with conn(tx=True) as c:
        rw, pos = _place(c, "rbuttons", "bot_id", bot_id, new_row, 4)
        return c.execute("INSERT INTO rbuttons(bot_id,rw,pos,text,style,content_id) VALUES(?,?,?,?,?,?)",
                         (bot_id, rw, pos, text, style, content_id)).lastrowid


def update_rbutton(rid, **f):
    f = {k: v for k, v in f.items() if k in ("text", "style")}
    if f:
        with conn() as c:
            c.execute(f"UPDATE rbuttons SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), rid))


def del_rbutton(rid):
    r = get_rbutton(rid)
    if r:
        delete_content(r["content_id"])
        with conn() as c:
            c.execute("DELETE FROM rbuttons WHERE id=?", (rid,))


def commands(bot_id):
    with conn() as c:
        return c.execute("SELECT * FROM commands WHERE bot_id=? ORDER BY name", (bot_id,)).fetchall()


def get_command(cid):
    with conn() as c:
        return c.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()


def command_by_name(bot_id, name):
    with conn() as c:
        return c.execute("SELECT * FROM commands WHERE bot_id=? AND name=?", (bot_id, name)).fetchone()


def add_command(bot_id, name, content_id):
    with conn() as c:
        try:
            return c.execute("INSERT INTO commands(bot_id,name,content_id) VALUES(?,?,?)",
                             (bot_id, name, content_id)).lastrowid
        except sqlite3.IntegrityError:
            return None


def del_command(cid):
    r = get_command(cid)
    if r:
        delete_content(r["content_id"])
        with conn() as c:
            c.execute("DELETE FROM commands WHERE id=?", (cid,))


# ----------------------------- majburiy obuna kanallari -----------------------------
def channels(bot_id):
    with conn() as c:
        return c.execute("SELECT * FROM channels WHERE bot_id=? ORDER BY id", (bot_id,)).fetchall()


def get_channel(chid):
    with conn() as c:
        return c.execute("SELECT * FROM channels WHERE id=?", (chid,)).fetchone()


def add_channel(bot_id, chat_id, title, username, url):
    with conn() as c:
        if c.execute("SELECT 1 FROM channels WHERE bot_id=? AND chat_id=?", (bot_id, chat_id)).fetchone():
            return None
        return c.execute("INSERT INTO channels(bot_id,chat_id,title,username,url) VALUES(?,?,?,?,?)",
                         (bot_id, chat_id, title, username, url)).lastrowid


def update_channel(chid, **f):
    f = {k: v for k, v in f.items() if k in ("label", "url", "title")}
    if f:
        with conn() as c:
            c.execute(f"UPDATE channels SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), chid))


def del_channel(chid):
    with conn() as c:
        c.execute("DELETE FROM channels WHERE id=?", (chid,))


# ----------------------------- yaratilgan bot foydalanuvchilari -----------------------------
def cuser_touch(bot_id, u):
    now = time.time()
    with conn() as c:
        c.execute(
            "INSERT INTO cusers(bot_id,user_id,name,username,joined,last_seen) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(bot_id,user_id) DO UPDATE SET name=excluded.name, username=excluded.username, "
            "last_seen=excluded.last_seen, active=1",
            (bot_id, u.id, (u.full_name or "")[:64], u.username, now, now))
        return c.execute("SELECT * FROM cusers WHERE bot_id=? AND user_id=?", (bot_id, u.id)).fetchone()


def get_cuser(bot_id, uid):
    with conn() as c:
        return c.execute("SELECT * FROM cusers WHERE bot_id=? AND user_id=?", (bot_id, uid)).fetchone()


def count_cusers(bot_id):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM cusers WHERE bot_id=?", (bot_id,)).fetchone()[0]


def cuser_ids(bot_id):
    with conn() as c:
        return [r[0] for r in c.execute("SELECT user_id FROM cusers WHERE bot_id=? AND active=1 AND banned=0",
                                        (bot_id,))]


def cuser_inactive(bot_id, uid):
    with conn() as c:
        c.execute("UPDATE cusers SET active=0 WHERE bot_id=? AND user_id=?", (bot_id, uid))


def set_cban(bot_id, uid, flag):
    with conn() as c:
        return c.execute("UPDATE cusers SET banned=? WHERE bot_id=? AND user_id=?",
                         (1 if flag else 0, bot_id, uid)).rowcount


def change_cbalance(bot_id, uid, mode, amount):
    """mode: add | set. Yangi balans yoki None (foydalanuvchi shu botda yo'q)."""
    with conn(tx=True) as c:
        u = c.execute("SELECT balance FROM cusers WHERE bot_id=? AND user_id=?", (bot_id, uid)).fetchone()
        if not u:
            return None
        nb = max(0, amount if mode == "set" else u["balance"] + amount)
        c.execute("UPDATE cusers SET balance=? WHERE bot_id=? AND user_id=?", (nb, bot_id, uid))
        return nb


def cstats(bot_id):
    day = util.midnight_ts()
    with conn() as c:
        q = lambda s, *a: c.execute(s, a).fetchone()[0]  # noqa: E731
        return {
            "total": q("SELECT COUNT(*) FROM cusers WHERE bot_id=?", bot_id),
            "active": q("SELECT COUNT(*) FROM cusers WHERE bot_id=? AND active=1", bot_id),
            "banned": q("SELECT COUNT(*) FROM cusers WHERE bot_id=? AND banned=1", bot_id),
            "today": q("SELECT COUNT(*) FROM cusers WHERE bot_id=? AND joined>=?", bot_id, day),
            "week": q("SELECT COUNT(*) FROM cusers WHERE bot_id=? AND joined>=?", bot_id, time.time() - 7 * 86400),
            "online": q("SELECT COUNT(*) FROM cusers WHERE bot_id=? AND last_seen>=?", bot_id, time.time() - 86400),
        }


# ----------------------------- promokodlar -----------------------------
def promo_create(code, reward, max_uses, starts, expires, batch):
    with conn() as c:
        try:
            c.execute("INSERT INTO promos(code,reward,max_uses,starts,expires,created,batch) VALUES(?,?,?,?,?,?,?)",
                      (code, reward, max_uses, starts, expires, time.time(), batch))
            return True
        except sqlite3.IntegrityError:
            return False


def promo_get(code):
    with conn() as c:
        return c.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()


def promo_list(offset=0, limit=10):
    with conn() as c:
        return c.execute("SELECT * FROM promos ORDER BY created DESC, code LIMIT ? OFFSET ?", (limit, offset)).fetchall()


def promo_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM promos").fetchone()[0]


def promo_batch(batch):
    with conn() as c:
        return c.execute("SELECT * FROM promos WHERE batch=? ORDER BY code", (batch,)).fetchall()


def promo_toggle(code):
    with conn() as c:
        c.execute("UPDATE promos SET active=1-active WHERE code=?", (code,))


def promo_delete(code):
    with conn() as c:
        c.execute("DELETE FROM promos WHERE code=?", (code,))
        c.execute("DELETE FROM promo_uses WHERE code=?", (code,))


def promo_state(p, now=None):
    """Kod holati matni: faol | o'chirilgan | boshlanmagan | muddati o'tgan | tugagan"""
    now = now or time.time()
    if not p["active"]:
        return "o'chirilgan"
    if p["starts"] and now < p["starts"]:
        return "boshlanmagan"
    if p["expires"] and now > p["expires"]:
        return "muddati o'tgan"
    if p["max_uses"] is not None and p["used"] >= p["max_uses"]:
        return "tugagan"
    return "faol"


def promo_redeem(code, uid, now=None):
    """(holat, mukofot). holat: ok | notfound | inactive | notstarted | expired | exhausted | already"""
    now = now or time.time()
    with conn(tx=True) as c:
        p = c.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()
        if not p:
            return "notfound", 0
        st = promo_state(p, now)
        if st != "faol":
            return {"o'chirilgan": "inactive", "boshlanmagan": "notstarted", "muddati o'tgan": "expired",
                    "tugagan": "exhausted"}[st], 0
        if c.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, uid)).fetchone():
            return "already", 0
        c.execute("INSERT INTO promo_uses(code,user_id,at) VALUES(?,?,?)", (code, uid, now))
        c.execute("UPDATE promos SET used=used+1 WHERE code=?", (code,))
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (p["reward"], uid))
        return "ok", p["reward"]
