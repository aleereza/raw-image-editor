"""Command line entry point: `uv run rie <command>`."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import catalog, config, drive, locations, safety
from .cull import actions
from .cull.actions import CullError
from .edit import EditError
from .engine.rawtherapee import RenderError, RenderResult
from .engine.recipe import RecipeError
from .engine.recipe import load as load_recipe
from .export import ExportError
from .pipeline import open_folder
from .render import RenderPlan, default_output, engine, render
from .review import ReviewError
from .scan import exif
from .scan.folder import ScanError

DCP_DIRS = (Path("/usr/share/rawtherapee/dcpprofiles"), Path("/usr/local/share/rawtherapee/dcpprofiles"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rie", description="AI-assisted RAW workflow (RawTherapee render engine).")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH, help="settings file (default: config.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    r = commands.add_parser("render", help="render a raw file to JPEG from a recipe with rawtherapee-cli")
    r.add_argument("raw", type=Path, help="the raw file (read only)")
    r.add_argument("--recipe", type=Path, required=True, help="recipe JSON (schema rie.recipe/1)")
    r.add_argument("-o", "--out", type=Path, help="output JPEG (default: <cache>/renders/<name>.jpg)")
    size = r.add_mutually_exclusive_group()
    size.add_argument("--size", type=int, metavar="PX", help="resize so the long edge is PX pixels")
    size.add_argument("--preview", action="store_true", help="resize to render.preview_size (2048 px by default)")
    r.add_argument("--profile-out", type=Path, metavar="PP3", help="also save the compiled per-image profile here")
    r.add_argument("--dry-run", action="store_true", help="print the profiles and command without rendering")

    commands.add_parser("check", help="check rawtherapee-cli, the D7100 profile, exiftool, Claude Code and the drive")

    o = commands.add_parser("open", help="scan a shoot folder into the catalog (reads the folder, never writes it)")
    o.add_argument("folder", type=Path, help="the shoot folder")
    o.add_argument("--no-analysis", action="store_true", help="only scan and make previews")
    o.add_argument("--workers", type=int, help="analysis processes (default: analysis.workers)")

    sv = commands.add_parser("serve", help="start the culling UI (http://localhost:7317 by default)")
    sv.add_argument("--host", help="address to listen on (default: server.host)")
    sv.add_argument("--port", type=int, help="port (default: server.port)")
    sv.add_argument("--open", action="store_true", help="open the UI in the default browser")

    rs = commands.add_parser("restore", help="move a folder's trashed rejects (or one photo) back from the trash")
    rs.add_argument("folder", type=Path, help="the shoot folder")
    rs.add_argument("--photo", help="only this photo, by file name")

    ed = commands.add_parser(
        "edit", help="edit a folder's kept photos with the agents; previews and a report go to the cache"
    )
    ed.add_argument("folder", type=Path, help="the shoot folder, opened before with `rie open` (read only)")
    ed.add_argument("--all", action="store_true", help="every present photo, not only those Done culling keeps")
    ed.add_argument("--photo", action="append", help="only this photo, by file name (repeat for more)")
    ed.add_argument("--no-warmth", action="store_true", help="neutralize warm light fully")
    ed.add_argument("--no-qa", action="store_true", help="skip the checks on the edited render")
    ed.add_argument("--workers", type=int, default=4, help="photos at a time (default 4)")
    ed.add_argument("--size", type=int, metavar="PX", help="long edge of the previews (default render.preview_size)")

    ex = commands.add_parser("export", help="write full-size JPEGs of the edited, kept photos into the folder's jpg/")
    ex.add_argument("folder", type=Path, help="the shoot folder, edited before with `rie edit`")
    ex.add_argument("--photo", action="append", help="only this photo, by file name (repeat for more)")
    ex.add_argument("--workers", type=int, default=3, help="renders at a time (default 3)")

    ev = commands.add_parser(
        "eval", help="compare the agents with the owner's edits in RawTherapee sidecars (reads the folders only)"
    )
    ev.add_argument("folders", type=Path, nargs="+", help="shoot folders whose sidecars hold the owner's edits")
    ev.add_argument("--agent", choices=("wb", "geometry", "all"), default="all", help="which agent (default all)")
    ev.add_argument("--workers", type=int, default=4, help="photos at a time (default 4)")
    return parser


def _inside(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return False
    path, folder = Path(os.path.abspath(path)), Path(os.path.abspath(folder))
    return path == folder or folder in path.parents


def cmd_render(args: argparse.Namespace, cfg: config.Config) -> int:
    if args.out is not None and _inside(args.out, cfg.paths.photos):
        # Exports into a shoot folder go through the file operations module (Phase 2), never through -o.
        raise RenderError(f"refusing to write a render inside the photos folder: {args.out}")
    recipe = load_recipe(args.recipe)
    size = cfg.render.preview_size if args.preview else args.size
    if size is not None and not 64 <= size <= 16384:
        raise RecipeError(f"--size must be 64-16384, got {size}")
    output = args.out or default_output(args.raw, cfg)
    result = render(args.raw, recipe, output, cfg, size=size, profile_out=args.profile_out, dry_run=args.dry_run)
    if isinstance(result, RenderPlan):
        print(f"# base profile: {result.base}")
        print(result.image_profile, end="")
        if result.size_profile:
            print(result.size_profile, end="")
        print("# command (the temporary profiles are removed after a dry run):")
        print(" ".join(result.command))
        return 0
    assert isinstance(result, RenderResult)
    print(f"{result.output}  ({result.seconds:.1f} s)")
    return 0


def _version(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    return lines[0] if lines else "?"


def cmd_check(cfg: config.Config) -> int:
    problems = 0
    try:
        rt = engine(cfg)
        print(f"rawtherapee-cli: {rt.version()} ({rt.executable()}), settings in {rt.settings_dir}")
    except (RenderError, OSError) as exc:
        print(f"rawtherapee-cli: MISSING ({exc})")
        problems += 1
    dcp = next((d / "NIKON D7100.dcp" for d in DCP_DIRS if (d / "NIKON D7100.dcp").is_file()), None)
    print(f"D7100 camera profile: {dcp or 'MISSING (InputProfile=(cameraICC) falls back to the camera matrix)'}")
    problems += dcp is None
    base = cfg.rawtherapee.profiles_dir / "d7100-base.pp3"
    print(f"base profile: {base if base.is_file() else 'MISSING ' + str(base)}")
    problems += not base.is_file()
    for name, cmd in (("exiftool", ["exiftool", "-ver"]), ("claude", ["claude", "--version"])):
        version = _version(cmd)
        print(f"{name}: {version or 'MISSING'}")
        problems += version is None
    status = drive.drive_status(cfg.drive.uuid, cfg.drive.mountpoint)
    where = f" at {status.mountpoint}" if status.mountpoint else ""
    device = f" ({status.device})" if status.device else ""
    print(f"photo drive: {status.state}{where}{device}{': ' + status.detail if status.detail else ''}")
    if status.connected and status.mountpoint is not None:
        report = safety.space_report(status.mountpoint, cfg.safety.min_free_gb)
        print(f"drive space: {report.describe()}")
    findings = safety.trash_autoempty_findings()
    if findings:
        for finding in findings:
            print(f"WARNING: something may empty the trash on its own: {finding}")
    else:
        print("trash: nothing found that empties it automatically")
    return 1 if problems else 0


def _progress(step: str, done: int, total: int) -> None:
    if total:
        print(f"  {step}: {done}/{total}", file=sys.stderr, flush=True)


def cmd_open(args: argparse.Namespace, cfg: config.Config) -> int:
    conn = catalog.connect(cfg.catalog_path)
    try:
        result = open_folder(
            conn, cfg, args.folder, progress=_progress, analyze=not args.no_analysis, workers=args.workers
        )
        catalog.backup(conn, cfg.paths.data)
    finally:
        conn.close()
    summary = result.scan
    moved = f", re-linked from {summary.moved_from}" if summary.moved_from else ""
    print(
        f"{summary.path}: {summary.shots} photos ({summary.new} new, {summary.changed} changed, "
        f"{summary.unchanged} unchanged, {summary.relinked} re-linked, {summary.missing} missing{moved}); "
        f"{summary.sidecars} RawTherapee sidecars, {summary.other} other files; {summary.seconds:.1f} s"
    )
    for error in summary.errors[:10]:
        print(f"  metadata: {error}", file=sys.stderr)
    pv = result.previews
    print(f"previews: {pv.done} made, {pv.skipped} already cached, {pv.failed} failed; {pv.seconds:.1f} s")
    for error in pv.errors[:10]:
        print(f"  preview: {error}", file=sys.stderr)
    sc = result.sidecars
    if sc is not None and sc.read:
        print(
            f"RawTherapee sidecars: {sc.imported} imported ({sc.edits} with edits kept as locked), "
            f"{sc.read - sc.imported} unchanged"
        )
        for error in sc.errors[:10]:
            print(f"  sidecar: {error}", file=sys.stderr)
    an = result.analysis
    if an is not None:
        state = " (paused: the drive is not connected)" if an.paused else ""
        print(f"analysis: {an.done} analysed, {an.skipped} up to date, {an.failed} failed; {an.seconds:.1f} s{state}")
        if an.note:
            print(f"  {an.note}", file=sys.stderr)
        for error in an.errors[:10]:
            print(f"  analysis: {error}", file=sys.stderr)
    if result.groups is not None:
        g = result.groups
        print(f"groups: {g.scenes} scenes, {g.bursts} bursts ({g.in_bursts} photos in bursts of two or more)")
    if result.sort is not None:
        piles = result.sort.piles
        print(
            f"sorted by {result.sort.model}: {piles['your_call']} your call, {piles['auto_reject']} auto-reject, "
            f"{piles['auto_keep']} auto-keep"
        )
    return 0


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """True once something accepts connections on host:port, False after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def cmd_serve(args: argparse.Namespace, cfg: config.Config) -> int:
    import uvicorn

    from .api.app import WEB_DIST, create_app

    host, port = args.host or cfg.server.host, args.port or cfg.server.port
    if not (WEB_DIST / "index.html").is_file():
        print("note: the web UI is not built; run `npm install` and `npm run build` in web/", file=sys.stderr)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    app = create_app(cfg)
    if args.open and shutil.which("xdg-open"):

        def open_browser() -> None:
            # Only once the server answers, so the browser never finds nothing there.
            if wait_for_port("127.0.0.1" if host == "0.0.0.0" else host, port):
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        threading.Thread(target=open_browser, name="rie-open-browser", daemon=True).start()
    print(f"rie: serving {url}; keep this running while you cull, Ctrl+C stops it", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=3))
    app.state.server = server  # the event stream ends itself when the server is stopping (see rie.api.app)
    with contextlib.suppress(KeyboardInterrupt):  # uvicorn re-raises Ctrl+C after a clean shutdown
        server.run()
    print("rie: stopped", flush=True)
    return 0


def cmd_restore(args: argparse.Namespace, cfg: config.Config) -> int:
    conn = catalog.connect(cfg.catalog_path)
    try:
        location = locations.locate(args.folder, cfg)
        row = conn.execute(
            "SELECT id FROM folders WHERE drive_uuid = ? AND path = ?", (location.drive_uuid, location.path)
        ).fetchone()
        if row is None:
            raise CullError(f"{args.folder} is not in the catalog")
        if args.photo:
            image = conn.execute(
                "SELECT id FROM images WHERE folder_id = ? AND name = ?", (row["id"], args.photo)
            ).fetchone()
            if image is None:
                raise CullError(f"no photo {args.photo} in {args.folder}")
            count = actions.restore(conn, cfg, image_id=image["id"])
        else:
            count = actions.restore(conn, cfg, folder_id=row["id"])
    finally:
        conn.close()
    print(f"restored {count} files")
    return 0


def _catalog_folder(conn: sqlite3.Connection, cfg: config.Config, folder: Path) -> int:
    location = locations.locate(folder, cfg)
    row = conn.execute(
        "SELECT id FROM folders WHERE drive_uuid = ? AND path = ?", (location.drive_uuid, location.path)
    ).fetchone()
    if row is None:
        raise CullError(f"{folder} is not in the catalog; open it first with `rie open`")
    return int(row["id"])


def cmd_edit(args: argparse.Namespace, cfg: config.Config) -> int:
    from .edit import EditSettings, edit_folder
    from .edit_report import write_report
    from .review import store_run

    conn = catalog.connect(cfg.catalog_path)
    try:
        folder_id = _catalog_folder(conn, cfg, args.folder)
        ids = None
        if args.photo:
            marks = ",".join("?" * len(args.photo))
            rows = conn.execute(
                f"SELECT id FROM images WHERE folder_id = ? AND name IN ({marks})", (folder_id, *args.photo)
            ).fetchall()
            if len(rows) != len(set(args.photo)):
                raise CullError("some --photo names are not in the folder")
            ids = [r["id"] for r in rows]
        settings = EditSettings(keep_warm=not args.no_warmth, qa=not args.no_qa, workers=args.workers, size=args.size)
        run = edit_folder(
            conn, cfg, folder_id, settings=settings, image_ids=ids, all_photos=args.all, progress=_progress
        )
        run_id = store_run(conn, run)
        catalog.backup(conn, cfg.paths.data)
        page = write_report(run, cfg)
    finally:
        conn.close()
    m = run.metrics()
    print(
        f"edited {m['photos'] - m['failed']} of {m['photos']} photos in {m['seconds']:.0f} s; "
        f"{m['review']} need a look ({m['no_review_share']:.0%} need no review)"
    )
    for name in ("geometry", "white_balance", "exposure", "color", "baseline"):
        a = m[name]
        bands = ", ".join(f"{k} {v}" for k, v in sorted(a["bands"].items()))
        print(f"  {name.replace('_', ' ')}: changed {a['changed']}; {bands}")
    for photo in run.photos:
        for error in photo.errors:
            print(f"  {photo.ctx.name}: {error}", file=sys.stderr)
    print(f"stored as edit run {run_id}; review it in `rie serve` (Review) or the report: {page}")
    return 0


def cmd_export(args: argparse.Namespace, cfg: config.Config) -> int:
    from .export import export_folder

    conn = catalog.connect(cfg.catalog_path)
    try:
        folder_id = _catalog_folder(conn, cfg, args.folder)
        ids = None
        if args.photo:
            marks = ",".join("?" * len(args.photo))
            rows = conn.execute(
                f"SELECT id FROM images WHERE folder_id = ? AND name IN ({marks})", (folder_id, *args.photo)
            ).fetchall()
            ids = [r["id"] for r in rows]
        result = export_folder(conn, cfg, folder_id, image_ids=ids, workers=args.workers, progress=_progress)
        catalog.backup(conn, cfg.paths.data)
    finally:
        conn.close()
    print(
        f"exported {result.exported} JPEGs ({result.bytes / 1e6:.0f} MB) into jpg/ in {result.seconds:.0f} s; "
        f"{result.skipped} unchanged, {result.replaced} older JPEGs moved to the trash first, {result.failed} failed"
    )
    for error in result.errors[:10]:
        print(f"  {error}", file=sys.stderr)
    return 1 if result.failed else 0


def cmd_eval(args: argparse.Namespace, cfg: config.Config) -> int:
    from . import evaluate

    agents = ("wb", "geometry") if args.agent == "all" else (args.agent,)
    for folder in args.folders:
        if _inside(cfg.paths.cache, folder):
            raise CullError(f"{folder} holds the app's cache; give a shoot folder")
    results, report = evaluate.run(args.folders, cfg, agents=agents, workers=args.workers, progress=_progress)
    for result in results:
        print(f"{result.agent}: {json.dumps(result.summary)}")
        for error in result.errors[:10]:
            print(f"  {error}", file=sys.stderr)
    print(f"report: {report}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
        if args.command == "render":
            return cmd_render(args, cfg)
        if args.command == "check":
            return cmd_check(cfg)
        if args.command == "open":
            return cmd_open(args, cfg)
        if args.command == "restore":
            return cmd_restore(args, cfg)
        if args.command == "serve":
            return cmd_serve(args, cfg)
        if args.command == "edit":
            return cmd_edit(args, cfg)
        if args.command == "eval":
            return cmd_eval(args, cfg)
        if args.command == "export":
            return cmd_export(args, cfg)
    except (
        config.ConfigError,
        RecipeError,
        RenderError,
        ScanError,
        exif.ExifError,
        locations.DriveUnavailable,
        catalog.CatalogError,
        CullError,
        EditError,
        ExportError,
        ReviewError,
    ) as exc:
        print(f"rie: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
