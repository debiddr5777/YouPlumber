"""Finalize stage: rename files to human-readable names + reorganize."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from . import db
from .audio import safe_filename


def _read_artist_title(path: Path) -> tuple[str | None, str | None]:
    """Try to recover (artist, title) from embedded tags."""
    try:
        f = MutagenFile(str(path), easy=True)
    except Exception:  # noqa: BLE001
        return None, None
    if not f or not f.tags:
        return None, None
    artist = (f.tags.get("artist") or [None])[0]
    title = (f.tags.get("title") or [None])[0]
    return artist, title


def _target_name(track: dict[str, Any], src: Path) -> str:
    """Decide the final filename for a track."""
    artist = track.get("uploader") or track.get("channel")
    title = track.get("title")
    if not artist or not title:
        a, t = _read_artist_title(src)
        artist = artist or a or "Unknown"
        title = title or t or track.get("video_id", "track")
    return safe_filename(f"{artist} - {title}{src.suffix}")


def finalize(
    conn,
    *,
    output_dir: str | Path,
    move: bool = True,
    organize_by: str | None = None,
) -> dict[str, int]:
    """Rename + optionally move every 'done' track into output_dir.

    organize_by: None | "genre" | "uploader" | "year" | "month"
    """
    out_root = Path(output_dir).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)

    rows = list(conn.execute(
        "SELECT * FROM tracks WHERE status='done' AND file_path IS NOT NULL"
    ))
    out = {"renamed": 0, "moved": 0, "skipped": 0}

    for r in rows:
        src = Path(r["file_path"])
        if not src.exists():
            out["skipped"] += 1
            continue
        new_name = _target_name(dict(r), src)
        sub = ""
        if organize_by == "genre":
            sub = safe_filename(r["uploader"] or "Unsorted")
        elif organize_by == "uploader":
            sub = safe_filename(r["uploader"] or "Unsorted")
        elif organize_by == "year":
            sub = (r["upload_date"] or "0000")[:4]
        elif organize_by == "month":
            sub = (r["upload_date"] or "000000")[:6]
        dst = out_root / sub / new_name if sub else out_root / new_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Avoid collisions
        n = 1
        stem = dst.stem
        while dst.exists() and dst != src:
            dst = dst.with_name(f"{stem} ({n}){dst.suffix}")
            n += 1

        try:
            if move:
                shutil.move(str(src), str(dst))
                out["moved"] += 1
            else:
                shutil.copy2(str(src), str(dst))
            out["renamed"] += 1
            db.set_file(conn, r["id"], str(dst), dst.stat().st_size)
        except Exception:  # noqa: BLE001
            out["skipped"] += 1

    return out
