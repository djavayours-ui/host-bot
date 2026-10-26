"""v3: shriftlar, broadcast, zaxira kanali tuzatishlari, navigatsiya (Orqaga/Bekor)."""
import asyncio
import os
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import backup
import broadcast
import db
import fonts
import limits


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = db.DATA_DIR
        db.DATA_DIR = str(Path(self.tmp.name, 'data'))
        self.env = patch.dict(os.environ, {'SECRET_KEY': 'offline-test-key', 'MASTER_BOT_TOKEN': 'test-master'})
        self.env.start()
        db.init()

    def tearDown(self):
        db.DATA_DIR = self.old_dir
        self.env.stop()
        self.tmp.cleanup()


class TestFonts(unittest.TestCase):
    def test_styles_change_letters_but_keep_other_chars(self):
        for name in fonts.available():
            out = fonts.style("Host Bot o'rnatish 2026!", name)
            self.assertIn("'", out, name)
            self.assertIn("!", out, name)
            self.assertNotEqual(out, "Host Bot o'rnatish 2026!", name)

    def test_html_tags_and_entities_preserved(self):
        out = fonts.style("a <b>x</b> &amp; <code>id</code>", "bold")
        self.assertIn("<b>", out)
        self.assertIn("</b>", out)
        self.assertIn("&amp;", out)
        self.assertIn("<code>", out)
        self.assertNotIn("x</b>", out)   # matn almashtirilgan

    def test_unknown_style_and_none(self):
        self.assertEqual(fonts.style("abc", "yoq"), "abc")
        self.assertEqual(fonts.style(None, "bold"), "")

    def test_bold_digits_and_known_values(self):
        self.assertEqual(fonts.style("Ab1", "bold"), "𝗔𝗯𝟭")
        self.assertEqual(fonts.style("abc", "smallcaps"), "ᴀʙᴄ")


class TestDbV3(Base):
    def test_news_removed_and_connection_closed(self):
        with sqlite3.connect(db.db_path()) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('news', tables)
        self.assertIn('broadcasts', tables)
        self.assertFalse(hasattr(db, 'add_news'))

    def test_audiences_exclude_blocked(self):
        for uid in (1, 2, 3):
            db.ensure_user(uid)
        db.add_host(2, '1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'n', 'u')
        db.set_blocked(3, True)
        self.assertEqual(db.audience_ids('all', extra={9}), [1, 2, 9])
        self.assertEqual(db.audience_ids('owners'), [2])
        db.set_user(1, promo_until=-1)
        self.assertEqual(db.audience_ids('promo'), [1])
        db.add_admin(77)
        self.assertEqual(db.audience_ids('admins', extra={5}), [5, 77])

    def test_user_writing_clears_blocked(self):
        db.ensure_user(10)
        db.set_blocked(10, True)
        self.assertEqual(db.get_user(10)['blocked'], 1)
        db.ensure_user(10)
        self.assertEqual(db.get_user(10)['blocked'], 0)

    def test_ai_refund_never_negative(self):
        db.ensure_user(5)
        db.ai_inc(5)
        db.ai_refund(5)
        db.ai_refund(5)
        self.assertEqual(db.ai_used(5), 0)

    def test_old_users_table_migrates(self):
        with sqlite3.connect(db.db_path()) as c:
            c.execute('DROP TABLE users')
            c.execute('CREATE TABLE users(user_id INTEGER PRIMARY KEY, promo_until REAL NOT NULL DEFAULT 0, '
                      'promo_code TEXT, max_bots INTEGER NOT NULL DEFAULT 0, ai_daily INTEGER NOT NULL DEFAULT 0, '
                      'note TEXT, created REAL NOT NULL)')
        db.init()
        db.ensure_user(42)
        self.assertEqual(db.get_user(42)['blocked'], 0)

    def test_stats(self):
        db.ensure_user(1)
        st = db.user_stats()
        self.assertEqual(st['users'], 1)
        self.assertEqual(st['active_day'], 1)


class FakeForbidden(Exception):
    pass


FakeForbidden.__name__ = 'TelegramForbiddenError'


class FakeRetry(Exception):
    retry_after = 0


FakeRetry.__name__ = 'TelegramRetryAfter'


class FakeBadRequest(Exception):
    pass


FakeBadRequest.__name__ = 'TelegramBadRequest'


async def _nosleep(_):
    return None


class TestBroadcast(Base):
    def run_job(self, recipients, send, **kw):
        job = broadcast.Job(db.add_broadcast(1, 'all', 'test', len(recipients)), 1, 'all', recipients)
        asyncio.run(broadcast.run(None, job, send, sleep=_nosleep, **kw))
        return job

    def test_counts_blocked_failed_and_marks_users(self):
        for uid in (1, 2, 3, 4):
            db.ensure_user(uid)
        async def send(bot, uid):
            if uid == 2:
                raise FakeForbidden('Forbidden: bot was blocked by the user')
            if uid == 3:
                raise FakeBadRequest('Bad Request: message is too long')
            if uid == 4:
                raise FakeBadRequest('Bad Request: chat not found')
        job = self.run_job([1, 2, 3, 4], send)
        self.assertEqual((job.sent, job.blocked, job.failed, job.status), (1, 2, 1, 'done'))
        self.assertEqual(db.get_user(2)['blocked'], 1)
        self.assertEqual(db.get_user(4)['blocked'], 1)
        self.assertEqual(db.get_user(3)['blocked'], 0)
        row = db.get_broadcast(job.id)
        self.assertEqual((row['sent'], row['blocked'], row['failed'], row['status']), (1, 2, 1, 'done'))

    def test_retry_after_is_retried(self):
        calls = {'n': 0}
        async def send(bot, uid):
            calls['n'] += 1
            if calls['n'] == 1:
                raise FakeRetry('Too Many Requests')
        job = self.run_job([1], send)
        self.assertEqual((job.sent, job.failed), (1, 0))
        self.assertEqual(calls['n'], 2)

    def test_network_error_retried_then_failed(self):
        class TelegramNetworkError(Exception):
            pass
        async def send(bot, uid):
            raise TelegramNetworkError('boom')
        job = self.run_job([1], send)
        self.assertEqual((job.sent, job.failed), (0, 1))
        self.assertEqual(job.errors, {'TelegramNetworkError': 1})

    def test_cancel_stops_remaining(self):
        seen = []
        job = broadcast.Job(db.add_broadcast(1, 'all', 't', 200), 1, 'all', list(range(1, 201)))
        async def send(bot, uid):
            seen.append(uid)
            if len(seen) == 5:
                job.cancel.set()
        asyncio.run(broadcast.run(None, job, send, sleep=_nosleep))
        self.assertEqual(job.status, 'cancelled')
        self.assertLess(job.sent, 200)
        self.assertEqual(db.get_broadcast(job.id)['status'], 'cancelled')

    def test_only_one_active_broadcast(self):
        async def go():
            gate = asyncio.Event()
            async def send(bot, uid):
                await gate.wait()
            job = broadcast.start(None, 1, 'all', [1, 2], send)
            with self.assertRaises(ValueError):
                broadcast.start(None, 1, 'all', [3], send)
            self.assertTrue(broadcast.stop(job.id))
            gate.set()
            await job.task
            self.assertIsNone(broadcast.ACTIVE)
            return job
        job = asyncio.run(go())
        self.assertEqual(job.status, 'cancelled')

    def test_empty_audience_rejected(self):
        async def go():
            broadcast.start(None, 1, 'all', [], AsyncMock())
        with self.assertRaises(ValueError):
            asyncio.run(go())

    def test_progress_callback_gets_final_state(self):
        states = []
        async def progress(job):
            states.append(job.status)
        async def send(bot, uid):
            return None
        job = broadcast.Job(db.add_broadcast(1, 'all', 't', 3), 1, 'all', [1, 2, 3])
        asyncio.run(broadcast.run(None, job, send, progress, sleep=_nosleep))
        self.assertEqual(states[-1], 'done')

    def test_parse_buttons(self):
        rows = broadcast.parse_buttons("Sayt | https://a.uz; Kanal | https://t.me/x\nYordam | https://b.uz")
        self.assertEqual(rows, [[('Sayt', 'https://a.uz'), ('Kanal', 'https://t.me/x')], [('Yordam', 'https://b.uz')]])
        for bad in ('', 'faqat matn', 'x | javascript:alert(1)', 'x | ftp://a', ' | https://a.uz',
                    '\n'.join(f'b{i} | https://a.uz/{i}' for i in range(7))):
            with self.assertRaises(ValueError, msg=bad):
                broadcast.parse_buttons(bad)

    def test_bar_and_duration(self):
        self.assertEqual(broadcast.bar(0), '▱' * 10)
        self.assertEqual(broadcast.bar(100), '▰' * 10)
        self.assertEqual(broadcast.fmt_duration(75), '1 daq 15 soniya')


class TestBackupFixes(Base):
    def test_backup_does_not_block_users_only_restore_does(self):
        async def go():
            async with backup.LOCK:
                self.assertFalse(backup.restoring())
        asyncio.run(go())
        backup._RESTORING += 1
        try:
            self.assertTrue(backup.restoring())
        finally:
            backup._RESTORING -= 1

    def test_fake_db_file_does_not_break_snapshot(self):
        hid = db.add_host(1, '1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'n', 'u')
        d = Path(db.bot_dir(hid))
        d.mkdir(parents=True)
        (d / 'main.py').write_text('print(1)')
        db.update_host(hid, entry='main.py')
        (d / 'fake.db').write_text('bu SQLite emas')
        out = str(Path(self.tmp.name, 'snap.zip'))
        self.assertGreater(backup.make_snapshot(out), 0)

    def test_explain_error_is_readable(self):
        self.assertIn('Kanal topilmadi', backup.explain_error(Exception('Bad Request: chat not found')))
        self.assertIn('huquq', backup.explain_error(Exception('Bad Request: not enough rights')))
        self.assertIn('chiqarilgan', backup.explain_error(Exception('Forbidden: bot was kicked')))

    def test_check_channel_wraps_telegram_errors(self):
        bot = types.SimpleNamespace(get_chat=AsyncMock(side_effect=Exception('Bad Request: chat not found')))
        with self.assertRaises(ValueError) as cm:
            asyncio.run(backup.check_channel(bot, -100))
        self.assertIn('Kanal topilmadi', str(cm.exception))

    def test_enum_like_status_and_type_accepted(self):
        class E:
            def __init__(self, v):
                self.value = v
            def __str__(self):
                return 'Enum.' + self.value.upper()
        bot = types.SimpleNamespace(
            get_chat=AsyncMock(return_value=types.SimpleNamespace(type=E('channel'), id=-1001)),
            get_me=AsyncMock(return_value=types.SimpleNamespace(id=1)),
            get_chat_member=AsyncMock(return_value=types.SimpleNamespace(
                status=E('administrator'), can_post_messages=True, can_edit_messages=True)))
        asyncio.run(backup.check_channel(bot, -1001))

    def test_creator_needs_no_rights_flags(self):
        bot = types.SimpleNamespace(
            get_chat=AsyncMock(return_value=types.SimpleNamespace(type='channel', id=-1001)),
            get_me=AsyncMock(return_value=types.SimpleNamespace(id=1)),
            get_chat_member=AsyncMock(return_value=types.SimpleNamespace(status='creator')))
        asyncio.run(backup.check_channel(bot, -1001))

    def test_group_is_rejected(self):
        bot = types.SimpleNamespace(get_chat=AsyncMock(return_value=types.SimpleNamespace(type='supergroup', id=-1)))
        with self.assertRaises(ValueError):
            asyncio.run(backup.check_channel(bot, -1))

    def test_pin_failure_keeps_backup_registered_and_reports(self):
        db.set_backup_channel(-100123)
        bot = types.SimpleNamespace(
            send_document=AsyncMock(return_value=types.SimpleNamespace(
                document=types.SimpleNamespace(file_id='f1'), message_id=7)),
            pin_chat_message=AsyncMock(side_effect=Exception('Bad Request: not enough rights to pin a message')))
        with patch.object(backup, 'check_channel', AsyncMock()):
            with self.assertRaises(ValueError) as cm:
                asyncio.run(backup.send_snapshot(bot, 'manual'))
        self.assertIn('pin', str(cm.exception))
        self.assertEqual(db.list_backups()[0]['path'], 'f1')   # qo'lda tiklash mumkin

    def test_inspect_channel_reports_each_step(self):
        pinned = types.SimpleNamespace(document=object(), caption=backup.MARKER + '\nx')
        bot = types.SimpleNamespace(
            get_chat=AsyncMock(return_value=types.SimpleNamespace(type='channel', id=-1001, title='Z', pinned_message=pinned)),
            get_me=AsyncMock(return_value=types.SimpleNamespace(id=1)),
            get_chat_member=AsyncMock(return_value=types.SimpleNamespace(
                status='administrator', can_post_messages=True, can_edit_messages=False)),
            send_message=AsyncMock(return_value=types.SimpleNamespace(message_id=5)),
            delete_message=AsyncMock())
        with patch.dict(os.environ, {'BACKUP_CHANNEL_ID': ''}):
            report = asyncio.run(backup.inspect_channel(bot, -1001))
        flags = {text: ok for ok, text in report}
        self.assertFalse(flags['Tahrirlash / pin qilish huquqi'])
        self.assertTrue(flags['Xabar yuborish huquqi'])
        self.assertTrue(any('BACKUP_CHANNEL_ID' in t and ok is False for ok, t in report))
        bot.delete_message.assert_awaited_once()
        with patch.dict(os.environ, {'BACKUP_CHANNEL_ID': '-1001'}):
            report = asyncio.run(backup.inspect_channel(bot, -1001))
        self.assertTrue(any('to‘g‘ri sozlangan' in t and ok for ok, t in report))

    def test_invalid_env_channel_is_ignored(self):
        with patch.dict(os.environ, {'BACKUP_CHANNEL_ID': '@kanal'}):
            self.assertIsNone(backup.channel_id())

    def test_daily_alert_is_throttled(self):
        db.set_backup_channel(-100123)
        alerts = []
        async def alert(text):
            alerts.append(text)
        sleeps = []
        async def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) >= 6:
                raise asyncio.CancelledError
        async def go():
            with patch.object(backup, 'send_snapshot', AsyncMock(side_effect=ValueError('xato'))), \
                 patch.object(backup.asyncio, 'sleep', fake_sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await backup.daily(None, alert)
        asyncio.run(go())
        self.assertEqual(len(alerts), 1)


class TestLimitsV3(Base):
    def test_ai_refund_returns_quota(self):
        uid = 900
        db.ensure_user(uid)
        db.set_user(uid, ai_daily=2)
        limits.ai_consume(uid)
        self.assertEqual(limits.ai_left(uid)[1], 1)
        limits.ai_refund(uid)
        self.assertEqual(limits.ai_left(uid)[1], 0)


@unittest.skipUnless(__import__('importlib').util.find_spec('aiogram'), "aiogram o'rnatilmagan")
class TestNavigation(Base):
    """Hech bir ekranda «asosiy menyu» tugmasi yo'q: faqat Orqaga / Bekor qilish."""

    def labels(self, markup):
        return [b.text for row in markup.inline_keyboard for b in row]

    def test_no_home_button_labels(self):
        import extensions
        import main
        import ui
        with patch.object(limits, 'is_admin', return_value=True):
            markups = [main.main_menu(1), extensions.admin_kb(), extensions.backup_kb(),
                       extensions.promos_view()[1], extensions.users_view(0)[1],
                       extensions.bc_menu()[1], extensions.bc_confirm_kb(5), ui.cancel_kb('x'), ui.back_kb('x')]
        for markup in markups:
            for label in self.labels(markup):
                self.assertNotIn('Menyu', label)
                self.assertNotIn('🏠', label)

    def test_news_button_removed_from_menus(self):
        import extensions
        import main
        with patch.object(limits, 'is_admin', return_value=True):
            data = [b.callback_data for m in (main.main_menu(1), extensions.admin_kb()) for row in m.inline_keyboard for b in row]
        self.assertNotIn('news', data)
        self.assertFalse(hasattr(main, 'news_view'))

    def test_every_callback_has_target_within_64_bytes(self):
        import extensions
        import main
        with patch.object(limits, 'is_admin', return_value=True):
            ms = [main.main_menu(1), extensions.admin_kb(), extensions.backup_kb(), extensions.bc_confirm_kb(3)]
        for m in ms:
            for row in m.inline_keyboard:
                for b in row:
                    self.assertLessEqual(len(b.callback_data.encode()), 64)

    def test_cancel_returns_to_given_screen(self):
        import ui
        self.assertEqual(ui.cancel_kb('p:3').inline_keyboard[0][0].callback_data, 'p:3')
        self.assertIn('Bekor', ui.cancel_kb('p:3').inline_keyboard[0][0].text)
        self.assertIn('Orqaga', ui.back_kb('mine').inline_keyboard[0][0].text)


if __name__ == '__main__':
    unittest.main()
