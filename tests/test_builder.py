import importlib.util
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.getcwd())
import config  # noqa: E402
import db  # noqa: E402
import util  # noqa: E402

HAS_AIOGRAM = bool(importlib.util.find_spec("aiogram"))


class User:
    def __init__(self, uid, name="Ali Valiyev", username="ali"):
        self.id, self.full_name, self.username = uid, name, username
        self.first_name, self.last_name = name.split()[0], "Valiyev"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DATA_DIR = self.tmp.name
        db.init()
        db.upsert_user(User(1))

    def tearDown(self):
        self.tmp.cleanup()

    def mkbot(self, bid=100, owner=1):
        return db.create_bot_with_defaults(bid, owner, f"{bid}:T", f"bot{bid}", f"Bot {bid}")


class TestUtil(unittest.TestCase):
    def test_render_variables(self):
        ctx = util.make_ctx({"name": "My <Bot>", "username": "mybot"}, User(7), balance=15, users=3)
        out = util.render("Salom %name%! %nickname% bal=%balance% id=%id% %xyz% 50% off", ctx)
        self.assertIn('<a href="tg://user?id=7">Ali</a>', out)
        self.assertIn("@ali", out)
        self.assertIn("bal=15", out)
        self.assertIn("%xyz%", out)           # noma'lum o'zgaruvchi o'zgarmaydi
        self.assertIn("50% off", out)
        self.assertRegex(util.render("%time%", ctx), r"^\d\d:\d\d$")
        self.assertEqual(util.render("%botname%", ctx), "My &lt;Bot&gt;")  # HTML xavfsiz
        self.assertEqual(util.render("%name%", ctx, "plain"), "Ali")

    def test_gen_code_format(self):
        for _ in range(50):
            self.assertRegex(util.gen_code(), r"^[A-Z0-9]{8}$")
        self.assertEqual(len({util.gen_code() for _ in range(200)}), 200)

    def test_parse_when(self):
        self.assertIsNone(util.parse_when("0"))
        self.assertIsNone(util.parse_when("inf"))
        self.assertAlmostEqual(util.parse_when("7"), time.time() + 7 * 86400, delta=5)
        self.assertGreater(util.parse_when("31.12.2099"), time.time())
        self.assertGreater(util.parse_when("2099-12-31 23:59"), time.time())
        with self.assertRaises(ValueError):
            util.parse_when("kecha")

    def test_parse_int(self):
        self.assertEqual(util.parse_int(" 12 ", 1, 20), 12)
        with self.assertRaises(ValueError):
            util.parse_int("x")
        with self.assertRaises(ValueError):
            util.parse_int("99", 1, 20)


class TestDb(Base):
    def test_default_limits(self):
        b = self.mkbot()
        self.assertEqual(db.limits(b), {"inline": 2, "reply": 6, "channel": 1})
        self.assertIsNotNone(b["start_cid"])
        self.assertIsNotNone(b["sub_cid"])

    def test_shop_purchase_flow(self):
        b = self.mkbot()
        self.assertEqual(db.buy(1, 100, "inline", 1)[0], "nofunds")
        db.add_balance(1, 1000)
        st, cost = db.buy(1, 100, "inline", 5)
        self.assertEqual((st, cost), ("ok", 25))
        self.assertEqual(db.limit(db.get_bot(100), "inline"), 7)
        self.assertEqual(db.get_user(1)["balance"], 975)
        # maksimal 20 dan oshmaydi
        self.assertEqual(db.buy(1, 100, "inline", 14)[0], "cap")
        self.assertEqual(db.buy(1, 100, "inline", 13)[0], "ok")
        self.assertEqual(db.limit(db.get_bot(100), "inline"), 20)
        self.assertEqual(db.buy(1, 100, "inline", 1)[0], "cap")
        db.set_s("price_channel", 0)
        self.assertEqual(db.buy(1, 100, "channel", 1)[0], "unavailable")
        self.assertEqual(db.buy(1, 999, "reply", 1)[0], "nobot")

    def test_promo_flow(self):
        db.upsert_user(User(2))
        self.assertTrue(db.promo_create("A8BXG67H", 50, 1, None, None, "b"))
        self.assertFalse(db.promo_create("A8BXG67H", 50, 1, None, None, "b"))
        self.assertEqual(db.promo_redeem("A8BXG67H", 1), ("ok", 50))
        self.assertEqual(db.get_user(1)["balance"], 50)
        self.assertEqual(db.promo_redeem("A8BXG67H", 1)[0], "exhausted")      # 1 marta ishlatilishi mumkin edi
        self.assertEqual(db.promo_redeem("A8BXG67H", 2)[0], "exhausted")
        db.promo_create("INFINITE", 10, None, None, None, "b")                # cheksiz
        self.assertEqual(db.promo_redeem("INFINITE", 1)[0], "ok")
        self.assertEqual(db.promo_redeem("INFINITE", 1)[0], "already")        # bir foydalanuvchi — bir marta
        self.assertEqual(db.promo_redeem("INFINITE", 2)[0], "ok")
        db.promo_create("OLDCODE1", 5, None, None, time.time() - 10, "b")
        self.assertEqual(db.promo_redeem("OLDCODE1", 2)[0], "expired")
        db.promo_create("FUTURE01", 5, None, time.time() + 1000, None, "b")
        self.assertEqual(db.promo_redeem("FUTURE01", 2)[0], "notstarted")
        db.promo_toggle("INFINITE")
        self.assertEqual(db.promo_redeem("INFINITE", 99)[0], "inactive")
        self.assertEqual(db.promo_redeem("NOPE", 2)[0], "notfound")

    def test_payment_idempotent(self):
        self.assertTrue(db.credit_payment("ch1", 1, 10, 10))
        self.assertFalse(db.credit_payment("ch1", 1, 10, 10))
        self.assertEqual(db.get_user(1)["balance"], 10)

    def test_button_layout(self):
        self.mkbot()
        cid = db.get_bot(100)["start_cid"]
        a = db.add_ibutton(cid, "A", "popup", "x", "", True)
        b = db.add_ibutton(cid, "B", "popup", "x", "", False)   # yonma-yon
        c = db.add_ibutton(cid, "C", "popup", "x", "", True)    # yangi qator
        rows = {x["id"]: (x["rw"], x["pos"]) for x in db.ibuttons(cid)}
        self.assertEqual((rows[a], rows[b], rows[c]), ((0, 0), (0, 1), (1, 0)))

    def test_delete_bot_cascades(self):
        b = self.mkbot()
        c = db.add_content(100, dict(type="text", html="x"))
        db.add_rbutton(100, "Menu", "", c)
        db.add_ibutton(b["start_cid"], "A", "popup", "x", "")
        db.add_channel(100, -1001, "T", "t", "https://t.me/t")
        db.delete_bot(100)
        with db.conn() as cn:
            for t in ("contents", "rbuttons", "channels", "cusers"):
                self.assertEqual(cn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0)
            self.assertEqual(cn.execute("SELECT COUNT(*) FROM ibuttons").fetchone()[0], 0)

    def test_channels_commands_cusers(self):
        self.mkbot()
        self.assertTrue(db.add_channel(100, -1001, "T", "t", "u"))
        self.assertIsNone(db.add_channel(100, -1001, "T", "t", "u"))
        cid = db.add_content(100, dict(type="text", html="x"))
        self.assertTrue(db.add_command(100, "help", cid))
        self.assertIsNone(db.add_command(100, "help", cid))
        u = User(55)
        self.assertEqual(db.cuser_touch(100, u)["balance"], 0)
        self.assertEqual(db.change_cbalance(100, 55, "add", 30), 30)
        self.assertEqual(db.change_cbalance(100, 55, "add", -100), 0)
        self.assertEqual(db.change_cbalance(100, 55, "set", 7), 7)
        self.assertIsNone(db.change_cbalance(100, 56, "add", 1))
        self.assertEqual(db.cstats(100)["total"], 1)

    def test_settings_roundtrip(self):
        db.set_s("price_inline", 9)
        self.assertEqual(db.get_s("price_inline"), 9)
        db.set_s("topup_packages", "5, 20,abc,999999")
        self.assertEqual(db.packages(), [5, 20])


@unittest.skipUnless(HAS_AIOGRAM, "aiogram o'rnatilmagan")
class TestContent(Base):
    def test_inline_kinds_and_styles(self):
        import content as ct
        cbot = self.mkbot()
        ctx = util.make_ctx(cbot, User(5), 3, 1)
        cid = cbot["start_cid"]
        mk = lambda kind, val, style="": ct.make_ibutton(  # noqa: E731
            {"id": 1, "text": "T %balance%", "kind": kind, "value": val, "style": style}, cbot, ctx)
        self.assertEqual(mk("url", "https://x.uz/?u=%id%").url, "https://x.uz/?u=5")
        self.assertIsNone(mk("url", "ftp://bad"))
        self.assertEqual(mk("copy", "kod").copy_text.text, "kod")
        self.assertEqual(mk("popup", "hi").callback_data, "i:1")
        self.assertEqual(mk("command", "start").callback_data, "i:1")
        self.assertIn("t.me/share/url", mk("forward", "").url)
        self.assertEqual(mk("inline", "q").switch_inline_query, "q")
        self.assertEqual(mk("inline_here", "q").switch_inline_query_current_chat, "q")
        self.assertEqual(mk("webapp", "https://a.b/c").web_app.url, "https://a.b/c")
        self.assertEqual(mk("popup", "x", "danger").style, "danger")
        self.assertFalse(hasattr(mk("popup", "x", ""), "style") and mk("popup", "x", "").style)
        self.assertEqual(mk("popup", "x").text, "T 3")
        db.add_ibutton(cid, "A", "popup", "x", "success")
        self.assertIsNotNone(ct.inline_markup(cbot, cid, ctx))

    def test_reply_markup(self):
        import content as ct
        cbot = self.mkbot()
        self.assertIsNone(ct.reply_markup(100))
        c = db.add_content(100, dict(type="text", html="x"))
        db.add_rbutton(100, "Menu", "primary", c)
        self.assertIsNotNone(ct.reply_markup(100))

    def test_check_value_and_chat_ref(self):
        import child
        self.assertEqual(child.check_value("url", "https://a.uz")[1], None)
        self.assertIsNotNone(child.check_value("url", "a.uz")[1])
        self.assertIsNotNone(child.check_value("copy", "x" * 300)[1])
        self.assertEqual(child.check_value("forward", "-"), ("", None))
        self.assertIsNotNone(child.check_value("webapp", "http://a.uz")[1])

        class M:
            forward_origin = None
            text = "https://t.me/mychannel"
        self.assertEqual(child.chat_ref(M()), "@mychannel")
        M.text = "-1001234567890"
        self.assertEqual(child.chat_ref(M()), -1001234567890)
        M.text = "hello"
        self.assertIsNone(child.chat_ref(M()))

    def test_storage_roundtrip(self):
        import asyncio
        from aiogram.fsm.storage.base import StorageKey
        from storage import SqliteStorage

        async def go():
            st = SqliteStorage()
            k = StorageKey(bot_id=1, chat_id=2, user_id=3)
            await st.update_data(k, {"a": [1, 2], "b": {"x": "y"}})
            await st.set_state(k, "S:ask")
            self.assertEqual(await st.get_state(k), "S:ask")
            self.assertEqual((await st.get_data(k))["b"], {"x": "y"})
            await st.set_state(k, None)
            await st.set_data(k, {})
            self.assertEqual(await st.get_data(k), {})
        asyncio.run(go())

    def test_admin_routes_complete(self):
        import child
        for p in child.PREFIXES:
            self.assertIn(p, child.ROUTES, p)
        self.assertEqual(set(child.ROUTES) - child.PREFIXES, set())


if __name__ == "__main__":
    unittest.main()
