"""Users' own SauceNAO API keys: one per Telegram user, shared by every bot, in SQLite.

Each call opens its own connection in a worker thread, so the event loop never
waits on the disk; at a few lookups per search that costs nothing.
"""

import asyncio
import contextlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS saucenao_keys (
    user_id INTEGER PRIMARY KEY,
    api_key TEXT NOT NULL,
    -- 0 once SauceNAO rejected the key: kept to tell the user, never used again
    valid INTEGER NOT NULL DEFAULT 1
)
"""


@dataclass(frozen=True)
class UserKey:
    api_key: str
    valid: bool

    def __repr__(self) -> str:
        return f"UserKey({self.masked}, valid={self.valid})"

    @property
    def masked(self) -> str:
        """The key's tail, the most of it Mikke ever shows."""
        return f"…{self.api_key[-4:]}"


class KeyStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._execute(SCHEMA)

    async def get(self, user_id: int) -> UserKey | None:
        rows, _ = await self._run("SELECT api_key, valid FROM saucenao_keys WHERE user_id = ?", user_id)
        return UserKey(rows[0][0], bool(rows[0][1])) if rows else None

    async def set(self, user_id: int, api_key: str) -> None:
        await self._run(
            "INSERT INTO saucenao_keys (user_id, api_key) VALUES (?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET api_key = excluded.api_key, valid = 1",
            user_id,
            api_key,
        )

    async def remove(self, user_id: int) -> bool:
        """Forget the user's key; False if there was none."""
        _, changed = await self._run("DELETE FROM saucenao_keys WHERE user_id = ?", user_id)
        return changed > 0

    async def invalidate(self, user_id: int, api_key: str) -> bool:
        """Mark the key SauceNAO rejected; True only for the call that did it, so the user is told once.

        Nothing happens if the user has replaced that key in the meantime.
        """
        _, changed = await self._run(
            "UPDATE saucenao_keys SET valid = 0 WHERE user_id = ? AND api_key = ? AND valid = 1", user_id, api_key
        )
        return changed > 0

    async def _run(self, sql: str, *params: Any) -> tuple[list[tuple], int]:
        return await asyncio.to_thread(self._execute, sql, params)

    def _execute(self, sql: str, params: tuple = ()) -> tuple[list[tuple], int]:
        """Rows and the number of rows changed, in a transaction of its own."""
        with contextlib.closing(sqlite3.connect(self._path)) as db, db:
            cursor = db.execute(sql, params)
            return cursor.fetchall(), cursor.rowcount
