"""Download queue: parallel workers, audio extraction, metadata embedding."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from . import config, db

log = logging.getLogger("youplumber.downloader")

CODEC_MAP = {
    "mp3":  ("mp3",      "mp3"),
    "wav":  ("wav",      "wav"),
    "flac": ("flac",     "flac"),
    "aac":  ("m4a",      "m4a"),
    "opus": ("opus",     "opus"),
    "best": ("best",     None),
}


def _ydl_opts_for_track(cfg: dict, track: dict) -> tuple[dict, Path, str]:
    dl = cfg["downloads"]
    audio = cfg["audio"]
    out_dir = Path(dl["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    codec = audio.get("codec", "mp3")
    target_ext = CODEC_MAP.get(codec, ("mp3", "mp3"))[1] or "%(ext)s"

    if track.get("source_kind") == "playlist":
        import re
        sub = re.sub(r'[\\/:*?"<>|]+', '_', track.get("source_name", "Playlist")).strip()
        out_dir = out_dir / sub
        out_dir.mkdir(parents=True, exist_ok=True)

    folder_tpl = dl.get("folder_template", "")
    file_tpl = dl.get("file_template", "%(title)s.%(ext)s")
    if folder_tpl and folder_tpl.strip():
        outtmpl = str(out_dir / folder_tpl.strip() / file_tpl)
    else:
        outtmpl = str(out_dir / file_tpl)

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "trim_file_name": 200,
        "format": dl.get("format", "bestaudio/best"),
        "socket_timeout": 30,
        "retries": int(dl.get("retries", 5)),
        "fragment_retries": int(dl.get("retries", 5)),
        "skip_unavailable_fragments": True,
        "concurrent_fragment_downloads": int(dl.get("concurrent_fragments", 8)),
        "ignoreerrors": False,
        "noplaylist": True,
        "extract_flat": False,
        "writethumbnail": False,
        "writemetadata": False,
        "postprocessors": [],
        "progress_hooks": [],
    }

    if dl.get("rate_limit"):
        opts["ratelimit"] = dl["rate_limit"]

    if codec != "best":
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": CODEC_MAP[codec][0]}
        if codec == "mp3":
            pp["preferredquality"] = audio.get("mp3_bitrate", "320")
        opts["postprocessors"].append(pp)
        if audio.get("embed_metadata", True):
            opts["postprocessors"].append({"key": "FFmpegMetadata"})

    return opts, out_dir, target_ext


def _cleanup_temp_files(out_dir: Path, video_id: str) -> None:
    """Remove leftover temp files from a failed download."""
    for p in out_dir.rglob(f"*{video_id}*"):
        if p.suffix.lower() in {".part", ".ytdl", ".temp", ".tmp", ".infojson"}:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


class ProgressReporter:
    """Thread-safe progress sink for the UI."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[int, dict] = {}

    def update(self, track_id: int, **fields: Any) -> None:
        with self._lock:
            self._items.setdefault(track_id, {}).update(fields)
            self._items[track_id]["track_id"] = track_id
            self._items[track_id]["updated_at"] = time.time()

    def snapshot(self) -> dict[int, dict]:
        with self._lock:
            return dict(self._items)

    def clear_finished(self, track_ids: set[int]) -> None:
        with self._lock:
            for tid in track_ids:
                self._items.pop(tid, None)

    def get(self, track_id: int) -> dict | None:
        with self._lock:
            return self._items.get(track_id)


def _make_progress_hook(track_id: int, title: str, reporter: ProgressReporter) -> Callable:
    def hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            done = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta")
            pct = min(done / total * 100, 99.9)
            reporter.update(track_id, status="downloading", title=title,
                            pct=pct, speed=speed, eta=eta, bytes_done=done, bytes_total=total)
        elif status == "finished":
            reporter.update(track_id, status="postprocessing", pct=100.0, title=title)
        elif status == "error":
            reporter.update(track_id, status="error", title=title)
    return hook


def download_one(
    cfg: dict,
    track: dict,
    reporter: ProgressReporter | None = None,
) -> tuple[bool, str | None, Path | None]:
    opts, out_dir, target_ext = _ydl_opts_for_track(cfg, track)
    title = track.get("title") or "Untitled"
    if reporter:
        hook = _make_progress_hook(track["id"], title, reporter)
        opts["progress_hooks"] = [hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([track["url"]])
    except Exception as e:
        _cleanup_temp_files(out_dir, track["video_id"])
        err = str(e)
        if "Video unavailable" in err:
            return False, "Video unavailable (removed or private)", None
        if "not available" in err.lower():
            return False, "Video not available in your region", None
        if "copyright" in err.lower():
            return False, "Blocked by copyright claim", None
        return False, err[:150], None

    final = _find_output(out_dir, track["video_id"], target_ext)
    if not final or not final.exists():
        _cleanup_temp_files(out_dir, track["video_id"])
        return False, "Output file not found", None

    if final.stat().st_size < 1024:
        final.unlink(missing_ok=True)
        return False, "Output file too small (likely silent/empty)", None

    return True, None, final


def _find_output(out_dir: Path, video_id: str, target_ext: str) -> Path | None:
    """Find the final audio file matching video_id under out_dir."""
    # First: try the predictable path pattern (fast path)
    for candidate in out_dir.rglob(f"*{video_id}*"):
        if candidate.suffix.lstrip(".").lower() in {"part", "ytdl", "tmp", "infojson"}:
            continue
        if target_ext != "%(ext)s" and candidate.suffix.lstrip(".").lower() != target_ext.lower():
            continue
        if candidate.stat().st_size < 1024:
            continue
        return candidate

    # Fallback: glob by video_id
    matches: list[Path] = []
    for pattern in (f"**/*{video_id}*", f"**/{video_id}.*"):
        matches.extend(out_dir.glob(pattern))
    matches = sorted(set(matches), key=lambda p: p.stat().st_mtime, reverse=True)

    for c in matches:
        ext = c.suffix.lstrip(".").lower()
        if ext in {"part", "ytdl", "tmp", "infojson"}:
            continue
        if target_ext != "%(ext)s" and ext != target_ext.lower():
            continue
        if c.stat().st_size < 1024:
            continue
        return c

    return matches[0] if matches else None


class DownloadQueue:
    def __init__(self, cfg: dict | None, conn, reporter: ProgressReporter | None = None) -> None:
        self.cfg = cfg or config.load_config()
        self.conn = conn
        self.reporter = reporter or ProgressReporter()
        self._stop = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._running = threading.Event()
        self._stats = {"ok": 0, "failed": 0, "total": 0, "done": 0}

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def stop(self) -> None:
        self._stop.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def enqueue(self, track_ids: list[int]) -> int:
        now = int(time.time())
        for tid in track_ids:
            self.conn.execute(
                "UPDATE tracks SET status='queued', updated_at=? "
                "WHERE id=? AND status IN ('new','failed','skipped')",
                (now, tid),
            )
        self.conn.commit()
        cnt = self.conn.execute(
            "SELECT COUNT(*) c FROM tracks WHERE status='queued'"
        ).fetchone()["c"]
        log.info("enqueued %d tracks, %d total queued", len(track_ids), cnt)
        return cnt

    def run(self) -> dict:
        workers = int(self.cfg["downloads"].get("concurrent_jobs", 4))
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._running.set()
        self._stats = {"ok": 0, "failed": 0, "total": 0, "done": 0}
        self._stop.clear()
        active: dict[int, Any] = {}

        def launch() -> None:
            if self._stop.is_set():
                return
            tracks = list(self.conn.execute(
                "SELECT t.*, s.name as source_name, s.kind as source_kind "
                "FROM tracks t LEFT JOIN sources s ON t.source_id = s.id "
                "WHERE t.status='queued' ORDER BY t.id LIMIT ?",
                (workers,),
            ))
            for t in tracks:
                if t["id"] in active:
                    continue
                db.set_status(self.conn, t["id"], "downloading")
                self.conn.commit()
                f = self._executor.submit(self._worker, dict(t))
                active[t["id"]] = f
                self._stats["total"] += 1
                log.debug("spawned worker for track %d", t["id"])

        launch()

        try:
            while active and not self._stop.is_set():
                done = [tid for tid, f in active.items() if f.done()]
                for tid in done:
                    f = active.pop(tid)
                    try:
                        ok, err, path = f.result()
                    except Exception as e:
                        ok, err, path = False, str(e), None

                    if ok and path:
                        size = path.stat().st_size if path.exists() else 0
                        db.set_file(self.conn, tid, str(path), size)
                        self._stats["ok"] += 1
                        log.info("done track %d → %s", tid, path)
                    else:
                        retries = self.conn.execute(
                            "SELECT retries FROM tracks WHERE id=?", (tid,)
                        ).fetchone()
                        retry_count = (retries["retries"] if retries else 0) + 1
                        if retry_count <= 3 and err and "HTTP Error 429" not in err:
                            # Auto-retry up to 3 times
                            self.conn.execute(
                                "UPDATE tracks SET status='queued', retries=?, "
                                "last_error=?, updated_at=? WHERE id=?",
                                (retry_count, err, int(time.time()), tid),
                            )
                            self._stats["failed"] += 1
                            log.warning("retry track %d (%d/3): %s", tid, retry_count, err)
                        else:
                            self.conn.execute(
                                "UPDATE tracks SET status='failed', last_error=?, "
                                "retries=?, updated_at=? WHERE id=?",
                                (err, retry_count, int(time.time()), tid),
                            )
                            self._stats["failed"] += 1
                            log.warning("failed track %d: %s", tid, err)
                    self.conn.commit()
                    self._stats["done"] += 1
                    self.reporter.clear_finished({tid})
                    launch()

                if not done:
                    time.sleep(0.1)
        finally:
            self._running.clear()
            if self._executor:
                self._executor.shutdown(wait=False)
            log.info("download queue finished: %s", self._stats)

        return dict(self._stats)

    def _worker(self, track: dict) -> tuple[bool, str | None, Path | None]:
        return download_one(self.cfg, track, self.reporter)
