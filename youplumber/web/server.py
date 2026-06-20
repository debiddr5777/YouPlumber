"""FastAPI web server for YouPlumber."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import config as cfg
from .. import db as db_module
from .. import sources
from ..finalize import finalize as do_finalize
from ..downloader import DownloadQueue, ProgressReporter

log = logging.getLogger("youplumber.web")

# ---------- global state ----------

_download_queue: DownloadQueue | None = None
_download_thread: threading.Thread | None = None
_progress_reporter = ProgressReporter()
_ws_clients: set[WebSocket] = set()
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Return a per-thread connection (safe for the background worker + web handlers)."""
    return db_module.init_db()


# ---------- lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = _get_conn()
    conn.execute("UPDATE tracks SET status='new' WHERE status='downloading'")
    conn.commit()
    task = asyncio.create_task(_broadcast_progress())
    yield
    task.cancel()
    if _download_queue:
        _download_queue.stop()


app = FastAPI(title="YouPlumber", docs_url="/api/docs", lifespan=lifespan)


# ---------- Pydantic models ----------

class AddSourceBody(BaseModel):
    url: str
    limit: int = 50
    name: str | None = None

class QueueBody(BaseModel):
    track_ids: list[int] | None = None
    all_new: bool = False
    source_id: int | None = None
    reset: bool = False

class FinalizeBody(BaseModel):
    output: str | None = None
    organize_by: str | None = None
    mode: str = "move"  # "move" or "copy"

class ConfigUpdate(BaseModel):
    key: str
    value: Any


# ---------- progress broadcaster ----------

async def _broadcast_progress():
    while True:
        await asyncio.sleep(0.3)
        if not _ws_clients:
            continue
        snapshot = _progress_reporter.snapshot()
        q = _download_queue
        payload = {
            "progress": {str(k): v for k, v in snapshot.items()},
            "running": q.is_running if q else False,
            "stats": q.stats if q else {"ok": 0, "failed": 0, "total": 0, "done": 0},
        }
        dead: set[WebSocket] = set()
        for ws in _ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        _ws_clients -= dead


# ---------- helpers ----------

def _stats() -> dict:
    conn = _get_conn()
    s = db_module.stats(conn)
    s["bytes"] = db_module.library_size_bytes(conn)
    return s


# ---------- static / UI ----------

UI_HTML = (Path(__file__).parent / "templates" / "index.html").resolve()


@app.get("/")
@app.get("/ui")
@app.get("/ui/{path:path}")
async def serve_ui():
    if not UI_HTML.exists():
        return HTMLResponse("<h1>UI not found</h1>", status_code=500)
    return HTMLResponse(UI_HTML.read_text(encoding="utf-8"))


# ---------- API ----------

@app.get("/api/stats")
async def api_stats():
    return _stats()


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# --- sources ---

def _source_row(r) -> dict:
    return dict(r)


@app.get("/api/sources")
async def list_sources():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT s.*, "
        "  (SELECT COUNT(*) FROM tracks t WHERE t.source_id=s.id) AS tracks, "
        "  (SELECT COUNT(*) FROM tracks t WHERE t.source_id=s.id AND t.status='done') AS done "
        "FROM sources s ORDER BY s.id"
    ).fetchall()
    return [_source_row(r) for r in rows]


@app.post("/api/sources")
async def add_source(body: AddSourceBody):
    conn = _get_conn()
    kind = sources.detect_url_kind(body.url)
    if kind == "unknown":
        raise HTTPException(400, f"Cannot detect source type for: {body.url}")
    try:
        if kind == "channel":
            entries = list(sources.channel_latest(body.url, limit=body.limit))
        elif kind == "playlist":
            entries = list(sources.playlist_entries(body.url, limit=body.limit))
        elif kind == "single":
            info = sources.fetch_info(body.url)
            entries = [info]
        elif kind == "search":
            entries = list(sources.search(body.name or body.url, limit=body.limit))
        else:
            entries = []
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch: {e}")

    if not entries:
        raise HTTPException(400, "No entries found at that URL")

    display = body.name or (entries[0].get("channel") or entries[0].get("uploader") or str(Path(body.url).name))
    source_id = db_module.upsert_source(conn, kind, display, body.url)
    db_module.touch_source(conn, source_id)
    conn.commit()

    added = 0
    for e in entries:
        norm = sources.normalize_entry(e, source_id=source_id)
        if not norm["video_id"]:
            continue
        db_module.upsert_track(conn, norm)
        added += 1
    conn.commit()

    return {"source_id": source_id, "name": display, "kind": kind, "tracks_added": added}


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM tracks WHERE source_id=?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.commit()
    return {"ok": True}


@app.post("/api/sources/{source_id}/sync")
async def sync_source(source_id: int, limit: int = 50):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Source not found")
    kind, url = row["kind"], row["url"]
    try:
        if kind == "channel":
            entries = list(sources.channel_latest(url, limit=limit))
        elif kind == "playlist":
            entries = list(sources.playlist_entries(url, limit=limit))
        else:
            entries = []
    except Exception as e:
        raise HTTPException(400, f"Sync failed: {e}")

    db_module.touch_source(conn, source_id)
    added = 0
    for e in entries:
        norm = sources.normalize_entry(e, source_id=source_id)
        if not norm["video_id"]:
            continue
        try:
            db_module.upsert_track(conn, norm)
            added += 1
        except Exception:
            pass
    conn.commit()
    return {"source_id": source_id, "new_tracks": added}


# --- tracks ---

@app.get("/api/tracks")
async def list_tracks(
    status: str | None = None,
    source_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
):
    conn = _get_conn()
    if search:
        q = ("SELECT * FROM tracks WHERE (title LIKE ? OR uploader LIKE ? OR channel LIKE ?) "
             "ORDER BY created_at DESC LIMIT ? OFFSET ?")
        rows = conn.execute(q, [f"%{search}%"] * 3 + [limit, offset]).fetchall()
    else:
        rows = db_module.list_tracks(conn, status=status, source_id=source_id, limit=limit, offset=offset)
    return [dict(r) for r in rows]


@app.get("/api/tracks/{track_id}")
async def get_track(track_id: int):
    conn = _get_conn()
    r = conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
    if not r:
        raise HTTPException(404)
    return dict(r)


@app.post("/api/tracks/queue")
async def queue_tracks(body: QueueBody):
    conn = _get_conn()
    if body.reset:
        conn.execute("UPDATE tracks SET status='new' WHERE status IN ('queued','downloading')")
        conn.commit()
        return {"reset": True}
    ids: list[int] = []
    if body.track_ids:
        ids = body.track_ids
    elif body.all_new:
        ids = [r["id"] for r in db_module.list_tracks(conn, status="new", limit=100000)]
    elif body.source_id is not None:
        ids = [r["id"] for r in db_module.list_tracks(conn, status="new", source_id=body.source_id, limit=100000)]
    if not ids:
        return {"queued": 0}

    q = DownloadQueue({}, conn)
    n = q.enqueue(ids)
    conn.commit()
    return {"queued": n}


# --- download ---

@app.get("/api/download/status")
async def download_status():
    q = _download_queue
    return {
        "running": q.is_running if q else False,
        "stats": q.stats if q else {"ok": 0, "failed": 0, "total": 0, "done": 0},
        "progress": {str(k): v for k, v in _progress_reporter.snapshot().items()},
    }


@app.post("/api/sources/{source_id}/download")
async def source_queue_download(source_id: int):
    """Queue all new tracks from a source + start downloading immediately."""
    conn = _get_conn()
    ids = [r["id"] for r in db_module.list_tracks(conn, status="new", source_id=source_id, limit=100000)]
    if not ids:
        raise HTTPException(400, "No new tracks in this source")
    q = DownloadQueue({}, conn)
    n = q.enqueue(ids)
    conn.commit()
    # auto-start
    global _download_queue, _download_thread
    if _download_queue and _download_queue.is_running:
        return {"status": "already_running", "queued": n}
    _download_queue = DownloadQueue(cfg.load_config(), db_module.init_db(), reporter=_progress_reporter)
    _download_thread = threading.Thread(target=_run_download, daemon=True)
    _download_thread.start()
    return {"status": "started", "queued": n}


@app.post("/api/download/start")
async def start_download():
    global _download_queue, _download_thread
    if _download_queue and _download_queue.is_running:
        return {"status": "already_running"}
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM tracks WHERE status='queued'").fetchone()["c"]
    if not total:
        raise HTTPException(400, "No tracks queued. Queue some tracks first.")
    conn.close()
    _download_queue = DownloadQueue(cfg.load_config(), db_module.init_db(), reporter=_progress_reporter)
    _download_thread = threading.Thread(target=_run_download, daemon=True)
    _download_thread.start()
    return {"status": "started", "queued": total}


def _run_download():
    global _download_queue
    try:
        _download_queue.run()
    except Exception as e:
        log.error("Download queue failed: %s", e)


@app.post("/api/download/stop")
async def stop_download():
    if _download_queue:
        _download_queue.stop()
        return {"status": "stopped"}
    return {"status": "not_running"}


# --- websocket ---

@app.websocket("/ws/progress")
async def ws_progress(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


# --- finalize ---

@app.get("/api/tracks/{track_id}/open")
async def open_track_file(track_id: int):
    """Open the downloaded file in the OS file manager."""
    import subprocess
    conn = _get_conn()
    r = conn.execute("SELECT file_path FROM tracks WHERE id=?", (track_id,)).fetchone()
    if not r or not r["file_path"]:
        raise HTTPException(404, "Track not found or no file")
    p = Path(r["file_path"])
    if not p.exists():
        raise HTTPException(404, f"File not found: {p}")
    subprocess.Popen(["xdg-open", str(p.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": str(p.parent)}


@app.get("/api/tracks/recent")
async def recent_tracks(limit: int = 6):
    """Return recently completed tracks."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tracks WHERE status='done' AND file_path IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/finalize")
async def finalize_tracks(body: FinalizeBody):
    conn = _get_conn()
    c = cfg.load_config()
    out = body.output or str(Path(c["downloads"]["output_dir"]) / "library")
    result = do_finalize(conn, output_dir=out, move=(body.mode != "copy"), organize_by=body.organize_by)
    conn.commit()
    return result


# --- config ---

@app.get("/api/config")
async def get_config():
    return cfg.load_config()


@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    c = cfg.load_config()
    parts = body.key.split(".")
    target = c
    for p in parts[:-1]:
        target = target.setdefault(p, {})
    target[parts[-1]] = body.value
    cfg.save_config(c)
    return {"ok": True}
