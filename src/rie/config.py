"""Settings from config.toml, which Git ignores so each installation's paths and choices stay out of the repository.

Every key has a default, so commands that need no installation-specific setting (such as `rie render`) work
without a config file. `config.example.toml` documents every key.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.toml"
PROFILES_DIR = REPO_ROOT / "profiles" / "rawtherapee"

_UUID = re.compile(r"[0-9A-Fa-f][0-9A-Fa-f-]{3,35}")


class ConfigError(RuntimeError):
    """Raised when config.toml has an unknown key or an invalid value."""


@dataclass(frozen=True)
class Paths:
    photos: Path | None = None  # folder that holds the shoot folders, on the photo drive
    cache: Path = Path.home() / ".cache" / "rie"  # previews, renders, analysis; safe to delete
    data: Path = Path.home() / ".local" / "share" / "rie"  # the catalog, its backups and sidecar backups


@dataclass(frozen=True)
class Drive:
    uuid: str | None = None  # filesystem UUID of the photo drive, as in /dev/disk/by-uuid
    mountpoint: Path | None = None  # where it is mounted; found from the mount table when unset


@dataclass(frozen=True)
class RawTherapee:
    cli: str = "rawtherapee-cli"
    settings_dir: Path | None = None  # RT_SETTINGS for every run; default: <cache>/rawtherapee
    profiles_dir: Path = PROFILES_DIR  # base profiles that recipes name, such as d7100-base@1
    timeout: float = 300.0  # seconds per render


@dataclass(frozen=True)
class Render:
    preview_size: int = 2048  # long edge of previews, in pixels
    jpeg_quality: int = 95
    jpeg_subsampling: int = 3  # 1 = 4:2:0, 2 = 4:2:2, 3 = 4:4:4


@dataclass(frozen=True)
class Previews:
    thumb_size: int = 512  # long edge of grid thumbnails, made from the camera's 1620 x 1080 OtherImage
    screen_size: int = 2048  # long edge of the loupe copies, made from the full-size JpgFromRaw
    jpeg_quality: int = 85
    workers: int = 6  # processes that make the cached copies when a folder opens


@dataclass(frozen=True)
class Analysis:
    workers: int = 3  # analysis processes; each holds a raw decode and the face models (about 1 GB)


@dataclass(frozen=True)
class Safety:
    min_free_gb: float = 100.0  # warn when the photo drive has less free space than this


@dataclass(frozen=True)
class Server:
    host: str = "127.0.0.1"
    port: int = 7317


@dataclass(frozen=True)
class Claude:
    enabled: bool = True  # Claude's culling and editing passes (Phase 3); `--no-claude` turns them off per run
    model: str = ""  # empty: whatever model Claude Code is set to


@dataclass(frozen=True)
class Export:
    artist: str = ""  # written into every exported JPEG's EXIF (plan §6 Export); empty: left out
    copyright: str = ""


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    drive: Drive = field(default_factory=Drive)
    rawtherapee: RawTherapee = field(default_factory=RawTherapee)
    render: Render = field(default_factory=Render)
    previews: Previews = field(default_factory=Previews)
    analysis: Analysis = field(default_factory=Analysis)
    safety: Safety = field(default_factory=Safety)
    server: Server = field(default_factory=Server)
    claude: Claude = field(default_factory=Claude)
    export: Export = field(default_factory=Export)

    @property
    def rawtherapee_settings_dir(self) -> Path:
        return self.rawtherapee.settings_dir or self.paths.cache / "rawtherapee"

    @property
    def catalog_path(self) -> Path:
        return self.paths.data / "catalog.db"


def _path(value: Any, key: str, base: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _table(data: dict[str, Any], name: str, known: set[str]) -> dict[str, Any]:
    table = data.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = set(table) - known
    if unknown:
        raise ConfigError(f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
    return table


def _typed[T](table: dict[str, Any], key: str, kind: type[T], default: T, where: str) -> T:
    value = table.get(key, default)
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"{where}.{key} must be {kind.__name__}")
    return cast(T, value)


def parse(data: dict[str, Any], base: Path = REPO_ROOT) -> Config:
    unknown = set(data) - {
        "paths",
        "drive",
        "rawtherapee",
        "render",
        "previews",
        "analysis",
        "safety",
        "server",
        "claude",
        "export",
    }
    if unknown:
        raise ConfigError(f"unknown table(s): {', '.join(sorted(unknown))}")

    p = _table(data, "paths", {"photos", "cache", "data"})
    paths = Paths(
        photos=_path(p["photos"], "paths.photos", base) if "photos" in p else None,
        cache=_path(p["cache"], "paths.cache", base) if "cache" in p else Paths.cache,
        data=_path(p["data"], "paths.data", base) if "data" in p else Paths.data,
    )

    d = _table(data, "drive", {"uuid", "mountpoint"})
    uuid = d.get("uuid")
    if uuid is not None and (not isinstance(uuid, str) or not _UUID.fullmatch(uuid)):
        raise ConfigError("drive.uuid must look like a filesystem UUID, as listed in /dev/disk/by-uuid")
    drive = Drive(uuid=uuid, mountpoint=_path(d["mountpoint"], "drive.mountpoint", base) if "mountpoint" in d else None)

    r = _table(data, "rawtherapee", {"cli", "settings_dir", "profiles_dir", "timeout"})
    rawtherapee = RawTherapee(
        cli=_typed(r, "cli", str, RawTherapee.cli, "rawtherapee"),
        settings_dir=_path(r["settings_dir"], "rawtherapee.settings_dir", base) if "settings_dir" in r else None,
        profiles_dir=_path(r["profiles_dir"], "rawtherapee.profiles_dir", base)
        if "profiles_dir" in r
        else RawTherapee.profiles_dir,
        timeout=_typed(r, "timeout", float, RawTherapee.timeout, "rawtherapee"),
    )

    rd = _table(data, "render", {"preview_size", "jpeg_quality", "jpeg_subsampling"})
    render = Render(
        preview_size=_typed(rd, "preview_size", int, Render.preview_size, "render"),
        jpeg_quality=_typed(rd, "jpeg_quality", int, Render.jpeg_quality, "render"),
        jpeg_subsampling=_typed(rd, "jpeg_subsampling", int, Render.jpeg_subsampling, "render"),
    )
    if not 64 <= render.preview_size <= 16384:
        raise ConfigError("render.preview_size must be 64-16384")
    if not 1 <= render.jpeg_quality <= 100:
        raise ConfigError("render.jpeg_quality must be 1-100")
    if render.jpeg_subsampling not in (1, 2, 3):
        raise ConfigError("render.jpeg_subsampling must be 1, 2 or 3")

    pv = _table(data, "previews", {"thumb_size", "screen_size", "jpeg_quality", "workers"})
    previews = Previews(
        thumb_size=_typed(pv, "thumb_size", int, Previews.thumb_size, "previews"),
        screen_size=_typed(pv, "screen_size", int, Previews.screen_size, "previews"),
        jpeg_quality=_typed(pv, "jpeg_quality", int, Previews.jpeg_quality, "previews"),
        workers=_typed(pv, "workers", int, Previews.workers, "previews"),
    )
    if not 128 <= previews.thumb_size <= 1620:
        raise ConfigError("previews.thumb_size must be 128-1620")
    if not 512 <= previews.screen_size <= 6000:
        raise ConfigError("previews.screen_size must be 512-6000")
    if not 50 <= previews.jpeg_quality <= 100:
        raise ConfigError("previews.jpeg_quality must be 50-100")
    if not 1 <= previews.workers <= 32:
        raise ConfigError("previews.workers must be 1-32")

    an = _table(data, "analysis", {"workers"})
    analysis = Analysis(workers=_typed(an, "workers", int, Analysis.workers, "analysis"))
    if not 1 <= analysis.workers <= 16:
        raise ConfigError("analysis.workers must be 1-16")

    sf = _table(data, "safety", {"min_free_gb"})
    safety = Safety(min_free_gb=_typed(sf, "min_free_gb", float, Safety.min_free_gb, "safety"))
    if safety.min_free_gb < 0:
        raise ConfigError("safety.min_free_gb must be 0 or more")

    sv = _table(data, "server", {"host", "port"})
    server = Server(
        host=_typed(sv, "host", str, Server.host, "server"),
        port=_typed(sv, "port", int, Server.port, "server"),
    )
    if not 1 <= server.port <= 65535:
        raise ConfigError("server.port must be 1-65535")

    c = _table(data, "claude", {"enabled", "model"})
    claude = Claude(
        enabled=_typed(c, "enabled", bool, Claude.enabled, "claude"),
        model=_typed(c, "model", str, Claude.model, "claude"),
    )
    ex = _table(data, "export", {"artist", "copyright"})
    export = Export(
        artist=_typed(ex, "artist", str, Export.artist, "export"),
        copyright=_typed(ex, "copyright", str, Export.copyright, "export"),
    )
    return Config(
        paths=paths,
        drive=drive,
        rawtherapee=rawtherapee,
        render=render,
        previews=previews,
        analysis=analysis,
        safety=safety,
        server=server,
        claude=claude,
        export=export,
    )


def load(path: Path = CONFIG_PATH) -> Config:
    """Read config.toml; a missing file gives the defaults."""
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse(data, base=path.parent)
