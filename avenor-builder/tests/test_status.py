import asyncio
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.getcwd())
import config  # noqa: E402
import db  # noqa: E402
import status  # noqa: E402
import util  # noqa: E402


class Msg:
    def __init__(self, i):
        self.message_id = i


class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.n = [], [], 100

    async def send_message(self, uid, text):
        self.n += 1
        self.sent.append((uid, text))
        return Msg(self.n)

    async def edit_message_text(self, text, chat_id, message_id):
        self.edits.append((chat_id, message_id, text))


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DATA_DIR = self.tmp.name
        db.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_fmt_dur(self):
        self.assertEqual(util.fmt_dur(20700), "5 soat 45 daqiqa")
        self.assertEqual(util.fmt_dur(1500), "25 daqiqa")
        self.assertEqual(util.fmt_dur(125), "2 daqiqa 05 soniya")
        self.assertEqual(util.fmt_dur(42), "42 soniya")
        self.assertEqual(util.fmt_dur(-5), "0 soniya")

    def test_trigger_descriptions(self):
        t, lines = status.describe_trigger({"GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "abcdef1234", "GITHUB_REF_NAME": "main",
                                            "GITHUB_RUN_NUMBER": "57", "GITHUB_REPOSITORY": "a/b", "GITHUB_RUN_ID": "9"})
        self.assertIn("Rejalashtirilgan", t)
        self.assertTrue(any("abcdef1" in x for x in lines))
        self.assertTrue(any("actions/runs/9" in x for x in lines))
        t, lines = status.describe_trigger({"GITHUB_EVENT_NAME": "push", "COMMIT_MSG": "Yangi <b>funksiya</b>\nbatafsil",
                                            "GITHUB_ACTOR": "ali"})
        self.assertIn("Kod yangilandi", t)
        joined = "\n".join(lines)
        self.assertIn("Yangi &lt;b&gt;funksiya", joined)   # HTML xavfsiz
        self.assertNotIn("batafsil", joined)               # faqat birinchi qator
        self.assertIn("Qo'lda", status.describe_trigger({"GITHUB_EVENT_NAME": "workflow_dispatch"})[0])
        self.assertIn("Oddiy", status.describe_trigger({})[0])

    def test_eta_decreases(self):
        st = status.Status(None, [], 20700, env={}, started=1000.0)
        a, b = st.render(now=1000 + 60), st.render(now=1000 + 3660)
        self.assertIn("5 soat 44 daqiqa", a)
        self.assertIn("4 soat 44 daqiqa", b)
        self.assertIn("Ishlayapti: <b>1 soat 01 daqiqa</b>", b)
        self.assertIn("run bo'ldi", a)
        self.assertIn("100%", st.render(now=1000 + 99999))
        self.assertIn("0 soniya", st.render(now=1000 + 99999))
        self.assertIn("Muddatsiz", status.Status(None, [], 0, env={}, started=1.0).render(now=5))

    def test_next_delay_speeds_up(self):
        self.assertEqual([status.next_delay(x) for x in (None, 5000, 300, 60)], [300, 60, 30, 10])

    def test_lifecycle_messages(self):
        bot = FakeBot()
        db.raw_set(status.KEY, '{"7": 55}')                  # oldingi seansdan qolgan xabar
        st = status.Status(bot, {7}, 600, env={"GITHUB_EVENT_NAME": "push", "COMMIT_MSG": "fix"})

        async def go():
            await st.start("mybot", 3)
            await st.finish("signal")
        asyncio.run(go())
        self.assertEqual(bot.edits[0][:2], (7, 55))            # eskisi «tugagan» deb belgilandi
        self.assertIn("tugagan", bot.edits[0][2])
        self.assertEqual(len(bot.sent), 1)                     # «run bo'ldi» xabari
        self.assertIn("run bo'ldi", bot.sent[0][1])
        self.assertIn("yaratilgan botlar: 3", bot.sent[0][1])
        self.assertEqual(bot.edits[-1][:2], (7, 101))          # yakuniy yangilanish o'sha xabarning o'zida
        self.assertIn("Yangilanish", bot.edits[-1][2])
        self.assertEqual(db.raw_get(status.KEY), "{}")

    def test_loop_edits_same_message(self):
        bot = FakeBot()
        st = status.Status(bot, {7}, 2, env={}, started=time.time())

        async def go():
            await st.start("b", 0)
            t = asyncio.create_task(st.loop())
            await asyncio.sleep(2.5)
            t.cancel()
        asyncio.run(go())
        edits = [e for e in bot.edits if e[1] == 101]
        self.assertTrue(len(edits) >= 1)
        self.assertEqual(len(bot.sent), 1)


if __name__ == "__main__":
    unittest.main()
