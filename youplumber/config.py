from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "youplumber"
CONFIG_DIR = Path.home() / ".config" / "youplumber"
DB_PATH = DATA_DIR / "library.db"
DOWNLOADS_DIR = DATA_DIR / "downloads"
STAGING_DIR = DATA_DIR / "staging"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """\
# youplumber configuration
[downloads]
output_dir = "{downloads}"
format = "bestaudio/best"
concurrent_jobs = 4
concurrent_fragments = 8
retries = 5
rate_limit = ""                # e.g. "10M" for 10 MB/s
# Layout: <output_dir>/<folder_template>/<file_template>
# Available placeholders: %(channel)s, %(uploader)s, %(title)s, %(id)s,
#                          %(playlist)s, %(upload_date>%Y-%m)s, %(ext)s
# %(channel,uploader)s means "use channel, fall back to uploader"
folder_template = ""
file_template   = "%(title)s [%(id)s].%(ext)s"

[audio]
codec = "mp3"             # mp3 | wav | flac | aac | opus | best
mp3_bitrate = "320"
mp3_quality = "0"         # 0 (best) - 9 (worst) for VBR
wav_bitdepth = "16"       # 16 | 24 | 32
flac_compression = "8"
normalize = false
target_loudness = "-14"   # LUFS

[metadata]
embed_thumbnail = false
embed_metadata = true
filename_template = "%(artist,uploader)s - %(title)s [%(id)s].%(ext)s"

[library]
folder_template = "{{genre}}/{{year}}/{{bpm_key}}/{{artist}} - {{title}}.{{ext}}"
""".replace("{downloads}", str(DOWNLOADS_DIR))


def ensure_dirs() -> None:
    for p in (DATA_DIR, CONFIG_DIR, DOWNLOADS_DIR, STAGING_DIR):
        p.mkdir(parents=True, exist_ok=True)


def write_default_config() -> None:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG)


def load_config() -> dict:
    import tomllib

    ensure_dirs()
    write_default_config()
    with CONFIG_PATH.open("rb") as f:
        user_cfg = tomllib.load(f)
    defaults = tomllib.loads(DEFAULT_CONFIG)
    return _deep_merge(defaults, user_cfg)


def _deep_merge(defaults: dict, overrides: dict) -> dict:
    """Recursively merge overrides into defaults."""
    out = dict(defaults)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def save_config(cfg: dict) -> None:
    import tomli_w

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("wb") as f:
        tomli_w.dump(cfg, f)
