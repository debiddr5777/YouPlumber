# utube — fast mass YouTube audio acquisition for DJs

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
utube grab "https://www.youtube.com/@somechannel" --limit 20 --codec mp3

# step by step
utube add  "https://www.youtube.com/playlist?list=PLxxxx" --limit 50
utube sources
utube list --status new
utube queue --all
utube download

# search
utube grab "ytsearch30:afro house 2024" --limit 30
```

## Configuration

`~/.config/utube/config.toml` (auto-created on first run):

```toml
[downloads]
output_dir       = "~/.local/share/utube/downloads"
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
utube config --concurrent-jobs 8 --codec flac --bitrate 320
```

## Commands

| Command         | Purpose                                          |
|-----------------|--------------------------------------------------|
| `utube add`     | Add a channel / playlist / video as a source     |
| `utube grab`    | One-shot add + ingest + download                 |
| `utube list`    | Browse library (`--json` for machine use)        |
| `utube sources` | List configured sources with counts              |
| `utube queue`   | Mark tracks as queued for download               |
| `utube download`| Process the download queue (parallel)            |
| `utube doctor`  | Verify environment + DB                          |
| `utube config`  | Update settings                                  |

## Data layout

* `~/.local/share/utube/library.db` — tracks, sources, jobs
* `~/.local/share/utube/downloads/<source_id>/<id>.<ext>` — staged files
* `~/.config/utube/config.toml` — user settings

## Notes

* Downloads are rate-limited only if you set `rate_limit`.
* Failed tracks auto-retry; the queue picks them up on next `utube download`.
* Run `utube download` repeatedly (cron/systemd) for unattended syncing.
