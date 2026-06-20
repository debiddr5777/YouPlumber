"""YouPlumber CLI — fast mass YouTube audio acquisition."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn, DownloadColumn, Progress, SpinnerColumn, TextColumn,
    TimeRemainingColumn, TransferSpeedColumn,
)
from rich.table import Table

from . import __version__, config, db, sources
from .downloader import DownloadQueue, ProgressReporter
from .finalize import finalize

console = Console()
err_console = Console(stderr=True, style="red")


def _ctx_setup() -> tuple[dict, "sqlite3.Connection"]:  # type: ignore[name-defined]
    cfg = config.load_config()
    conn = db.init_db()
    return cfg, conn


# ----------------------------- add / sync -----------------------------

@click.group()
@click.version_option(__version__, prog_name="yp")
def main() -> None:
    """Fast mass YouTube audio acquisition for DJs."""


@main.command()
@click.argument("url")
@click.option("--limit", type=int, default=50, show_default=True,
              help="Max items to ingest.")
@click.option("--name", default=None, help="Display name for the source.")
def add(url: str, limit: int, name: str | None) -> None:
    """Add a YouTube channel, playlist, or single video as a source."""
    cfg, conn = _ctx_setup()
    kind = sources.detect_url_kind(url)
    if kind == "unknown":
        err_console.print(f"Could not detect source kind for: {url}")
        sys.exit(1)

    with console.status(f"[bold cyan]Fetching {kind}…[/bold cyan]"):
        if kind == "channel":
            entries = list(sources.channel_latest(url, limit=limit))
            display = name or url.rsplit("/", 1)[-1]
        elif kind == "playlist":
            entries = list(sources.playlist_entries(url, limit=limit))
            display = name or "playlist"
        elif kind == "single":
            info = sources.fetch_info(url)
            entries = [info]
            display = name or info.get("title", "single")
        elif kind == "search":
            entries = list(sources.search(name or url, limit=limit))
            display = name or url
        else:
            entries = []
            display = name or url

    if not entries:
        err_console.print("No entries found.")
        sys.exit(1)

    source_id = db.upsert_source(conn, kind, display, url)
    db.touch_source(conn, source_id)

    added = 0
    for e in entries:
        norm = sources.normalize_entry(e, source_id=source_id)
        if not norm["video_id"]:
            continue
        db.upsert_track(conn, norm)
        added += 1

    console.print(
        f"[green]OK[/green] added source [bold]{display}[/bold] "
        f"({kind}) with [bold]{added}[/bold] tracks"
    )


@main.command(name="list")
@click.option("--source", type=int, default=None, help="Filter by source id.")
@click.option("--status", default=None,
              type=click.Choice(["new", "queued", "downloading", "done",
                                 "failed", "skipped"]))
@click.option("--limit", type=int, default=50)
@click.option("--json", "as_json", is_flag=True)
def list_cmd(source: int | None, status: str | None, limit: int, as_json: bool) -> None:
    """List tracks in the library."""
    _, conn = _ctx_setup()
    rows = db.list_tracks(conn, status=status, source_id=source, limit=limit)

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return

    table = Table(title=f"Library ({len(rows)} tracks)", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Uploader", style="magenta")
    table.add_column("Dur", justify="right")
    table.add_column("Status")
    table.add_column("BPM", justify="right")
    table.add_column("Key")
    for r in rows:
        dur = f"{r['duration']//60}:{r['duration']%60:02d}" if r["duration"] else "-"
        table.add_row(
            str(r["id"]),
            (r["title"] or "")[:60],
            (r["uploader"] or "")[:25],
            dur,
            r["status"],
            f"{r['bpm']:.1f}" if r["bpm"] else "-",
            r["camelot_key"] or r["musical_key"] or "-",
        )
    console.print(table)


@main.command()
def sources_cmd() -> None:
    """List configured sources."""
    _, conn = _ctx_setup()
    rows = list(conn.execute(
        "SELECT s.id, s.kind, s.name, s.url, s.last_synced, "
        "  (SELECT COUNT(*) FROM tracks t WHERE t.source_id=s.id) AS n_tracks, "
        "  (SELECT COUNT(*) FROM tracks t WHERE t.source_id=s.id AND t.status='done') AS n_done "
        "FROM sources s ORDER BY s.id"
    ))
    table = Table(title="Sources")
    table.add_column("ID", style="cyan")
    table.add_column("Kind")
    table.add_column("Name", style="bold")
    table.add_column("URL")
    table.add_column("Tracks", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Last sync")
    for r in rows:
        sync = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_synced"])) \
            if r["last_synced"] else "-"
        table.add_row(str(r["id"]), r["kind"], r["name"], r["url"],
                      str(r["n_tracks"]), str(r["n_done"]), sync)
    console.print(table)


# ----------------------------- queue / download -----------------------------

@main.command()
@click.argument("track_ids", nargs=-1, type=int)
@click.option("--all", "select_all", is_flag=True,
              help="Queue every track not yet downloaded.")
@click.option("--status", default="new",
              type=click.Choice(["new", "failed", "skipped"]),
              help="Which tracks to queue when --all is used.")
@click.option("--from-source", type=int, default=None,
              help="Queue all matching tracks from a source id.")
def queue(track_ids: tuple[int, ...], select_all: bool, status: str,
          from_source: int | None) -> None:
    """Queue tracks for download. Pass track IDs or use --all / --from-source."""
    cfg, conn = _ctx_setup()
    q = DownloadQueue(cfg, conn)

    if select_all or from_source is not None:
        ids = [
            r["id"] for r in db.list_tracks(
                conn, status=status, source_id=from_source, limit=100000,
            )
        ]
    else:
        ids = list(track_ids)

    if not ids:
        err_console.print("No tracks to queue.")
        sys.exit(1)

    n = q.enqueue(ids)
    console.print(f"[green]OK[/green] queued [bold]{n}[/bold] tracks")


@main.command()
@click.option("--workers", type=int, default=None,
              help="Override concurrent_jobs for this run.")
@click.option("--watch/--no-watch", default=True)
def download(workers: int | None, watch: bool) -> None:
    """Process the download queue."""
    cfg, conn = _ctx_setup()
    if workers:
        cfg["downloads"]["concurrent_jobs"] = workers

    reporter = ProgressReporter()
    q = DownloadQueue(cfg, conn, reporter=reporter)

    total = conn.execute(
        "SELECT COUNT(*) c FROM tracks WHERE status='queued'"
    ).fetchone()["c"]
    if not total:
        console.print("[yellow]Nothing queued.[/yellow] Use `yp queue …` first.")
        return

    console.print(f"[bold cyan]Starting downloads:[/bold cyan] {total} tracks, "
                  f"{cfg['downloads']['concurrent_jobs']} workers")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.completed}/{task.total}"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    if watch:
        with Live(_render_status(reporter, conn, total),
                  console=console, refresh_per_second=4) as live:
            import threading
            def _runner():
                stats = q.run()
                live.stop()
                console.print(
                    f"[green]done[/green] ok={stats['ok']} failed={stats['failed']}"
                )
            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            while t.is_alive():
                time.sleep(0.25)
                live.update(_render_status(reporter, conn, total))
    else:
        stats = q.run()
        console.print(f"[green]done[/green] ok={stats['ok']} failed={stats['failed']}")


def _render_status(reporter: ProgressReporter, conn, total: int):
    snap = {item.get("track_id"): item for item in reporter.snapshot()}
    queued = conn.execute(
        "SELECT COUNT(*) c FROM tracks WHERE status IN ('queued','downloading')"
    ).fetchone()["c"]
    table = Table(title=f"Queue: {queued} active / {total} total", show_lines=False)
    table.add_column("Track", style="bold", no_wrap=False)
    table.add_column("Status")
    table.add_column("Progress", justify="right")
    table.add_column("Speed", justify="right")
    table.add_column("ETA", justify="right")
    active = conn.execute(
        "SELECT id, title FROM tracks WHERE status='downloading' LIMIT 12"
    ).fetchall()
    for r in active:
        s = snap.get(r["id"], {})
        pct = f"{s.get('pct', 0):5.1f}%"
        spd = s.get("speed")
        speed = f"{spd/1024/1024:.2f} MB/s" if spd else "-"
        eta = f"{int(s.get('eta', 0))}s" if s.get("eta") else "-"
        table.add_row((r["title"] or "")[:50], s.get("status", "?"), pct, speed, eta)
    return table


@main.command()
def status() -> None:
    """Show library statistics."""
    _, conn = _ctx_setup()
    s = db.stats(conn)
    size = db.library_size_bytes(conn)
    table = Table(title="Library status", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total tracks", str(s.get("total", 0)))
    table.add_row("  new",       str(s.get("new", 0)))
    table.add_row("  queued",    str(s.get("queued", 0)))
    table.add_row("  downloading", str(s.get("downloading", 0)))
    table.add_row("  done",      str(s.get("done", 0)))
    table.add_row("  failed",    str(s.get("failed", 0)))
    if size:
        if size >= 1024**3:
            table.add_row("Disk usage", f"{size/1024**3:.2f} GB")
        else:
            table.add_row("Disk usage", f"{size/1024**2:.1f} MB")
    console.print(table)


@main.command()
@click.option("--output", "output_dir", default=None,
              help="Destination folder. Defaults to <downloads>/library.")
@click.option("--organize-by", default=None,
              type=click.Choice(["genre", "uploader", "year", "month"]))
@click.option("--copy/--move", default=False,
              help="Copy instead of move (safer while testing).")
def finalize_cmd(output_dir: str | None, organize_by: str | None, copy: bool) -> None:
    """Rename + organize downloaded files into a clean library folder."""
    cfg, conn = _ctx_setup()
    out = output_dir or str(Path(cfg["downloads"]["output_dir"]) / "library")
    console.print(f"[bold cyan]Finalizing into[/bold cyan] {out}")
    stats = finalize(conn, output_dir=out, move=not copy, organize_by=organize_by)
    console.print(
        f"[green]done[/green] renamed={stats['renamed']} "
        f"moved={stats['moved']} skipped={stats['skipped']}"
    )


# ----------------------------- one-shot convenience -----------------------------

@main.command()
@click.argument("url")
@click.option("--limit", type=int, default=20)
@click.option("--codec", default=None,
              type=click.Choice(["mp3", "wav", "flac", "aac", "opus", "best"]))
@click.option("--workers", type=int, default=4)
def grab(url: str, limit: int, codec: str | None, workers: int) -> None:
    """One-shot: add a source, ingest, queue, and download in one go."""
    cfg, conn = _ctx_setup()
    if codec:
        cfg["audio"]["codec"] = codec
    cfg["downloads"]["concurrent_jobs"] = workers

    kind = sources.detect_url_kind(url)
    console.print(f"[bold cyan]Source kind:[/bold cyan] {kind}")

    with console.status("[bold cyan]Fetching entries…[/bold cyan]"):
        if kind == "channel":
            entries = list(sources.channel_latest(url, limit=limit))
        elif kind == "playlist":
            entries = list(sources.playlist_entries(url, limit=limit))
        elif kind == "single":
            entries = [sources.fetch_info(url)]
        elif kind == "search":
            entries = list(sources.search(url, limit=limit))
        else:
            err_console.print(f"Unsupported URL: {url}")
            sys.exit(1)

    if not entries:
        err_console.print("No entries found.")
        sys.exit(1)

    source_id = db.upsert_source(conn, kind, Path(url).name or url, url)
    track_ids: list[int] = []
    for e in entries:
        norm = sources.normalize_entry(e, source_id=source_id)
        if not norm["video_id"]:
            continue
        tid = db.upsert_track(conn, norm)
        track_ids.append(tid)

    console.print(f"[green]Ingested[/green] {len(track_ids)} tracks")

    q = DownloadQueue(cfg, conn)
    n = q.enqueue(track_ids)
    console.print(f"[green]Queued[/green] {n} new downloads")

    stats = q.run()
    console.print(
        f"[bold green]Done[/bold green] ok={stats['ok']} failed={stats['failed']}"
    )


# ----------------------------- config -----------------------------

@main.command()
def show_config() -> None:
    """Show the effective configuration."""
    cfg, _ = _ctx_setup()
    console.print_json(data=cfg)


@main.command()
@click.option("--concurrent-jobs", type=int, default=None)
@click.option("--codec", default=None,
              type=click.Choice(["mp3", "wav", "flac", "aac", "opus"]))
@click.option("--bitrate", default=None, help="MP3 bitrate (e.g. 320).")
def config_cmd(concurrent_jobs: int | None, codec: str | None,
               bitrate: str | None) -> None:
    """Update settings."""
    cfg, _ = _ctx_setup()
    changed = False
    if concurrent_jobs is not None:
        cfg["downloads"]["concurrent_jobs"] = concurrent_jobs
        changed = True
    if codec is not None:
        cfg["audio"]["codec"] = codec
        changed = True
    if bitrate is not None:
        cfg["audio"]["mp3_bitrate"] = bitrate
        changed = True
    if changed:
        config.save_config(cfg)
        console.print("[green]config updated[/green]")
    else:
        console.print("nothing to update. pass --concurrent-jobs/--codec/--bitrate")


@main.command()
def doctor() -> None:
    """Check that the environment is healthy."""
    import shutil, subprocess

    checks = []
    for name, cmd in [
        ("ffmpeg", "ffmpeg -version"),
        ("ffprobe", "ffprobe -version"),
        ("yt-dlp", "yt-dlp --version"),
        ("aria2c (optional)", "aria2c --version"),
    ]:
        path = shutil.which(cmd.split()[0])
        checks.append((name, "OK" if path else "MISSING", path or ""))

    cfg, conn = _ctx_setup()
    n = db.count_tracks(conn)
    checks.append(("DB", "OK", f"{n} tracks @ {config.DB_PATH}"))
    checks.append(("Downloads dir", "OK" if config.DOWNLOADS_DIR.exists() else "MISSING",
                   str(config.DOWNLOADS_DIR)))

    table = Table(title="yp doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status, detail in checks:
        style = "green" if status == "OK" else "red"
        table.add_row(name, f"[{style}]{status}[/{style}]", detail)
    console.print(table)


@main.command()
@click.option("--host", default="127.0.0.1", help="Bind address.")
@click.option("--port", default=8652, help="Port to listen on.", type=int)
@click.option("--open/--no-open", default=True,
              help="Open browser on start.")
def serve(host: str, port: int, open: bool) -> None:
    """Launch the web UI server."""
    import webbrowser
    from .web.server import app
    import uvicorn

    url = f"http://{host}:{port}"
    if open:
        webbrowser.open(url)
    console.print(f"[bold cyan]yp web UI[/bold cyan] → {url}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    uvicorn.run(app, host=host, port=port, log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
