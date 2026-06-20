# YouPlumber — fast mass YouTube audio acquisition for DJs

A focused CLI for grabbing audio from YouTube channels, playlists, and searches
in parallel. Built around `yt-dlp` + `ffmpeg`, tracks everything in SQLite,
embeds DJ-friendly metadata, and writes directly to a folder of your choice.

## Install

```bash
pip install -e .
```

Requires `ffmpeg` on PATH.

## Quick start

```bash
# one-shot: add source, ingest, download
youplumber grab "https://www.youtube.com/@somechannel" --limit 20 --codec mp3

# step by step
youplumber add  "https://www.youtube.com/playlist?list=PLxxxx" --limit 50
youplumber sources
youplumber list --status new
youplumber queue --all
youplumber download

# search
youplumber grab "ytsearch30:afro house 2024" --limit 30
```

## Configuration

`~/.config/youplumber/config.toml` (auto-created on first run):

```toml
[downloads]
output_dir       = "~/.local/share/youplumber/downloads"
concurrent_jobs  = 4
concurrent_fragments = 8
retries          = 5
rate_limit       = ""         # e.g. "10M" for 10 MB/s cap

[audio]
codec            = "mp3"      # mp3 | wav | flac | aac | opus | best
mp3_bitrate      = "320"
embed_thumbnail  = true
embed_metadata   = true
```

Override at runtime:

```bash
youplumber config --concurrent-jobs 8 --codec flac --bitrate 320
```

## Commands

| Command         | Purpose                                          |
|-----------------|--------------------------------------------------|
| `youplumber add`     | Add a channel / playlist / video as a source     |
| `youplumber grab`    | One-shot add + ingest + download                 |
| `youplumber list`    | Browse library (`--json` for machine use)        |
| `youplumber sources` | List configured sources with counts              |
| `youplumber queue`   | Mark tracks as queued for download               |
| `youplumber download`| Process the download queue (parallel)            |
| `youplumber doctor`  | Verify environment + DB                          |
| `youplumber config`  | Update settings                                  |

## Data layout

* `~/.local/share/youplumber/library.db` — tracks, sources, jobs
* `~/.local/share/youplumber/downloads/<source_id>/<id>.<ext>` — staged files
* `~/.config/youplumber/config.toml` — user settings

## Notes

* Downloads are rate-limited only if you set `rate_limit`.
* Failed tracks auto-retry; the queue picks them up on next `youplumber download`.
* Run `youplumber download` repeatedly (cron/systemd) for unattended syncing.
