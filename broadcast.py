"""Broadcast mexanizmi: tezlik nazorati, qayta urinish, bloklaganlarni belgilash, bekor qilish.

aiogram'ga bog'liq emas: xatolar sinf nomi bo'yicha aniqlanadi, yuboruvchi funksiya
(`send(bot, uid)`) tashqaridan beriladi. Shu sabab offline test qilish oson.
"""
import asyncio
import re
import time

import db

RATE = 20          # soniyasiga xabar (Telegram umumiy chegarasi ~30/s)
WORKERS = 8        # parallel yuboruvchilar
MAX_ATTEMPTS = 3
PROGRESS_EVERY = 3.0
MAX_BUTTONS = 6

JOBS: dict = {}
ACTIVE = None      # hozir ishlayotgan Job yoki None

AUDIENCE_TITLES = {
    "all": "👥 Hammaga",
    "owners": "🤖 Bot egalariga",
    "promo": "🎁 Promo foydalanuvchilarga",
    "admins": "👑 Adminlarga",
}


class Job:
    def __init__(self, bid, admin_id, audience, recipients):
        self.id = bid
        self.admin_id = admin_id
        self.audience = audience
        self.recipients = list(recipients)
        self.total = len(self.recipients)
        self.sent = 0
        self.blocked = 0
        self.failed = 0
        self.status = "running"      # running | done | cancelled | error
        self.started = time.time()
        self.finished = 0.0
        self.cancel = asyncio.Event()
        self.errors: dict = {}

    @property
    def processed(self):
        return self.sent + self.blocked + self.failed

    @property
    def percent(self):
        return 100 if not self.total else int(self.processed * 100 / self.total)

    @property
    def eta(self):
        """Taxminiy qolgan soniyalar."""
        done = self.processed
        if not done or self.status != "running":
            return 0
        spent = time.time() - self.started
        return int(spent / done * (self.total - done))


def bar(percent, width=10):
    filled = max(0, min(width, round(percent * width / 100)))
    return "▰" * filled + "▱" * (width - filled)


def _names(exc):
    return {c.__name__ for c in type(exc).__mro__}


def classify(exc):
    """'retry' | 'blocked' | 'network' | 'failed'."""
    names = _names(exc)
    text = str(exc).lower()
    if "TelegramRetryAfter" in names or hasattr(exc, "retry_after"):
        return "retry"
    if "TelegramForbiddenError" in names:
        return "blocked"
    if "TelegramBadRequest" in names:
        if any(k in text for k in ("chat not found", "user is deactivated", "peer_id_invalid", "bot was blocked")):
            return "blocked"
        return "failed"
    if names & {"TelegramNetworkError", "TelegramServerError", "TimeoutError", "ClientError"}:
        return "network"
    return "failed"


async def _deliver(bot, job, uid, send, pause, sleep):
    for attempt in range(MAX_ATTEMPTS):
        wait = pause["until"] - time.monotonic()
        if wait > 0:
            await sleep(wait)
        try:
            await send(bot, uid)
            job.sent += 1
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            kind = classify(e)
            if kind == "retry":
                delay = float(getattr(e, "retry_after", 1) or 1) + 1
                pause["until"] = max(pause["until"], time.monotonic() + delay)
                continue
            if kind == "blocked":
                job.blocked += 1
                try:
                    db.set_blocked(uid, True)
                except Exception:
                    pass
                return
            if kind == "network" and attempt < MAX_ATTEMPTS - 1:
                await sleep(1 + attempt)
                continue
            job.failed += 1
            key = type(e).__name__
            job.errors[key] = job.errors.get(key, 0) + 1
            return
    job.failed += 1
    job.errors["RetryAfter"] = job.errors.get("RetryAfter", 0) + 1


def _persist(job):
    try:
        db.update_broadcast(job.id, sent=job.sent, blocked=job.blocked, failed=job.failed,
                            status=job.status, finished=job.finished)
    except Exception:
        pass


async def run(bot, job, send, progress=None, rate=RATE, workers=WORKERS, sleep=asyncio.sleep):
    """Qabul qiluvchilarni parallel (lekin tezlik cheklovi bilan) yuboradi."""
    queue: asyncio.Queue = asyncio.Queue()
    for uid in job.recipients:
        queue.put_nowait(uid)
    pause = {"until": 0.0}
    gap = workers / float(rate)

    async def worker():
        while not job.cancel.is_set():
            try:
                uid = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await _deliver(bot, job, uid, send, pause, sleep)
            await sleep(gap)

    async def ticker():
        while True:
            await asyncio.sleep(PROGRESS_EVERY)
            _persist(job)
            if progress:
                try:
                    await progress(job)
                except Exception:
                    pass

    tick = asyncio.create_task(ticker())
    try:
        await asyncio.gather(*(worker() for _ in range(max(1, min(workers, job.total or 1)))))
        job.status = "cancelled" if job.cancel.is_set() else "done"
    except asyncio.CancelledError:
        job.status = "cancelled"
        raise
    except Exception:
        job.status = "error"
        raise
    finally:
        tick.cancel()
        await asyncio.gather(tick, return_exceptions=True)
        job.finished = time.time()
        _persist(job)
    if progress:
        try:
            await progress(job)
        except Exception:
            pass
    return job


def start(bot, admin_id, audience, recipients, send, preview="", progress=None, done=None):
    """Fon vazifasi sifatida ishga tushiradi. Bir vaqtda bitta broadcast."""
    global ACTIVE
    if ACTIVE is not None and ACTIVE.status == "running":
        raise ValueError("Boshqa broadcast hali tugamagan. Tugashini kuting yoki to‘xtating.")
    if not recipients:
        raise ValueError("Qabul qiluvchilar yo‘q.")
    bid = db.add_broadcast(admin_id, audience, preview, len(recipients))
    job = Job(bid, admin_id, audience, recipients)
    JOBS[bid] = job
    ACTIVE = job

    async def main():
        global ACTIVE
        try:
            await run(bot, job, send, progress)
        finally:
            if ACTIVE is job:
                ACTIVE = None
            if done:
                try:
                    await done(job)
                except Exception:
                    pass

    job.task = asyncio.create_task(main())
    return job


def stop(bid=None):
    job = JOBS.get(bid) if bid else ACTIVE
    if job and job.status == "running":
        job.cancel.set()
        return True
    return False


def parse_buttons(text):
    """«Matn | https://...» qatorlaridan URL tugmalar. Bir qatorda `;` bilan bir nechta."""
    rows, count = [], 0
    for line in (text or "").splitlines():
        row = []
        for part in line.split(";"):
            part = part.strip()
            if not part:
                continue
            if "|" not in part:
                raise ValueError(f"Format: Matn | https://havola — «{part[:30]}» noto‘g‘ri.")
            label, url = (x.strip() for x in part.rsplit("|", 1))
            if not label or len(label) > 40:
                raise ValueError("Tugma matni 1–40 belgi bo‘lsin.")
            if not re.fullmatch(r"(https?://|tg://)[^\s]+", url):
                raise ValueError(f"Havola http(s):// yoki tg:// bilan boshlansin: {url[:40]}")
            row.append((label, url))
            count += 1
            if count > MAX_BUTTONS:
                raise ValueError(f"Ko‘pi bilan {MAX_BUTTONS} ta tugma.")
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("Tugma topilmadi.")
    return rows


def fmt_duration(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec} soniya"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} daq {s} soniya"
    h, m = divmod(m, 60)
    return f"{h} soat {m} daq"
