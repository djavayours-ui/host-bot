"""Kino shablon mantig'i (aiogram'siz): hashtag, indeks, qidiruv."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.getcwd())
import config  # noqa: E402
import db  # noqa: E402
import kino_core as kc  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DATA_DIR = self.tmp.name
        db.init()
        db.create_bot_with_defaults(1, 10, "1:tok", "kinobot", "Kino", "kino")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tags(self):
        self.assertEqual(kc.tag_of("Tez va Jahldor 5"), "#tez_va_jahldor_5")
        self.assertEqual(kc.tag_of("O'rgimchak odam!"), "#orgimchak_odam")
        self.assertEqual(kc.first_tag("#Tez_va_Jahldor_5\n🎬 x #boshqa"), "#tez_va_jahldor_5")
        self.assertEqual(kc.first_tag("hashtag yo'q"), "")
        self.assertEqual(db.get_bot(1)["kind"], "kino")

    def test_index_parts_and_dedupe(self):
        self.assertEqual(kc.add_post(1, 100, "#avatar", "video", title="Avatar"), (1, True, 1))
        self.assertEqual(kc.add_post(1, 105, "#avatar", "video"), (1, False, 2))
        self.assertIsNone(kc.add_post(1, 105, "#avatar", "video"))      # takror post
        self.assertEqual(kc.add_post(1, 110, "#titanik", "document")[0], 2)
        self.assertEqual(kc.parts(1, "#avatar"), [100, 105])
        self.assertEqual(kc.counts(1)[:2], (2, 3))

    def test_search(self):
        kc.add_post(1, 1, "#tez_va_jahldor_5", "video", title="Tez va Jahldor 5")
        kc.add_post(1, 2, "#tez_va_jahldor_6", "video", title="Tez va Jahldor 6")
        kc.add_post(1, 3, "#orgimchak_odam", "video", title="O'rgimchak odam")
        self.assertEqual(kc.search(1, "2")[0]["title"], "Tez va Jahldor 6")                 # kod bo'yicha
        self.assertEqual(len(kc.search(1, "jahldor")), 2)                                  # nom bo'yicha
        self.assertEqual(kc.search(1, "tez va jahldor 5")[0]["code"], 1)                   # aniq moslik birinchi
        self.assertEqual(kc.search(1, "orgimchak")[0]["code"], 3)                          # apostrofsiz
        self.assertEqual(kc.search(1, "orgimchek odam")[0]["code"], 3)                     # imlo xatosi
        self.assertEqual(kc.search(1, "yo'q narsa"), [])

    def test_views_top_delete(self):
        kc.add_post(1, 1, "#a_film", "video", title="A film")
        kc.add_post(1, 2, "#b_film", "video", title="B film")
        kc.bump_views(1, "#b_film")
        self.assertEqual(kc.top(1)[0]["title"], "B film")
        kc.delete_post(1, 2)                                   # kanaldan o'chgan post → kino ham yo'qoladi
        self.assertIsNone(kc.title_by_tag(1, "#b_film"))
        self.assertEqual(kc.delete_title(1, "#a_film"), [1])
        self.assertEqual(kc.counts(1)[:2], (0, 0))

    def test_new_bot_columns_and_delete(self):
        db.update_bot(1, kino_chat=-100123, kino_protect=1, kino_footer="x")
        r = db.get_bot(1)
        self.assertEqual((r["kino_chat"], r["kino_protect"], r["kino_footer"]), (-100123, 1, "x"))
        kc.add_post(1, 1, "#a_film", "video")
        db.delete_bot(1)
        self.assertEqual(kc.counts(1)[:2], (0, 0))


if __name__ == "__main__":
    unittest.main()
