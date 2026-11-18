"""Command line entry point: `uv run rie <command>`."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import catalog, config, drive, locations
from .engine.rawtherapee import RenderError, RenderResult
from .engine.recipe import RecipeError
from .engine.recipe import load as load_recipe
from .render import RenderPlan, default_output, engine, render
from .scan import exif
from .scan.folder import ScanError, scan_folder

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
    return parser


def cmd_render(args: argparse.Namespace, cfg: config.Config) -> int:
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
    return 1 if problems else 0


def _progress(step: str, done: int, total: int) -> None:
    if total:
        print(f"  {step}: {done}/{total}", file=sys.stderr, flush=True)


def cmd_open(args: argparse.Namespace, cfg: config.Config) -> int:
    conn = catalog.connect(cfg.catalog_path)
    try:
        summary = scan_folder(conn, cfg, args.folder, progress=_progress)
        catalog.backup(conn, cfg.paths.data)
    finally:
        conn.close()
    moved = f", re-linked from {summary.moved_from}" if summary.moved_from else ""
    print(
        f"{summary.path}: {summary.shots} photos ({summary.new} new, {summary.changed} changed, "
        f"{summary.unchanged} unchanged, {summary.relinked} re-linked, {summary.missing} missing{moved}); "
        f"{summary.sidecars} RawTherapee sidecars, {summary.other} other files; {summary.seconds:.1f} s"
    )
    for error in summary.errors[:10]:
        print(f"  metadata: {error}", file=sys.stderr)
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
    except (
        config.ConfigError,
        RecipeError,
        RenderError,
        ScanError,
        exif.ExifError,
        locations.DriveUnavailable,
        catalog.CatalogError,
    ) as exc:
        print(f"rie: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
