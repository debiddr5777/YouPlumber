"""Source discovery: channels, playlists, search."""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Iterator

import yt_dlp
from yt_dlp.utils import DownloadError

YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": "in_playlist",
    "ignoreerrors": False,
}


def _ydl_opts(extra: dict | None = None) -> dict:
    opts = dict(YDL_BASE)
    if extra:
        opts.update(extra)
    return opts


def detect_url_kind(url: str) -> str:
    u = url.lower()
    if "youtube.com/@" in u or "youtube.com/c/" in u or "youtube.com/channel/" in u:
        return "channel"
    if "playlist?list=" in u:
        return "playlist"
    if "watch?v=" in u or "youtu.be/" in u:
        return "single"
    if u.startswith("ytsearch:") or u.startswith("ytsearch"):
        return "search"
    return "unknown"


def fetch_info(url: str, *, limit: int | None = None) -> dict[str, Any]:
    """Fetch metadata + entry list for a URL."""
    opts = _ydl_opts({"playlistend": limit} if limit else {})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError(f"Could not extract info from {url}")
    return info


def iter_entries(info: dict) -> Iterator[dict]:
    if "entries" in info and info["entries"]:
        for e in info["entries"]:
            if e:
                yield e
    else:
        yield info


def normalize_entry(e: dict, source_id: int | None = None) -> dict:
    """Map a yt-dlp entry to our track schema (partial - analysis added later)."""
    video_id = e.get("id") or e.get("url")
    url = e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
    if url and not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={video_id}"
    return {
        "source_id": source_id,
        "video_id": video_id,
        "url": url,
        "title": e.get("title") or "Untitled",
        "uploader": e.get("uploader") or e.get("channel") or e.get("creator"),
        "channel": e.get("channel") or e.get("uploader"),
        "duration": e.get("duration"),
        "upload_date": e.get("upload_date"),
        "description": (e.get("description") or "")[:1000],
        "thumbnail": (
            (e.get("thumbnails") or [{}])[-1].get("url")
            if e.get("thumbnails")
            else None
        ),
        "view_count": e.get("view_count"),
        "like_count": e.get("like_count"),
    }


def channel_latest(channel_url: str, limit: int = 50) -> Iterator[dict]:
    """Get latest uploads from a channel via /streams tab."""
    # Try the /streams tab first, fall back to /videos
    for tab in ("/streams", "/videos"):
        try:
            url = channel_url.rstrip("/") + tab
            info = fetch_info(url, limit=limit)
            return iter_entries(info)
        except (DownloadError, Exception):
            continue
    raise RuntimeError(f"Could not fetch channel {channel_url}")


def playlist_entries(playlist_url: str, limit: int | None = None) -> Iterator[dict]:
    info = fetch_info(playlist_url, limit=limit)
    return iter_entries(info)


def search(query: str, limit: int = 30) -> Iterator[dict]:
    url = f"ytsearch{limit}:{query}"
    info = fetch_info(url)
    return iter_entries(info)
