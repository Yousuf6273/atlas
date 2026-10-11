"""Persistent HTTP response cache (separate SQLite file, zlib-compressed bodies)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

CACHEABLE_STATUSES = {200, 203, 204, 300, 301, 404, 410}


@dataclass
class CachedResponse:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    content: bytes
    fetched_at: float


class HttpCache:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, url TEXT NOT NULL, final_url TEXT NOT NULL, "
            "status INTEGER NOT NULL, headers TEXT NOT NULL, body BLOB NOT NULL, fetched_at REAL NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_http_cache_exp ON http_cache(expires_at)")

    @staticmethod
    def make_key(method: str, url: str, body: bytes | None = None) -> str:
        h = hashlib.sha256()
        h.update(method.upper().encode())
        h.update(b"\0")
        h.update(url.encode())
        if body:
            h.update(b"\0")
            h.update(body)
        return h.hexdigest()

    def get(self, key: str) -> CachedResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT url, final_url, status, headers, body, fetched_at, expires_at FROM http_cache WHERE key = ?", (key,)
            ).fetchone()
        if not row or row[6] < time.time():
            return None
        try:
            body = zlib.decompress(row[4])
        except zlib.error:
            return None
        return CachedResponse(url=row[0], final_url=row[1], status=row[2], headers=json.loads(row[3]), content=body,
                              fetched_at=row[5])

    def put(self, key: str, *, url: str, final_url: str, status: int, headers: dict[str, str], content: bytes,
            ttl_s: float) -> None:
        if status not in CACHEABLE_STATUSES or ttl_s <= 0:
            return
        now = time.time()
        keep = {k.lower(): v for k, v in headers.items() if k.lower() in {"content-type", "last-modified", "etag", "location"}}
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache(key, url, final_url, status, headers, body, fetched_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, url, final_url, status, json.dumps(keep), zlib.compress(content, 6), now, now + ttl_s),
            )

    def purge_expired(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM http_cache WHERE expires_at < ?", (time.time(),))
        return cur.rowcount

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM http_cache")

    def stats(self) -> dict[str, float]:
        with self._lock:
            n, size = self._conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM http_cache").fetchone()
        return {"entries": n, "compressed_bytes": size}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
