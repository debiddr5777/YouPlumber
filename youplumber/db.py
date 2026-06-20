"""SQLite database layer for YouPlumber library tracking."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,           -- channel | playlist | search | single
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    last_synced INTEGER,
    meta        TEXT,                    -- JSON blob
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    video_id     TEXT NOT NULL UNIQUE,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    uploader     TEXT,
    channel      TEXT,
    duration     INTEGER,                -- seconds
    upload_date  TEXT,                   -- YYYYMMDD
    description  TEXT,
    thumbnail    TEXT,
    view_count   INTEGER,
    like_count   INTEGER,

    -- audio analysis
    bpm          REAL,
    musical_key  TEXT,                   -- e.g. "A minor"
    camelot_key  TEXT,                   -- e.g. "8A"
    energy       REAL,                   -- 0..1
    loudness_lufs REAL,
    bitrate      INTEGER,
    sample_rate  INTEGER,

    -- status
    status       TEXT NOT NULL DEFAULT 'new',  -- new | queued | downloading | done | failed | skipped
    last_error   TEXT,
    retries      INTEGER DEFAULT 0,

    -- files
    file_path    TEXT,
    file_size    INTEGER,

    -- timestamps
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_status     ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_source     ON tracks(source_id);
CREATE INDEX IF NOT EXISTS idx_tracks_uploader   ON tracks(uploader);
CREATE INDEX IF NOT EXISTS idx_tracks_upload     ON tracks(upload_date);
CREATE INDEX IF NOT EXISTS idx_tracks_bpm        ON tracks(bpm);
CREATE INDEX IF NOT EXISTS idx_tracks_camelot    ON tracks(camelot_key);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,           -- download | analyze | convert
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  INTEGER,
    finished_at INTEGER,
    progress    REAL DEFAULT 0,
    speed       REAL,
    eta         INTEGER,
    log         TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    config.ensure_dirs()
    p = path or config.DB_PATH
    conn = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> sqlite3.Connection:
    conn = connect()
    conn.executescript(SCHEMA)
    return conn


def upsert_source(conn: sqlite3.Connection, kind: str, name: str, url: str) -> int:
    now = int(time.time())
    row = conn.execute(
        "SELECT id FROM sources WHERE url=?", (url,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources(kind, name, url, created_at) VALUES (?,?,?,?)",
        (kind, name, url, now),
    )
    return cur.lastrowid


def touch_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute(
        "UPDATE sources SET last_synced=? WHERE id=?",
        (int(time.time()), source_id),
    )


def upsert_track(conn: sqlite3.Connection, t: dict[str, Any]) -> int:
    """Insert or update a track by video_id."""
    now = int(time.time())
    existing = conn.execute(
        "SELECT id FROM tracks WHERE video_id=?", (t["video_id"],)
    ).fetchone()
    cols = [
        "source_id", "video_id", "url", "title", "uploader", "channel",
        "duration", "upload_date", "description", "thumbnail",
        "view_count", "like_count", "created_at", "updated_at",
    ]
    vals = [
        t.get("source_id"),
        t["video_id"],
        t["url"],
        t["title"],
        t.get("uploader"),
        t.get("channel"),
        t.get("duration"),
        t.get("upload_date"),
        t.get("description"),
        t.get("thumbnail"),
        t.get("view_count"),
        t.get("like_count"),
        now,
        now,
    ]
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols if c != "video_id")
        conn.execute(
            f"UPDATE tracks SET {sets} WHERE video_id=?",
            [*[v for c, v in zip(cols, vals) if c != "video_id"], t["video_id"]],
        )
        return existing["id"]
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO tracks({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    return cur.lastrowid


def set_status(
    conn: sqlite3.Connection, track_id: int, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE tracks SET status=?, last_error=?, updated_at=? WHERE id=?",
        (status, error, int(time.time()), track_id),
    )


def set_analysis(
    conn: sqlite3.Connection, track_id: int, **kwargs: Any
) -> None:
    allowed = {"bpm", "musical_key", "camelot_key", "energy",
               "loudness_lufs", "bitrate", "sample_rate"}
    sets, vals = [], []
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(int(time.time()))
    vals.append(track_id)
    conn.execute(
        f"UPDATE tracks SET {', '.join(sets)} WHERE id=?",
        vals,
    )


def set_file(
    conn: sqlite3.Connection, track_id: int, file_path: str, file_size: int
) -> None:
    conn.execute(
        "UPDATE tracks SET file_path=?, file_size=?, status='done', "
        "updated_at=? WHERE id=?",
        (file_path, file_size, int(time.time()), track_id),
    )


def get_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tracks WHERE id=?", (track_id,)
    ).fetchone()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM tracks GROUP BY status"
    ).fetchall()
    out = {r["status"]: r["c"] for r in rows}
    out["total"] = sum(out.values())
    return out


def library_size_bytes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(file_size), 0) s FROM tracks WHERE status='done'"
    ).fetchone()
    return int(row["s"] or 0)


def list_tracks(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    source_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[sqlite3.Row]:
    q = "SELECT * FROM tracks"
    args: list[Any] = []
    where = []
    if status:
        where.append("status=?")
        args.append(status)
    if source_id is not None:
        where.append("source_id=?")
        args.append(source_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return list(conn.execute(q, args))


def count_tracks(
    conn: sqlite3.Connection, *, status: str | None = None
) -> int:
    if status:
        return conn.execute(
            "SELECT COUNT(*) c FROM tracks WHERE status=?", (status,)
        ).fetchone()["c"]
    return conn.execute("SELECT COUNT(*) c FROM tracks").fetchone()["c"]


def next_queued(conn: sqlite3.Connection, limit: int = 1) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM tracks WHERE status='queued' "
        "ORDER BY id LIMIT ?",
        (limit,),
    ))
