"""v2 inline panellar: admin paneli, zaxira, foydalanuvchilar, promo, broadcast.

Barcha imtiyozli callback va FSM kiritishlarida admin huquqi QAYTA tekshiriladi.
Navigatsiya qoidasi: «asosiy menyu» tugmasi yo'q — faqat «⬅️ Orqaga» yoki kerakli joyga «❌ Bekor qilish».
"""
import html
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import aiex
import backup
import bot_secrets
import broadcast
import db
import fonts as F_
import limits
import runner
from ui import BACK, CANCEL, back_kb, cancel_kb, kb, reset_state


class Input(StatesGroup):
    text = State()
    restore = State()
    restore_confirm = State()


class Broadcast(StatesGroup):
    content = State()


def esc(text):
    return html.escape(str(text))


def sc(text):
    """Admin tugmalari uchun ꜱᴍᴀʟʟ ᴄᴀᴘꜱ."""
    return F_.style(text, "smallcaps")


def admin_kb():
    return kb(
        [('💾 ' + sc('Zaxira'), 'v2:bkp'), ('📣 ' + sc('Broadcast'), 'v2:bc')],
        [('👥 ' + sc('Foydalanuvchilar'), 'v2:users:0'), ('🎁 ' + sc('Promolar'), 'v2:promos')],
        [('📊 ' + sc('Statistika'), 'v2:stats'), ('🔎 ' + sc('AI diagnostika'), 'v2:diagnostic')],
        [(BACK, 'home')],
    )


def backup_kb():
    return kb(
        [('📢 ' + sc('Kanalni sozlash'), 'v2:channel'), ('🔎 ' + sc('Tekshirish'), 'v2:channelcheck')],
        [('💾 ' + sc('Hozir zaxiralash'), 'v2:backup'), ('♻️ ' + sc('Tiklash'), 'v2:backups')],
        [('📎 ' + sc('ZIP yuklab tiklash'), 'v2:restoreupload')],
        [(BACK, 'v2:admin')],
    )


ADMIN_ACTIONS = {'admin', 'stats', 'bkp', 'channel', 'channelcheck', 'backup', 'backups', 'restore',
                 'restoreyes', 'restoreupload', 'users', 'user', 'setlimits', 'promos',
                 'newpromo', 'promotoggle', 'revokepromo', 'diagnostic',
                 'bc', 'bcnew', 'bcaud', 'bcbtn', 'bctest', 'bcgo', 'bcstop', 'bchist'}
ADMIN_INPUTS = {'channel', 'setlimits', 'newpromo', 'bcbuttons'}
OWN_ACTIONS = {'secrets', 'secretadd', 'secretdel', 'res', 'explain', 'gitupdate', 'gitconfirm'}
# Bu amallarda FSM ma'lumotlari saqlanishi kerak (qolganlarida holat tozalanadi).
KEEP_STATE = {'restoreyes', 'bcbtn', 'bctest', 'bcgo'}

# Xatolik yuz berganda qaytariladigan ota-ekran.
PARENT = {
    'channel': 'v2:bkp', 'channelcheck': 'v2:bkp', 'backup': 'v2:bkp', 'backups': 'v2:bkp',
    'restore': 'v2:backups', 'restoreyes': 'v2:bkp', 'restoreupload': 'v2:bkp', 'bkp': 'v2:admin',
    'users': 'v2:admin', 'user': 'v2:users:0', 'setlimits': 'v2:users:0', 'revokepromo': 'v2:users:0',
    'promos': 'v2:admin', 'newpromo': 'v2:promos', 'promotoggle': 'v2:promos', 'stats': 'v2:admin',
    'diagnostic': 'v2:admin', 'bc': 'v2:admin', 'bcnew': 'v2:bc', 'bcaud': 'v2:bcnew', 'bcbtn': 'v2:bc',
    'bctest': 'v2:bc', 'bcgo': 'v2:bc', 'bcstop': 'v2:bc', 'bchist': 'v2:bc',
    'limits': 'home', 'promo': 'home', 'models': 'ai', 'model': 'ai', 'project': 'ai', 'admin': 'home',
}


def owned(uid, hid):
    try:
        host = db.get_host(int(hid))
    except (ValueError, TypeError):
        return None
    return host if host and host['owner_id'] == uid else None


def parent_of(action, bits):
    if action in OWN_ACTIONS and len(bits) > 2:
        return f'p:{bits[2]}'
    return PARENT.get(action, 'home')


async def edit(c, text, markup=None, parent='home'):
    from aiogram.exceptions import TelegramBadRequest
    markup = markup or back_kb(parent)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if 'not modified' not in str(e):
            await c.message.answer(text, reply_markup=markup)


async def prompt(c, state, action, text, cancel, **data):
    """Matn kiritishni so'raydi; «❌ Bekor qilish» tugmasi `cancel` ekraniga qaytaradi."""
    await reset_state(state)
    await state.set_state(Input.text)
    await state.update_data(action=action, cancel=cancel, **data)
    await c.message.answer(text, reply_markup=cancel_kb(cancel))


def ago(ts):
    if not ts:
        return 'hali yo‘q'
    sec = max(0, int(time.time() - ts))
    if sec < 60:
        return 'hozirgina'
    if sec < 3600:
        return f'{sec // 60} daq oldin'
    if sec < 86400:
        return f'{sec // 3600} soat oldin'
    return f'{sec // 86400} kun oldin'


def clean_repo_tree(dest):
    files = {}
    for root, dirs, names in os.walk(dest, followlinks=False):
        for d in list(dirs):
            if (Path(root) / d).is_symlink():
                raise ValueError('GitHub loyihasida symlink bor.')
        dirs[:] = [d for d in dirs if d not in runner.SKIP_DIRS]
        for n in names:
            p = Path(root, n)
            rel = p.relative_to(dest).as_posix()
            if p.is_symlink() or ':' in rel:
                raise ValueError('GitHub loyihasida xavfli fayl bor.')
            if n in runner.SKIP_FILES:
                continue
            files[rel] = p
            if len(files) > runner.MAX_ZIP_FILES:
                raise ValueError('Repoda 300 dan ortiq fayl bor.')
    if sum(p.stat().st_size for p in files.values()) > runner.MAX_UNZIPPED:
        raise ValueError('Repo 60 MB dan oshdi.')
    return files


async def install_github(m, state, text):
    from main import finish_add
    parts = text.split()
    if not parts or len(parts) > 2 or not runner.normalize_repo(parts[0]):
        return await m.answer('❌ Masalan: https://github.com/user/repo yoki user/repo main')
    url = runner.normalize_repo(parts[0])
    branch = parts[1] if len(parts) == 2 else ''
    data = await state.get_data()
    if not data.get('token'):
        return await m.answer('Avval «Bot qo‘shish» orqali token kiriting.')
    await m.answer('⏳ GitHub loyihasi yuklanmoqda...')
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, 'repo')
        ok, msg = await runner.git_clone(url, branch, dest)
        if not ok:
            return await m.answer('❌ ' + esc(msg))
        files = clean_repo_tree(Path(dest))
        if not runner.find_entry(dest):
            return await m.answer('❌ Repoda Python/PHP asosiy fayli topilmadi.')
        # Record installed source files so later updates can remove deleted source only.
        import zipfile
        fd, archive = tempfile.mkstemp(suffix='.zip')
        os.close(fd)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel, path in files.items():
                z.write(path, rel)
            z.writestr('.host-source.json', json.dumps(list(files)))
        await state.update_data(git_repo=url, git_branch=branch)
        await finish_add(m, state, archive, 'github.zip')


async def update_github(host):
    """Stage source, stop, keep rollback, preserve runtime databases and untracked files."""
    hid = host['id']
    d = Path(db.bot_dir(hid))
    with tempfile.TemporaryDirectory() as td:
        clone = Path(td, 'repo')
        ok, msg = await runner.git_clone(host['git_repo'], host['git_branch'] or '', str(clone))
        if not ok:
            raise ValueError(msg)
        files = clean_repo_tree(clone)
        if not runner.find_entry(str(clone)):
            raise ValueError('Yangi repoda asosiy fayl yo‘q.')
        was = runner.is_running(hid)
        await runner.stop(hid)
        rollback = Path(td, 'old')
        shutil.copytree(d, rollback, symlinks=True)
        try:
            manifest = d / '.host-source.json'
            previous = json.loads(manifest.read_text()) if manifest.exists() else []
            for rel in set(previous) - set(files):
                target = Path(runner.safe_join(d, rel))
                if target.is_file() and target.suffix.lower() not in runner.DB_EXT:
                    target.unlink()
            for rel, src in files.items():
                target = Path(runner.safe_join(d, rel))
                # Never overwrite existing user DBs or .env on a code update.
                if target.exists() and (target.suffix.lower() in runner.DB_EXT or target.name == '.env'):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            manifest.write_text(json.dumps(list(files)))
            entry = host['entry'] if (d / (host['entry'] or '_')).is_file() else runner.find_entry(str(d))
            db.update_host(hid, entry=entry)
            if was:
                ok, msg = await runner.start(db.get_host(hid))
                if not ok:
                    raise ValueError(msg)
        except Exception:
            await runner.stop(hid)
            shutil.rmtree(d)
            shutil.copytree(rollback, d, symlinks=True)
            db.update_host(hid, entry=host['entry'])
            if was:
                await runner.start(db.get_host(hid))
            raise


# ----------------------------- ko'rinishlar (view) -----------------------------
def admin_text():
    st = db.user_stats()
    info = backup.summary()
    bk = ('✅' if info['env_ok'] else '⚠️') if info['channel'] else '❌'
    return (f"👑 <b>{F_.h1('Admin paneli')}</b>\n"
            f"👥 {st['users']} foydalanuvchi · 🤖 {st['hosts']} bot · 📊 ishlayotgan: {len(runner.PROCS)}\n"
            f"💾 Zaxira: {bk} oxirgi — {ago(info['last_at'])}\n"
            '⚠️ Faqat ishonchli kodni hosting qiling. Subprocess xavfsiz sandbox emas.')


def backup_text():
    info = backup.summary()
    lines = [f"💾 <b>{F_.h1('Zaxira')}</b>"]
    if info['channel']:
        title = esc(info['title'] or '—')
        lines.append(f"📢 Kanal: {title} · <code>{info['channel']}</code>")
    else:
        lines.append('📢 Kanal: <b>sozlanmagan</b>')
    lines.append(f"🕒 Oxirgi zaxira: {ago(info['last_at'])} · saqlangan nusxalar: {info['count']}")
    if info['channel'] and not info['env_ok']:
        lines.append('\n⚠️ GitHub Secret <code>BACKUP_CHANNEL_ID</code> yo‘q yoki boshqa kanalga ishora qiladi: '
                     'yangi runnerda ma’lumotlar avtomatik tiklanmaydi. «🔎 Tekshirish» to‘liq hisobot beradi.')
    return '\n'.join(lines)


def stats_text():
    st = db.user_stats()
    hist = db.list_broadcasts(1)
    last = (f"#{hist[0]['id']} · {hist[0]['sent']}/{hist[0]['total']} yuborilgan" if hist else 'hali yo‘q')
    return (f"📊 <b>{F_.h1('Statistika')}</b>\n"
            f"👥 Foydalanuvchilar: {st['users']} (🚫 bloklagan: {st['blocked']})\n"
            f"🟢 24 soatda faol: {st['active_day']} · 7 kunda: {st['active_week']}\n"
            f"🤖 Botlar: {st['hosts']} · egalari: {st['owners']}\n"
            f"🎁 Faol promo: {st['promo']}\n"
            f"📣 Oxirgi broadcast: {last}")


def users_view(page):
    per = 10
    users = db.all_users()
    pages = max(1, (len(users) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    rows = []
    for u in users[page * per:(page + 1) * per]:
        flags = ('🎁' if limits.promo_active(u['user_id']) else '') + ('🚫' if u['blocked'] else '')
        rows.append([(f'👤 {u["user_id"]} · {db.count_hosts(u["user_id"])} bot {flags}'.strip(), f'v2:user:{u["user_id"]}')])
    nav = []
    if page > 0:
        nav.append(('◀️', f'v2:users:{page - 1}'))
    nav.append((f'{page + 1}/{pages}', f'v2:users:{page}'))
    if page < pages - 1:
        nav.append(('▶️', f'v2:users:{page + 1}'))
    rows += [nav, [(BACK, 'v2:admin')]]
    return f"👥 <b>{F_.h1('Foydalanuvchilar')}</b> · jami {len(users)}", kb(*rows)


def user_view(uid):
    row = db.get_user(uid)
    if not row:
        raise ValueError('Foydalanuvchi topilmadi.')
    botlim, ailim = limits.bot_limit(uid), limits.ai_limit(uid)
    text = (f"👤 <b>{F_.h1('Foydalanuvchi')}</b> <code>{uid}</code>\n"
            f"🤖 Botlar: {db.count_hosts(uid)}/{botlim or '∞'} · 🧠 AI bugun: {db.ai_used(uid)}/{ailim or '∞'}\n"
            f"🕒 Oxirgi faollik: {ago(row['last_seen'])}"
            + ('\n🚫 Botni bloklagan (yozsa, belgi o‘chadi)' if row['blocked'] else ''))
    promo = limits.promo_status(uid)
    if promo:
        text += '\n' + esc(promo)
    rows = []
    if limits.promo_active(uid):
        rows.append([('🎁 Promoni bekor qilish', f'v2:revokepromo:{uid}')])
    rows += [[('⚖️ Limit belgilash', f'v2:setlimits:{uid}')], [(BACK, 'v2:users:0')]]
    return text, kb(*rows)


def promos_view():
    rows = []
    for p in db.list_promos()[:30]:
        rows.append([(f'{"✅" if p["active"] else "❌"} {p["code"]} · {p["uses"]}', f'v2:promotoggle:{p["code"]}')])
    rows += [[('➕ Promo yaratish', 'v2:newpromo')], [(BACK, 'v2:admin')]]
    return (f"🎁 <b>{F_.h1('Promolar')}</b>\nTugma kodni yoqadi/o‘chiradi. "
            'Avval berilgan huquqlar avtomatik bekor bo‘lmaydi.'), kb(*rows)


# ----------------------------- broadcast -----------------------------
def _extra_ids(kind):
    if kind == 'admins':
        return set(limits._ENV_ADMINS)
    if kind == 'all':
        return set(limits._ALLOWED)
    return set()


def bc_audience(kind):
    return db.audience_ids(kind, extra=_extra_ids(kind))


def bc_markup(rows):
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=url) for label, url in row] for row in rows])


def bc_progress_text(job):
    title = broadcast.AUDIENCE_TITLES.get(job.audience, job.audience)
    state = {'running': '⏳ davom etmoqda', 'done': '✅ tugadi', 'cancelled': '⏹ to‘xtatildi', 'error': '❌ xato'}[job.status]
    text = (f"📣 <b>{F_.h1('Broadcast')}</b> #{job.id} · {title}\n"
            f"{broadcast.bar(job.percent)} {job.percent}% · {state}\n"
            f"✅ yuborildi: {job.sent} · 🚫 bloklagan: {job.blocked} · ❌ xato: {job.failed}\n"
            f"👥 jami: {job.total}")
    if job.status == 'running' and job.eta:
        text += f"\n⏱ taxminan qoldi: {broadcast.fmt_duration(job.eta)}"
    elif job.status != 'running':
        text += f"\n⏱ davomiyligi: {broadcast.fmt_duration(job.finished - job.started)}"
        if job.errors:
            text += '\n⚠️ ' + ', '.join(f'{k}: {v}' for k, v in list(job.errors.items())[:3])
    return text


def bc_running_kb(job):
    return kb([('⏹ To‘xtatish', f'v2:bcstop:{job.id}')]) if job.status == 'running' else kb([(BACK, 'v2:bc')])


def bc_menu():
    job = broadcast.ACTIVE
    if job and job.status == 'running':
        return bc_progress_text(job), kb([('🔄 Yangilash', 'v2:bc')], [('⏹ To‘xtatish', f'v2:bcstop:{job.id}')], [(BACK, 'v2:admin')])
    hist = db.list_broadcasts(1)
    last = ''
    if hist:
        h = hist[0]
        last = f"\n\nOxirgi: #{h['id']} · {h['sent']}/{h['total']} yuborilgan · {ago(h['created'])}"
    return (f"📣 <b>{F_.h1('Broadcast')}</b>\nHar qanday xabar (matn, rasm, video, fayl) formatlashi bilan yuboriladi. "
            'Yuborishdan oldin ko‘rib chiqasiz va o‘zingizga sinab ko‘rishingiz mumkin.' + last,
            kb([('✍️ Yangi broadcast', 'v2:bcnew')], [('📜 Tarix', 'v2:bchist')], [(BACK, 'v2:admin')]))


def bc_preview_text(data):
    kind = data.get('bc_aud', 'all')
    n = len(bc_audience(kind))
    btns = data.get('bc_buttons') or []
    return (f"📣 <b>{F_.h1('Tasdiqlash')}</b>\n"
            f"{broadcast.AUDIENCE_TITLES.get(kind, kind)}: <b>{n}</b> ta qabul qiluvchi\n"
            f"🔗 Tugmalar: {sum(len(r) for r in btns) or 'yo‘q'}\n"
            f"⏱ taxminiy vaqt: ≈ {broadcast.fmt_duration(n / broadcast.RATE)}\n\n"
            'Yuqoridagi xabar aynan shunday yuboriladi. Boshqa xabar yuborsangiz — u almashtiriladi.'), n


def bc_confirm_kb(n):
    return kb(
        [(f'🚀 Yuborish ({n})', 'v2:bcgo')],
        [('🧪 Sinov: o‘zimga', 'v2:bctest'), ('🔗 Tugma qo‘shish', 'v2:bcbtn')],
        [(CANCEL, 'v2:bc')],
    )


async def bc_show_confirm(bot, chat_id, state):
    """Xabar ko'rinishini (preview) va tasdiqlash tugmalarini chiqaradi."""
    data = await state.get_data()
    await bot.copy_message(chat_id=chat_id, from_chat_id=data['bc_chat'], message_id=data['bc_msg'],
                           reply_markup=bc_markup(data.get('bc_buttons')))
    text, n = bc_preview_text(data)
    await bot.send_message(chat_id, text, reply_markup=bc_confirm_kb(n))


def _describe(m):
    if getattr(m, 'text', None):
        return m.text[:120]
    cap = getattr(m, 'caption', None) or ''
    for kind in ('photo', 'video', 'document', 'animation', 'voice', 'audio', 'video_note', 'sticker'):
        if getattr(m, kind, None):
            return f'[{kind}] {cap}'[:120]
    return cap[:120] or '[xabar]'


async def bc_content(m, state, bot):
    """Broadcast mazmuni: istalgan turdagi xabar qabul qilinadi."""
    if not limits.is_admin(m.from_user.id):
        await state.clear()
        return await m.answer('⛔ Faqat admin uchun', reply_markup=back_kb('home'))
    data = await state.get_data()
    if data.get('bc_aud') not in db.AUDIENCES:
        await state.clear()
        return await m.answer('Sessiya tugagan. Qaytadan boshlang.', reply_markup=back_kb('v2:bc'))
    await state.update_data(bc_chat=m.chat.id, bc_msg=m.message_id, bc_desc=_describe(m))
    try:
        await bc_show_confirm(bot, m.chat.id, state)
    except Exception as e:
        await m.answer('❌ Bu xabarni yuborib bo‘lmaydi: ' + esc(str(e)[:300]), reply_markup=cancel_kb('v2:bc'))


async def bc_start(c, state, bot):
    data = await state.get_data()
    if not data.get('bc_msg') or data.get('bc_aud') not in db.AUDIENCES:
        raise ValueError('Sessiya tugagan. Qaytadan boshlang.')
    kind, chat, msg = data['bc_aud'], data['bc_chat'], data['bc_msg']
    markup = bc_markup(data.get('bc_buttons'))
    recipients = bc_audience(kind)
    admin_chat = c.message.chat.id

    async def send(b, uid):
        await b.copy_message(chat_id=uid, from_chat_id=chat, message_id=msg, reply_markup=markup)

    holder = {}

    async def progress(job):
        mid = holder.get('mid')
        if not mid:
            return
        try:
            await bot.edit_message_text(bc_progress_text(job), chat_id=admin_chat, message_id=mid,
                                        reply_markup=bc_running_kb(job))
        except Exception:
            pass

    async def done(job):
        try:
            await bot.send_message(admin_chat, '🏁 ' + bc_progress_text(job), reply_markup=back_kb('v2:bc'))
        except Exception:
            pass

    first = await bot.send_message(admin_chat, '📣 Broadcast boshlanmoqda...')
    holder['mid'] = first.message_id
    try:
        job = broadcast.start(bot, c.from_user.id, kind, recipients, send, data.get('bc_desc', ''), progress, done)
    except ValueError:
        try:
            await first.delete()
        except Exception:
            pass
        raise
    await state.clear()
    await progress(job)


async def callback(c, state, bot):
    bits = c.data.split(':')
    action = bits[1] if len(bits) > 1 else ''
    uid = c.from_user.id
    # Do not trust hidden buttons or forged callback data.
    if action in ADMIN_ACTIONS and not limits.is_admin(uid):
        return await c.answer('⛔ Faqat admin uchun', show_alert=True)
    h = None
    if action in OWN_ACTIONS:
        h = owned(uid, bits[2] if len(bits) > 2 else None)
        if not h:
            return await c.answer('⛔ Bot topilmadi yoki ruxsat yo‘q', show_alert=True)
    await c.answer()
    try:
        if action not in KEEP_STATE:
            await reset_state(state)   # eski kiritish holati va vaqtinchalik fayllar tozalanadi
        if action == 'admin':
            return await edit(c, admin_text(), admin_kb())
        if action == 'stats':
            return await edit(c, stats_text(), back_kb('v2:admin'))
        if action == 'bkp':
            return await edit(c, backup_text(), backup_kb())
        if action == 'channel':
            return await prompt(c, state, action,
                '📢 Botni <b>private zaxira kanaliga admin</b> qiling va quyidagi huquqlarni bering: '
                '<i>xabar yuborish</i> va <i>xabarlarni tahrirlash</i> (pin uchun).\n\n'
                'Keyin <b>kanal ID</b> sini (-100...), @username yuboring yoki kanaldan <b>istalgan postni shu yerga forward qiling</b>.\n'
                '⚠️ Kanalga maxfiy tokenli ZIP lar yuboriladi — a’zolarni ehtiyot qiling.', 'v2:bkp')
        if action == 'channelcheck':
            channel = backup.channel_id()
            if not channel:
                raise ValueError('Avval zaxira kanalini qo‘shing.')
            await edit(c, '⏳ Kanal tekshirilmoqda...', back_kb('v2:bkp'))
            report = await backup.inspect_channel(bot, channel)
            icons = {True: '✅', False: '❌', None: '⚠️'}
            lines = [f'{icons[ok]} {esc(text)}' for ok, text in report]
            bad = any(ok is False for ok, _ in report)
            lines.append('\n' + ('❌ Muammolar bor — yuqoridagilarni tuzating.' if bad else '✅ Hammasi tayyor.'))
            return await edit(c, f"🔎 <b>{F_.h1('Kanal tekshiruvi')}</b>\n" + '\n'.join(lines),
                              kb([('🔄 Qayta tekshirish', 'v2:channelcheck')], [(BACK, 'v2:bkp')]))
        if action == 'backup':
            await edit(c, '⏳ Zaxira tayyorlanmoqda va kanalga yuborilmoqda...', back_kb('v2:bkp'))
            size = await backup.send_snapshot(bot, 'manual')
            return await edit(c, f'✅ Zaxira kanalga yuborildi va pin qilindi: {runner.human(size)}.\n\n' + backup_text(), backup_kb())
        if action == 'backups':
            rows = []
            for b in db.list_backups(15):
                label = time.strftime('%m-%d %H:%M', time.gmtime(b['created']))
                rows.append([(f'♻️ #{b["id"]} {label} · {runner.human(b["size"])}', f'v2:restore:{b["id"]}')])
            rows += [[('📎 ZIP yuklab tiklash', 'v2:restoreupload')], [(BACK, 'v2:bkp')]]
            return await edit(c, '💾 Tiklash uchun nusxani tanlang. Amaldagi ma’lumotlar almashtiriladi.', kb(*rows))
        if action == 'restoreupload':
            await state.set_state(Input.restore)
            return await c.message.answer('📎 Host Bot v2 to‘liq zaxira ZIP faylini yuboring (19 MB gacha).',
                                          reply_markup=cancel_kb('v2:bkp'))
        if action == 'restore':
            b = db.get_backup(int(bits[2]))
            if not b:
                raise ValueError('Zaxira topilmadi.')
            await state.set_state(Input.restore_confirm)
            await state.update_data(restore_file=b['path'])
            return await edit(c, '⚠️ Barcha botlar to‘xtatilib, bazalar va fayllar almashtiriladi. Davom etasizmi?',
                kb([('✅ Tiklash', 'v2:restoreyes')], [(CANCEL, 'v2:backups')]))
        if action == 'restoreyes':
            data = await state.get_data()
            if await state.get_state() != Input.restore_confirm.state or not data.get('restore_file'):
                raise ValueError('Tasdiqlash sessiyasi tugagan.')
            await state.clear()
            await edit(c, '⏳ Tiklanmoqda... Botlar vaqtincha to‘xtatiladi.', back_kb('v2:bkp'))
            await backup.restore_telegram(bot, data['restore_file'])
            return await edit(c, '✅ Zaxira tiklandi. Oldingi nusxa serverdagi .rollback papkasida saqlandi.', backup_kb())
        if action == 'limits':
            botlim, ailim = limits.bot_limit(uid), limits.ai_limit(uid)
            return await edit(c,
                f"📊 <b>{F_.h1('Limitlarim')}</b>\nID: <code>{uid}</code>\n"
                f'🤖 Botlar: {db.count_hosts(uid)}/{botlim or "∞"}\n'
                f'🧠 AI bugun (UTC): {db.ai_used(uid)}/{ailim or "∞"}\n'
                f'{esc(limits.promo_status(uid))}\n'
                '∞ — dastur kvotasi yo‘q. Telegram/API, fizik RAM/CPU va xavfsizlik chegaralari saqlanadi.',
                kb([('🎁 Promo', 'v2:promo')], [(BACK, 'home')]))
        if action == 'promo':
            return await prompt(c, state, action, '🎁 Promo kodni yuboring. Kod kunlik AI va botlar soni kvotasini ochadi.', 'home')
        if action == 'users':
            text, markup = users_view(int(bits[2]) if len(bits) > 2 else 0)
            return await edit(c, text, markup)
        if action == 'user':
            text, markup = user_view(int(bits[2]))
            return await edit(c, text, markup)
        if action == 'setlimits':
            if len(bits) > 2:
                target = int(bits[2])
                return await prompt(c, state, action,
                    f'⚖️ <code>{target}</code> uchun format: <code>BOT_SONI AI_KUNLIK</code>\nMasalan: <code>5 30</code>\n'
                    '0 — standart limit. Admin va faol promo kvotadan ozod.', f'v2:user:{target}', target=target)
            return await prompt(c, state, action,
                '⚖️ Format: <code>USER_ID BOT_SONI AI_KUNLIK</code>\nMasalan: <code>123456789 5 30</code>\n'
                '0 — standart limit. Admin va faol promo kvotadan ozod.', 'v2:users:0')
        if action == 'revokepromo':
            target = int(bits[2])
            limits.revoke_promo(target)
            text, markup = user_view(target)
            return await edit(c, '✅ Promo huquqi bekor qilindi.\n\n' + text, markup)
        if action == 'promos':
            text, markup = promos_view()
            return await edit(c, text, markup)
        if action == 'promotoggle':
            code = bits[2]
            p = db.get_promo(code)
            if not p:
                raise ValueError('Promo topilmadi.')
            db.set_promo_active(code, not p['active'])
            text, markup = promos_view()
            return await edit(c, text, markup)
        if action == 'newpromo':
            return await prompt(c, state, action, '🎁 Format: <code>KOD KUN ISHLATISH_SONI</code>\n'
                'Masalan: <code>summer 30 100</code>\n0 kun — doimiy; 0 ishlatish — cheksiz. Kod 32 belgigacha.', 'v2:promos')
        if action == 'diagnostic':
            from main import m_aicheck
            return await m_aicheck(c.message, state)
        # ---- broadcast ----
        if action == 'bc':
            text, markup = bc_menu()
            return await edit(c, text, markup)
        if action == 'bcnew':
            rows = [[(f'{title} · {len(bc_audience(kind))}', f'v2:bcaud:{kind}')]
                    for kind, title in broadcast.AUDIENCE_TITLES.items()]
            rows.append([(BACK, 'v2:bc')])
            return await edit(c, f"📣 <b>{F_.h1('Kimga yuboramiz?')}</b>\nBotni bloklaganlar avtomatik chiqarib tashlanadi.", kb(*rows))
        if action == 'bcaud':
            kind = bits[2] if len(bits) > 2 else ''
            if kind not in db.AUDIENCES:
                raise ValueError('Auditoriya noto‘g‘ri.')
            if broadcast.ACTIVE is not None and broadcast.ACTIVE.status == 'running':
                raise ValueError('Boshqa broadcast hali tugamagan. Tugashini kuting yoki to‘xtating.')
            n = len(bc_audience(kind))
            if not n:
                raise ValueError('Bu auditoriyada qabul qiluvchi yo‘q.')
            await state.set_state(Broadcast.content)
            await state.update_data(bc_aud=kind)
            return await c.message.answer(
                f"✍️ <b>{F_.h1('Xabarni yuboring')}</b>\n{broadcast.AUDIENCE_TITLES[kind]}: <b>{n}</b> ta qabul qiluvchi.\n"
                'Matn, rasm, video yoki fayl yuboring — formatlash (qalin, havola...) saqlanadi.',
                reply_markup=cancel_kb('v2:bc'))
        if action == 'bcbtn':
            data = await state.get_data()
            if not data.get('bc_msg'):
                raise ValueError('Sessiya tugagan. Qaytadan boshlang.')
            await state.set_state(Input.text)
            await state.update_data(action='bcbuttons', cancel='v2:bc')
            return await c.message.answer(
                '🔗 Tugmalarni har qatorga <code>Matn | https://havola</code> ko‘rinishida yozing '
                f'(bir qatorda <code>;</code> bilan bir nechta, ko‘pi bilan {broadcast.MAX_BUTTONS} ta).\n'
                'Olib tashlash uchun «-» yuboring.', reply_markup=cancel_kb('v2:bc'))
        if action == 'bctest':
            data = await state.get_data()
            if not data.get('bc_msg'):
                raise ValueError('Sessiya tugagan. Qaytadan boshlang.')
            await bot.copy_message(chat_id=c.message.chat.id, from_chat_id=data['bc_chat'], message_id=data['bc_msg'],
                                   reply_markup=bc_markup(data.get('bc_buttons')))
            return await c.message.answer('🧪 Sinov xabari shu tarzda boradi. Mos bo‘lsa «🚀 Yuborish» ni bosing.')
        if action == 'bcgo':
            return await bc_start(c, state, bot)
        if action == 'bcstop':
            job = broadcast.ACTIVE
            stopped = broadcast.stop(int(bits[2])) if len(bits) > 2 and bits[2].isdigit() else False
            if not stopped and not (job and job.status == 'running'):
                raise ValueError('Faol broadcast yo‘q.')
            return await c.message.answer('⏹ To‘xtatish buyrug‘i berildi: yuborilganlar saqlanadi, qolganlari yuborilmaydi.')
        if action == 'bchist':
            rows = db.list_broadcasts(10)
            names = {'running': '⏳', 'done': '✅', 'cancelled': '⏹', 'error': '❌'}
            lines = [f"{names.get(r['status'], '•')} #{r['id']} · {time.strftime('%d.%m %H:%M', time.gmtime(r['created']))} · "
                     f"{r['sent']}/{r['total']} (🚫{r['blocked']} ❌{r['failed']}) · {esc(r['preview'][:30])}" for r in rows]
            return await edit(c, f"📜 <b>{F_.h1('Broadcast tarixi')}</b> (UTC)\n" + ('\n'.join(lines) or 'Hali yo‘q.'), back_kb('v2:bc'))
        # ---- AI modellar / loyiha ----
        if action == 'models':
            rows = [[(('✅ ' if model == aiex.selected(uid) else '') + model, f'v2:model:{i}')]
                    for i, model in enumerate(aiex.models())]
            return await edit(c, '🧠 Bir xil AI_BASE_URL/API_KEY xizmatidagi modellar. Provayder modelni qo‘llashi kerak.',
                              kb(*rows, [(BACK, 'ai')]))
        if action == 'model':
            model = aiex.models()[int(bits[2])]
            db.kv_set(f'ai_model:{uid}', model)
            return await edit(c, f'✅ Model: <code>{esc(model)}</code>', kb([('🧠 AI menyu', 'ai')]))
        if action == 'project':
            if not ai.enabled():
                raise ValueError('API_KEY sozlanmagan.')
            from main import AI
            await state.set_state(AI.prompt)
            await state.update_data(multi=True, lang='py', hid=None, history=[])
            return await c.message.answer('🗂 Ko‘p fayllik loyiha tavsifini yozing (backend Python/PHP bo‘lishi mumkin).\n'
                'Mini App uchun index.html + server yaratiladi. GitHub Actions doimiy ochiq HTTPS manzil bermaydi; '
                'Mini Appni ishlatish uchun tashqi HTTPS domen/hosting kerak.', reply_markup=cancel_kb('ai'))
        # ---- bot egasi amallari ----
        if action == 'secrets':
            rows = [[('➕ Kalit qo‘shish / yangilash', f'v2:secretadd:{h["id"]}')]]
            text = f"🔐 <b>{F_.h1('Maxfiy o‘zgaruvchilar')}</b>\nQiymatlar shifrlangan, ekranga chiqarilmaydi.\n"
            for i, (name, value) in enumerate(bot_secrets.items(h['id'], mask=True)):
                text += f'• <code>{esc(name)}</code>: ••••••••\n'
                rows.append([(f'🗑 {name}', f'v2:secretdel:{h["id"]}:{i}')])
            rows.append([(BACK, f'p:{h["id"]}')])
            return await edit(c, text, kb(*rows))
        if action == 'secretadd':
            return await prompt(c, state, action, '🔐 <code>OPENAI_KEY=qiymat</code> ko‘rinishida yuboring. Xabar o‘chiriladi.\n'
                'Host kalitlari meros qilinmaydi; siz kiritgan kalit faqat shu botga beriladi. Saqlangach botni qayta ishga tushiring.',
                f'v2:secrets:{h["id"]}', hid=h['id'])
        if action == 'secretdel':
            items = db.list_secrets(h['id'])
            item = items[int(bits[3])]
            bot_secrets.delete(h['id'], item['key'])
            return await edit(c, '✅ Kalit o‘chirildi. Botni qayta ishga tushiring.', kb([('🔐 Kalitlar', f'v2:secrets:{h["id"]}')]))
        if action == 'res':
            stats = runner.res_stats(h['id'])
            text = ('🔴 Bot ishlamayapti.' if not stats else
                    f'📊 <b>{esc(h["name"])}</b>\n{F_.label("RAM")}: {runner.human(stats["ram"])}\n'
                    f'{F_.label("CPU")}: {stats["cpu"]:.1f}%\nPID: {stats["pid"]} · oqimlar: {stats["threads"]}\n'
                    'CPU birinchi o‘lchashda 0 bo‘lishi mumkin; yangilang.')
            return await edit(c, text, kb([('🔄 Yangilash', f'v2:res:{h["id"]}')], [(BACK, f'p:{h["id"]}')]))
        if action == 'explain':
            from main import AI_BUSY, AI_LAST, AI_COOLDOWN
            if not ai.enabled():
                raise ValueError('API_KEY sozlanmagan.')
            available, used, lim = limits.ai_left(uid)
            if not available:
                raise ValueError(f'Kunlik AI limiti tugadi: {used}/{lim}.')
            if uid in AI_BUSY or time.time() - AI_LAST.get(uid, 0) < AI_COOLDOWN:
                raise ValueError('Oldingi AI so‘rovi tugashini kuting.')
            if not h['entry']:
                raise ValueError('Asosiy fayl yo‘q.')
            path = runner.safe_join(db.bot_dir(h['id']), h['entry'])
            if os.path.getsize(path) > ai.MAX_CODE_CTX:
                raise ValueError('Asosiy fayl AI uchun juda katta.')
            text = bot_secrets.redact(Path(path).read_text(errors='replace'), h['id'])
            AI_BUSY.add(uid)
            limits.ai_consume(uid)
            token = aiex.SELECTED_MODEL.set(aiex.selected(uid))
            try:
                answer = await aiex.explain(text, h['entry'])
            except Exception:
                limits.ai_refund(uid)   # javob olinmagan so'rov kvotani yemaydi
                raise
            finally:
                aiex.SELECTED_MODEL.reset(token)
                AI_BUSY.discard(uid)
                AI_LAST[uid] = time.time()
            # Chunk plain text safely after HTML escaping; «Orqaga» faqat oxirgi bo'lakda.
            chunks = [answer[i:i + 2800] for i in range(0, len(answer), 2800)] or ['(bo‘sh javob)']
            for i, chunk in enumerate(chunks):
                last = i == len(chunks) - 1
                await c.message.answer(esc(chunk), reply_markup=back_kb(f'p:{h["id"]}') if last else None)
            return
        if action == 'gitupdate':
            return await edit(c, '📦 GitHub kodi yangilanadi. Runtime bazalari saqlanadi, o‘zgartirilgan kod almashtiriladi.',
                kb([('✅ Yangilash', f'v2:gitconfirm:{h["id"]}')], [(CANCEL, f'p:{h["id"]}')]))
        if action == 'gitconfirm':
            if not h['git_repo']:
                raise ValueError('GitHub repo sozlanmagan.')
            await update_github(h)
            from main import show_panel
            return await show_panel(c, h['id'], '✅ GitHub kodi yangilandi.')
        raise ValueError('Tugma eskirgan yoki mavjud emas.')
    except (ValueError, IndexError, KeyError, OSError, ai.AIError) as e:
        await c.message.answer('❌ ' + esc(str(e)[:700]), reply_markup=back_kb(parent_of(action, bits)))
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception('v2 callback failed')
        await c.message.answer('❌ Amal bajarilmadi: ' + esc(backup.explain_error(e)[:400]),
                               reply_markup=back_kb(parent_of(action, bits)))


def _channel_from(m, text):
    """Kanal ko'rsatkichi: forward qilingan post, ID yoki @username / t.me havolasi."""
    origin = getattr(m, 'forward_origin', None)
    chat = getattr(origin, 'chat', None) if origin else None
    chat = chat or getattr(m, 'forward_from_chat', None)
    if chat is not None:
        return chat.id
    raw = text.strip()
    if re.fullmatch(r'-?\d+', raw):
        return int(raw)
    link = re.fullmatch(r'(?:https?://)?t\.me/([A-Za-z0-9_]{4,})/?', raw)
    if link:
        return '@' + link.group(1)
    if re.fullmatch(r'@[A-Za-z0-9_]{4,}', raw):
        return raw
    raise ValueError('Kanal ID (-100...), @username yuboring yoki kanaldan postni forward qiling. '
                     'Private kanalning taklif havolasi ishlamaydi.')


async def input_text(m, state, bot):
    data = await state.get_data()
    action = data.get('action')
    uid = m.from_user.id
    back = data.get('cancel') or 'home'
    if action in ADMIN_INPUTS and not limits.is_admin(uid):
        await state.clear()
        return await m.answer('⛔ Faqat admin uchun', reply_markup=back_kb('home'))
    text = m.text or ''
    try:
        if action == 'channel':
            chat = await backup.check_channel(bot, _channel_from(m, text))
            db.set_backup_channel(chat.id, chat.title)
            await state.clear()
            env_ok = backup.summary()['env_ok']
            note = ('' if env_ok else
                    f'\n\n⚠️ Endi shu ID ni GitHub Secret <code>BACKUP_CHANNEL_ID</code> ga yozing: <code>{chat.id}</code>. '
                    'Busiz yangi runnerda ma’lumotlar avtomatik tiklanmaydi.')
            return await m.answer(f'✅ Kanal saqlandi: {esc(chat.title)} · <code>{chat.id}</code>.\n'
                'Oxirgi zaxira xabarini pin holatida qoldiring.' + note,
                reply_markup=kb([('🔎 Tekshirish', 'v2:channelcheck'), ('💾 Hozir zaxiralash', 'v2:backup')], [(BACK, 'v2:bkp')]))
        if action == 'promo':
            ok, msg = limits.apply_promo(uid, text)
            if ok:
                await state.clear()
                return await m.answer(esc(msg), reply_markup=kb([('📊 Limitlarim', 'v2:limits')], [(BACK, 'home')]))
            return await m.answer(esc(msg), reply_markup=cancel_kb(back))
        if action == 'setlimits':
            parts = text.split()
            target = data.get('target')
            if target:
                if len(parts) != 2:
                    raise ValueError('Format: BOT_SONI AI_KUNLIK')
                bots, daily = map(int, parts)
            else:
                if len(parts) != 3:
                    raise ValueError('Format: USER_ID BOT_SONI AI_KUNLIK')
                target, bots, daily = map(int, parts)
            if target <= 0 or not 0 <= bots <= 100000 or not 0 <= daily <= 100000:
                raise ValueError('Qiymatlar noto‘g‘ri.')
            db.set_user(target, max_bots=bots, ai_daily=daily)
            await state.clear()
            return await m.answer('✅ Foydalanuvchi limitlari saqlandi.',
                                  reply_markup=kb([('👤 Foydalanuvchi', f'v2:user:{target}')], [(BACK, 'v2:users:0')]))
        if action == 'newpromo':
            code, days, uses = text.lower().split()
            days, uses = int(days), int(uses)
            if not re.fullmatch(r'[a-z0-9_-]{2,32}', code) or not 0 <= days <= 36500 or uses < 0:
                raise ValueError('Kod yoki sonlar noto‘g‘ri.')
            if db.get_promo(code):
                raise ValueError('Kod mavjud. Boshqa kod yozing.')
            db.add_promo(code, days, uses, uid)
            await state.clear()
            return await m.answer('✅ Promo yaratildi.', reply_markup=back_kb('v2:promos'))
        if action == 'bcbuttons':
            if not data.get('bc_msg'):
                raise ValueError('Sessiya tugagan. Qaytadan boshlang.')
            rows = [] if text.strip() == '-' else broadcast.parse_buttons(text)
            await state.update_data(bc_buttons=[[list(b) for b in row] for row in rows])
            await state.set_state(Broadcast.content)
            return await bc_show_confirm(bot, m.chat.id, state)
        if action == 'secretadd':
            h = owned(uid, data.get('hid'))
            if not h:
                raise ValueError('Ruxsat yo‘q.')
            try:
                await m.delete()
            except Exception:
                pass
            if '=' not in text:
                raise ValueError('Format: OPENAI_KEY=qiymat')
            name, value = text.split('=', 1)
            name = bot_secrets.set_value(h['id'], name, value.strip())
            await state.clear()
            return await m.answer(f'✅ <code>{esc(name)}</code> shifrlanib saqlandi. Restart qiling.',
                reply_markup=kb([('🔄 Restart', f'rs:{h["id"]}'), ('🔐 Kalitlar', f'v2:secrets:{h["id"]}')]))
        raise ValueError('Sessiya tugagan. Orqaga qayting.')
    except (ValueError, OSError) as e:
        return await m.answer('❌ ' + esc(str(e)), reply_markup=cancel_kb(back))
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception('v2 input failed')
        return await m.answer('❌ Bajarilmadi: ' + esc(backup.explain_error(e)[:300]), reply_markup=cancel_kb(back))


async def restore_upload(m, state):
    if not limits.is_admin(m.from_user.id):
        await state.clear()
        return await m.answer('⛔ Faqat admin uchun', reply_markup=back_kb('home'))
    doc = m.document
    if not doc or not (doc.file_name or '').lower().endswith('.zip') or (doc.file_size or 0) > backup.MAX_ARCHIVE:
        return await m.answer('❌ To‘liq v2 zaxira ZIP faylini yuboring (19 MB gacha).', reply_markup=cancel_kb('v2:bkp'))
    await state.set_state(Input.restore_confirm)
    await state.update_data(restore_file=doc.file_id)
    await m.answer('⚠️ Hozirgi baza va barcha bot fayllari almashtiriladi. Ishonchli zaxira ekanini tekshiring.',
                   reply_markup=kb([('✅ Tiklash', 'v2:restoreyes')], [(CANCEL, 'v2:bkp')]))


def register(router):
    router.callback_query.register(callback, F.data.startswith('v2:'))
    router.message.register(input_text, Input.text)
    router.message.register(restore_upload, Input.restore)
    router.message.register(bc_content, Broadcast.content)

    async def fallback(m):
        from main import main_menu
        await m.answer('Kerakli amalni tugmalardan tanlang 👇', reply_markup=main_menu(m.from_user.id))
    router.message.register(fallback)
