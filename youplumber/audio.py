"""Audio post-processing: normalization, tagging, organization."""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3, TALB, TDRC, TIT2, TPE1, TPE2, TCON, TRCK, TKEY, TBPM, TXXX, ID3NoHeaderError,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

log = logging.getLogger("youplumber.audio")

CAMELOT_TO_MUSICAL = {
    # Camelot -> musical key (used to suggest the reverse)
    "1A": "Ab minor", "1B": "B major",
    "2A": "Eb minor", "2B": "F# major",
    "3A": "Bb minor", "3B": "Db major",
    "4A": "F minor",  "4B": "Ab major",
    "5A": "C minor",  "5B": "Eb major",
    "6A": "G minor",  "6B": "Bb major",
    "7A": "D minor",  "7B": "F major",
    "8A": "A minor",  "8B": "C major",
    "9A": "E minor",  "9B": "G major",
    "10A": "B minor", "10B": "D major",
    "11A": "F# minor","11B": "A major",
    "12A": "Db minor","12B": "E major",
}

# Open Key notation: letters to musical key
OPEN_KEY_TO_MUSICAL = {
    "1m": "Ab minor", "1d": "B major",
    "2m": "Eb minor", "2d": "F# major",
    "3m": "Bb minor", "3d": "Db major",
    "4m": "F minor",  "4d": "Ab major",
    "5m": "C minor",  "5d": "Eb major",
    "6m": "G minor",  "6d": "Bb major",
    "7m": "D minor",  "7d": "F major",
    "8m": "A minor",  "8d": "C major",
    "9m": "E minor",  "9d": "G major",
    "10m": "B minor", "10d": "D major",
    "11m": "F# minor","11d": "A major",
    "12m": "Db minor","12d": "E major",
}


def parse_key(value: str) -> tuple[str, str] | None:
    """Return (musical_key, camelot) from various input formats."""
    if not value:
        return None
    v = value.strip()
    # Camelot e.g. "8A", "12B"
    m = re.match(r"^(\d{1,2})([AB])$", v, re.I)
    if m:
        camelot = f"{int(m.group(1))}{m.group(2).upper()}"
        musical = CAMELOT_TO_MUSICAL.get(camelot, v)
        return musical, camelot
    # Open Key e.g. "8m", "11d"
    m = re.match(r"^(\d{1,2})([md])$", v, re.I)
    if m:
        ok = f"{int(m.group(1))}{m.group(2).lower()}"
        musical = OPEN_KEY_TO_MUSICAL.get(ok, v)
        # convert OpenKey to Camelot: minor stays A, major becomes B
        camelot_num = int(m.group(1))
        camelot_letter = "A" if m.group(2).lower() == "m" else "B"
        camelot = f"{camelot_num}{camelot_letter}"
        return musical, camelot
    return v, _musical_to_camelot(v)


# Major/minor to Camelot
_NOTES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NOTES_FLAT  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_MUSICAL_TO_CAMELOT_MAJOR = {
    "B":  "1B",  "F#": "2B", "Db": "3B", "Ab": "4B", "Eb": "5B", "Bb": "6B",
    "F":  "7B",  "C":  "8B", "G":  "9B", "D": "10B", "A": "11B", "E": "12B",
}
_MUSICAL_TO_CAMELOT_MINOR = {
    "G#": "1A",  "Eb": "2A", "Bb": "3A", "F":  "4A", "C":  "5A", "G":  "6A",
    "D":  "7A",  "A":  "8A", "E":  "9A", "B": "10A", "F#": "11A", "C#": "12A",
}


def _musical_to_camelot(musical: str) -> str | None:
    """Best-effort musical key (e.g. 'A minor', 'C# minor') -> Camelot."""
    parts = musical.strip().split()
    if len(parts) < 2:
        return None
    note = parts[0]
    mode = parts[1].lower()
    table = _MUSICAL_TO_CAMELOT_MINOR if mode.startswith("min") else _MUSICAL_TO_CAMELOT_MAJOR
    return table.get(note) or table.get(note.replace("Db", "C#").replace("Eb", "D#"))


def loudness_lufs(path: Path) -> float | None:
    """Measure integrated loudness (ITU-R BS.1770) via ffmpeg's ebur128."""
    text = ""
    try:
        out = subprocess.check_output(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                "-af", "ebur128=peak=true", "-f", "null", "-",
            ],
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        text = out.decode("utf-8", errors="ignore")
    except subprocess.CalledProcessError as e:
        text = e.output.decode("utf-8", errors="ignore") if e.output else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    try:
        m = re.search(r"I:\s*(-?\d+\.?\d*)\s*LUFS", text)
        if m:
            return float(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def probe_bitrate(path: Path) -> tuple[int | None, int | None]:
    """Return (bitrate_bps, sample_rate_hz) from ffprobe."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=bit_rate,sample_rate",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            timeout=30,
        ).decode().strip().splitlines()
        if len(out) >= 2:
            br = int(out[0]) if out[0].isdigit() else None
            sr = int(out[1]) if out[1].isdigit() else None
            return br, sr
    except Exception:  # noqa: BLE001
        pass
    return None, None


def tag_file(path: Path, meta: dict[str, Any]) -> None:
    """Embed DJ-relevant tags. Mutates the file in place."""
    try:
        if path.suffix.lower() == ".mp3":
            _tag_mp3(path, meta)
        elif path.suffix.lower() == ".flac":
            _tag_flac(path, meta)
        elif path.suffix.lower() in (".m4a", ".mp4"):
            _tag_mp4(path, meta)
        elif path.suffix.lower() == ".ogg":
            _tag_ogg(path, meta)
    except Exception as e:  # noqa: BLE001
        log.warning("tag_file failed for %s: %s", path, e)


def _tag_mp3(path: Path, meta: dict[str, Any]) -> None:
    try:
        audio = MP3(path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(path)
        audio.add_tags()
    id3 = audio.tags
    if meta.get("title"):    id3.add(TIT2(encoding=3, text=[meta["title"]]))
    if meta.get("artist"):   id3.add(TPE1(encoding=3, text=[meta["artist"]]))
    if meta.get("album"):    id3.add(TALB(encoding=3, text=[meta["album"]]))
    if meta.get("genre"):    id3.add(TCON(encoding=3, text=[meta["genre"]]))
    if meta.get("year"):     id3.add(TDRC(encoding=3, text=[str(meta["year"])]))
    if meta.get("track_no"): id3.add(TRCK(encoding=3, text=[str(meta["track_no"])]))
    if meta.get("bpm"):      id3.add(TBPM(encoding=3, text=[str(int(round(meta["bpm"])))]))
    if meta.get("key"):
        id3.add(TKEY(encoding=3, text=[meta["key"]]))
        id3.add(TXXX(encoding=3, desc="CAMELOT", text=[meta.get("camelot", "")]))
    if meta.get("label"):
        id3.add(TXXX(encoding=3, desc="LABEL", text=[meta["label"]]))
    audio.save()


def _tag_flac(path: Path, meta: dict[str, Any]) -> None:
    f = FLAC(path)
    if meta.get("title"):    f["title"] = meta["title"]
    if meta.get("artist"):   f["artist"] = meta["artist"]
    if meta.get("album"):    f["album"] = meta["album"]
    if meta.get("genre"):    f["genre"] = meta["genre"]
    if meta.get("year"):     f["date"] = str(meta["year"])
    if meta.get("track_no"): f["tracknumber"] = str(meta["track_no"])
    if meta.get("bpm"):      f["bpm"] = str(int(round(meta["bpm"])))
    if meta.get("key"):      f["key"] = meta["key"]
    if meta.get("camelot"):  f["comment"] = f"CAMELOT={meta['camelot']}"
    f.save()


def _tag_mp4(path: Path, meta: dict[str, Any]) -> None:
    f = MP4(path)
    if meta.get("title"):  f["\xa9nam"] = meta["title"]
    if meta.get("artist"): f["\xa9ART"] = meta["artist"]
    if meta.get("album"):  f["\xa9alb"] = meta["album"]
    if meta.get("genre"):  f["\xa9gen"] = meta["genre"]
    if meta.get("year"):   f["\xa9day"] = str(meta["year"])
    if meta.get("bpm"):    f["----:com.apple.iTunes:BPM"] = \
        [str(int(round(meta["bpm"]))).encode()]
    if meta.get("key"):
        f["----:com.apple.iTunes:KEY"] = [meta["key"].encode()]
        if meta.get("camelot"):
            f["----:com.apple.iTunes:CAMELOT"] = [meta["camelot"].encode()]
    f.save()


def _tag_ogg(path: Path, meta: dict[str, Any]) -> None:
    f = OggVorbis(path)
    if meta.get("title"):    f["title"] = [meta["title"]]
    if meta.get("artist"):   f["artist"] = [meta["artist"]]
    if meta.get("album"):    f["album"] = [meta["album"]]
    if meta.get("genre"):    f["genre"] = [meta["genre"]]
    if meta.get("year"):     f["date"] = [str(meta["year"])]
    if meta.get("bpm"):      f["bpm"] = [str(int(round(meta["bpm"])))]
    if meta.get("key"):      f["key"] = [meta["key"]]
    f.save()


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:200] or "track"
