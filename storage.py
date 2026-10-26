"""FSM holatlari SQLite'da: bot qayta ishga tushsa ham suhbatlar yo'qolmaydi."""
import json
import time

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import db


def _k(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{getattr(key, 'thread_id', None) or 0}:{key.destiny}"


class SqliteStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state=None) -> None:
        value = state.state if isinstance(state, State) else state
        with db.conn() as c:
            c.execute("INSERT INTO fsm(key,state,data,updated) VALUES(?,?,'{}',?) "
                      "ON CONFLICT(key) DO UPDATE SET state=excluded.state, updated=excluded.updated",
                      (_k(key), value, time.time()))

    async def get_state(self, key: StorageKey):
        with db.conn() as c:
            r = c.execute("SELECT state FROM fsm WHERE key=?", (_k(key),)).fetchone()
        return r[0] if r else None

    async def set_data(self, key: StorageKey, data) -> None:
        try:
            raw = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = "{}"
        with db.conn() as c:
            c.execute("INSERT INTO fsm(key,state,data,updated) VALUES(?,NULL,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                      (_k(key), raw, time.time()))

    async def get_data(self, key: StorageKey) -> dict:
        with db.conn() as c:
            r = c.execute("SELECT data FROM fsm WHERE key=?", (_k(key),)).fetchone()
        if not r:
            return {}
        try:
            return json.loads(r[0])
        except ValueError:
            return {}

    async def close(self) -> None:
        pass
