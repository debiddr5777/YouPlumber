<p align="center">
  <br>
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-0.1.0-purple.svg?style=for-the-badge" alt="Version">
  <br><br>
</p>

<h1 align="center">🎧 YouPlumber</h1>

<p align="center">
  <strong>Mass YouTube audio acquisition for DJs — fast, parallel, beautiful.</strong>
  <br>
  Paste a channel/playlist link → fetch all tracks → tick what you want → download with one click.
  <br><br>
  <img src="https://img.shields.io/badge/CLI-ready-blueviolet?style=flat-square" alt="CLI">
  <img src="https://img.shields.io/badge/Web_UI-FastAPI-009688?style=flat-square&logo=fastapi" alt="Web UI">
  <img src="https://img.shields.io/badge/yt--dlp-powered-ff0000?style=flat-square&logo=youtube" alt="yt-dlp">
  <img src="https://img.shields.io/badge/SQLite-database-003B57?style=flat-square&logo=sqlite" alt="SQLite">
</p>

---

## ✨ Features

<table>
<tr>
<td width="50%">

**🚀 Parallel Downloads**  
Multi-threaded worker pool rips through large playlists.  
Configurable concurrency, auto-retry on failure.

**🎛️ DJ-Ready Metadata**  
BPM, Camelot key, artist, title — embedded directly  
into every file via FFmpeg postprocessing.

**🌐 Web UI + CLI**  
Use the terminal for scripting, or the web dashboard  
for quick interactive sessions.

</td>
<td width="50%">

**📦 Flat Output**  
No nested channel folders — all files land flat in  
your chosen directory. Name template: `Title [id].ext`.

**🔁 Auto-Resume**  
Interrupted downloads resume. Stuck `downloading`  
state is cleaned on server startup.

**🕸️ WebSocket Live Progress**  
Real-time speed, ETA, and progress bars pushed  
to the browser every 300ms.

</td>
</tr>
</table>

<div align="center">
  <br>
  <img src="https://img.shields.io/badge/➕_add_source-6366f1?style=for-the-badge" alt="add">
  <img src="https://img.shields.io/badge/✅_select_tracks-22c55e?style=for-the-badge" alt="select">
  <img src="https://img.shields.io/badge/⬇️_download-3b82f6?style=for-the-badge" alt="download">
  <img src="https://img.shields.io/badge/🎵_play-8b5cf6?style=for-the-badge" alt="play">
  <br><br>
</div>

---

## 🖼️ Web UI

```
┌──────────────────────────────────────────────────────────────┐
│  🎧 YouPlumber                                   ● idle     │
│  0 queued  0 active  5 done  142 MB               ⚙️        │
├──────────────────────────────────────────────────────────────┤
│  [ Paste YouTube link (channel, playlist, or search) ] [🔍] │
├──────────────────────────────┬───────────────────────────────┤
│  📋 Queue                    │  🕐 Recent                    │
│                              │                               │
│  ┌──────────────────────────┐│  ┌───────────────────────────┐│
│  │ Artist - Track Name  45% ││  │ Track Name            ✓  ││
│  │ ██████████████░░░░░░░    ││  │ Uploader · 4:30         ││
│  └──────────────────────────┘│  │ 📁 Title [abc123].mp3    ││
│                              │  └───────────────────────────┘│
│  Track A                  ✓  │  ┌───────────────────────────┐│
│  Track B             45% ◌   │  │ Track Name            ✓  ││
│  Track C                  ⏳ │  │ ...                       ││
│                              │                               │
│  [▶ Start]  [⏹ Stop]  [🗑]  │                               │
└──────────────────────────────┴───────────────────────────────┘
```

The web UI runs on `http://localhost:8652` — paste a URL, tick tracks, download.

---

## 📦 Installation

```bash
# 1. Install ffmpeg (required for audio conversion)
sudo apt install ffmpeg          # Debian/Ubuntu
brew install ffmpeg              # macOS

# 2. Install YouPlumber
git clone https://github.com/debiddr5777/YouPlumber.git
cd YouPlumber
pip install -e .

# 3. Launch the web UI
yp serve --port 8652
```

---

## 🚀 Quick Start

### Web UI (recommended)

```bash
yp serve
# → http://127.0.0.1:8652
```

1. Paste a YouTube channel, playlist, or search URL
2. Click **Fetch**
3. Tick the tracks you want
4. Click **Add to Queue**
5. Click **Start** — watch real-time progress

### CLI

```bash
# One-shot: add + queue + download
yp grab "https://www.youtube.com/@somechannel" --limit 20 --codec mp3

# Step by step
yp add   "https://www.youtube.com/playlist?list=PLxxxx" --limit 50
yp sources
yp list  --status new
yp queue --all
yp download

# Search
yp grab "ytsearch30:afro house 2026" --limit 30
```

---

## 📟 CLI Reference

| Command | Purpose |
|---------|---------|
| `yp add <url>` | Add a channel/playlist/video as a source |
| `yp grab <url>` | One-shot add + queue + download |
| `yp list` | Browse library tracks |
| `yp sources` | List configured sources |
| `yp queue` | Mark tracks for download |
| `yp download` | Process the download queue |
| `yp status` | Show library statistics |
| `yp doctor` | Verify environment + DB |
| `yp config` | Update settings |
| `yp serve` | Launch the web UI |

---

## ⚙️ Configuration

Auto-created at `~/.config/youplumber/config.toml`:

```toml
[downloads]
output_dir          = "~/music"
concurrent_jobs     = 4
retries             = 5
rate_limit          = ""            # e.g. "10M"
folder_template     = ""            # flat output
file_template       = "%(title)s [%(id)s].%(ext)s"

[audio]
codec               = "mp3"         # mp3 | wav | flac | aac | opus
mp3_bitrate         = "320"

[metadata]
embed_thumbnail     = false
embed_metadata      = true
```

Override at runtime:

```bash
yp config --concurrent-jobs 8 --codec flac --bitrate 320
```

---

## 🗄️ Data Layout

```
~/.config/youplumber/config.toml          # Settings
~/.local/share/youplumber/library.db      # SQLite database (tracks, sources, jobs)
```

Downloaded files land in your configured `output_dir` (default `~/music/`).

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Audio extraction** | yt-dlp + FFmpeg |
| **Metadata** | mutagen (ID3/FLAC/Vorbis) |
| **Database** | SQLite (WAL mode) |
| **CLI** | Click + Rich |
| **Web server** | FastAPI + Uvicorn |
| **WebSockets** | websockets |
| **Templates** | Jinja2 + Tailwind CSS |
| **Parallelism** | ThreadPoolExecutor |

---

<p align="center">
  <sub>Built for DJs who need to move fast.</sub>
  <br>
  <sub>Copyright © 2026 · MIT License</sub>
</p>
