"""`rie eval`: the agents against the owner's own edits in existing RawTherapee sidecars (plan §4.5, §4.6, §9).

Only reads the photo folders: the raw files, their `.pp3` sidecars and their metadata (exiftool, read only). Renders
and the report go to the cache. The catalog is read for faces and never migrated, so code on an older branch can
still open it. Each row names its folder by its place in the list given, not by its path.

- **White balance (§4.6, decision 21):** the owner set a custom white balance by clicking something neutral. What
  that means in raw multipliers is measured, not modelled: the raw is rendered twice with no input profile and an
  sRGB working space, once with the camera's white balance and once with the sidecar's white balance section
  (and its version line, so RawTherapee reads an old profile the way it was written: 5.8 profiles use the
  10-degree observer, ADR 0013). The ratio of the two renders is the ratio of the multipliers. These white
  balances are compared with it as the angle between illuminant colours, the usual colour-constancy error (target:
  median 2.5 degrees or less): the camera's own, the agent's neutral estimate for the photo alone, and what
  `rie edit` would apply: the agent's proposal harmonized over its scene and routed by confidence, both neutral
  (`applied_neutral`, the measure of accuracy, since the owner clicked something neutral) and keeping some of warm
  light's warmth as `rie edit` does by default (`applied`, plan §4.6). Scenes are Phase 1's, grouped from the
  metadata of every raw file in the folder (capture time, flash and light, without the look, which needs the
  previews).
- **Straightening (§4.5):** the owner's `[Rotation] Degree` against what `rie edit` would turn the as-shot render
  by: the agent's proposal routed by confidence (target: median error 0.3 degrees or less). The photos are the ones
  the owner turned and, from each folder, as many the owner left level, spread through the folder.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import catalog, locations
from .agents import colorspace as cs
from .agents import geometry, harmonize, wb
from .agents.context import Face, ImageContext
from .agents.linear import read_linear
from .agents.proposal import Proposal
from .analysis import grouping
from .config import Config
from .engine import pp3, wbmodel
from .engine.recipe import SCHEMA
from .engine.recipe import parse as parse_recipe
from .render import cached_render, engine, size_overlay
from .scan import exif, files
from .scan.files import quick_hash

type Progress = Callable[[str, int, int], None]

# Measurement only: camera RGB times the white balance multipliers, through the sRGB curve (ADR 0013).
PROBE_OVERLAY = pp3.stack(
    {
        "Color Management": {"InputProfile": "(none)", "WorkingProfile": "sRGB", "OutputProfile": "RTv4_sRGB"},
        "LensProfile": {"LcMode": "none"},
        "RAW": {"CA": False},
        "PostDemosaicSharpening": {"Enabled": False},
        "HLRecovery": {"Enabled": False},
    }
)
PROBE_SIZE = 512
WB_TARGET = 2.5  # degrees (plan §4.6)
ROLL_TARGET = 0.3  # degrees (plan §4.5)
TURNED = 0.05  # degrees: a sidecar rotation above this counts as a turn


@dataclass
class Reference:
    folder: Path
    name: str  # the raw file
    sidecar: pp3.Profile

    @property
    def turn(self) -> float:
        """The owner's `[Rotation] Degree`."""
        return float(self.sidecar.get("Rotation", {}).get("Degree", "0") or 0)


def references(folder: Path) -> list[Reference]:
    """Raw files of a folder with a RawTherapee sidecar next to them (read only)."""
    listing = files.list_folder(folder)
    sidecars = set(listing.sidecars)
    found = []
    for shot in listing.shots:
        if shot.kind != "raw" or f"{shot.name}.pp3" not in sidecars:
            continue
        try:
            profile = pp3.load(folder / f"{shot.name}.pp3")
        except (OSError, UnicodeDecodeError, pp3.ProfileError):
            continue
        found.append(Reference(folder, shot.name, profile))
    return found


def custom_white_balance(profile: pp3.Profile) -> dict[str, str] | None:
    section = profile.get("White Balance", {})
    if section.get("Setting") != "Custom" or "Temperature" not in section or "Green" not in section:
        return None
    return section


def metadata(folder: Path) -> dict[str, dict[str, Any]]:
    """What the agents read from the metadata of every raw file in the folder, with capture time and shutter count."""
    raws = [folder / s.name for s in files.list_folder(folder).shots if s.kind == "raw"]
    out: dict[str, dict[str, Any]] = {}
    for path, record in zip(raws, exif.read_folder(raws), strict=True):
        meta = exif.normalize(record)
        meta["capture_time"] = exif.capture_time(record)
        meta["shutter_count"] = exif.shutter_count(record)
        out[path.name] = meta
    return out


def scenes(meta: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Phase 1's scenes from metadata alone (capture time, flash and light; no look): each raw file's scene number."""
    frames = [
        grouping.Frame(
            image_id=k,
            time=exif.timestamp(m.get("capture_time")),
            shutter=m.get("shutter_count"),
            name=name,
            focal=m.get("focal_length"),
            fnumber=m.get("fnumber"),
            ev=m.get("light_value"),
            flash=m.get("flash_fired"),
        )
        for k, (name, m) in enumerate(meta.items())
    ]
    return {f.name: number for number, scene in enumerate(grouping.group(frames)) for f in scene.frames}


def level_sample(refs: list[Reference]) -> list[Reference]:
    """From each folder, as many photos the owner left level as it has turned ones, spread through the folder."""
    by_folder: dict[Path, tuple[list[Reference], list[Reference]]] = {}
    for ref in refs:
        turned, level = by_folder.setdefault(ref.folder, ([], []))
        (turned if abs(ref.turn) > TURNED else level).append(ref)
    sample = []
    for turned, level in by_folder.values():
        n = min(len(turned), len(level))
        sample += [level[int((i + 0.5) * len(level) / n)] for i in range(n)]
    return sample


def _linear(path: Path) -> np.ndarray:
    data = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if data is None:
        raise RuntimeError(f"cannot read {path}")
    return cs.decode_srgb(cv2.cvtColor(data, cv2.COLOR_BGR2RGB)).astype(np.float64)


def measured_multipliers(
    raw: Path, section: dict[str, str], version: dict[str, str], as_shot: np.ndarray, cfg: Config, work: Path
) -> np.ndarray:
    """RawTherapee's multipliers (G = 1) for a sidecar's white balance section, measured on two probe renders."""
    rt = engine(cfg)
    base = cfg.rawtherapee.profiles_dir / "d7100-base.pp3"
    stem = f"{raw.stem}-{quick_hash(raw)[:12]}"
    work.mkdir(parents=True, exist_ok=True)
    overlay = work / "probe.pp3"
    if not overlay.is_file():
        overlay.write_text(pp3.dump(pp3.stack(PROBE_OVERLAY, size_overlay(PROBE_SIZE))), encoding="utf-8")
    camera_profile = work / f"{stem}-camera.pp3"
    camera_profile.write_text(pp3.dump({"White Balance": {"Setting": "Camera"}}), encoding="utf-8")
    owner_profile = work / f"{stem}-owner.pp3"
    owner = {"Version": version, "White Balance": section} if version else {"White Balance": section}
    owner_profile.write_text(pp3.dump(owner), encoding="utf-8")
    camera = rt.render(raw, [base, overlay, camera_profile], work / f"{stem}-camera.jpg").output
    picked = rt.render(raw, [base, overlay, owner_profile], work / f"{stem}-owner.jpg").output
    a, b = _linear(camera), _linear(picked)
    ok = (a > 0.02).all(axis=2) & (a < 0.6).all(axis=2) & (b > 0.02).all(axis=2) & (b < 0.6).all(axis=2)
    if ok.sum() < 500:
        raise RuntimeError("too few unclipped pixels to measure the white balance")
    ratio = np.median(b[ok] / a[ok], axis=0)
    mul = as_shot[:3] * ratio
    return mul / mul[1]


def _faces(conn: sqlite3.Connection | None, path: Path, cfg: Config) -> list[Face]:
    """Faces from the catalog when the folder has been opened; none otherwise."""
    if conn is None:
        return []
    location = locations.locate(path.parent, cfg)
    rows = conn.execute(
        "SELECT f.box, f.score, f.blink_l, f.cut_off FROM faces f JOIN images i ON i.id = f.image_id "
        "JOIN folders d ON d.id = i.folder_id WHERE d.drive_uuid = ? AND d.path = ? AND i.name = ?",
        (location.drive_uuid, location.path, path.name),
    ).fetchall()
    return [
        Face(tuple(catalog.loads(r["box"])), float(r["score"]), r["blink_l"] is not None, bool(r["cut_off"]))  # type: ignore[arg-type]
        for r in rows
    ]


@dataclass
class EvalResult:
    agent: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _stats(values: list[float], target: float) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    a = np.array(values)
    return {
        "n": len(values),
        "median": round(float(np.median(a)), 2),
        "mean": round(float(a.mean()), 2),
        "p90": round(float(np.quantile(a, 0.9)), 2),
        "within_target": round(float(np.mean(a <= target)), 3),
    }


def _share(flags: list[bool]) -> float | None:
    return round(float(np.mean(flags)), 3) if flags else None


def _run_all[T, R](
    items: list[T], one: Callable[[T], R], label: str, workers: int, progress: Progress | None
) -> tuple[list[tuple[T, R]], list[str]]:
    """`one` of every item on a thread pool, in order; an item that raises is reported and skipped."""
    done, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, item) for item in items]
        for count, (item, future) in enumerate(zip(items, futures, strict=True), start=1):
            try:
                done.append((item, future.result()))
            except Exception as exc:  # one photo must not stop the evaluation
                name = getattr(item, "name", None) or getattr(item[0], "name", "?")  # type: ignore[index]
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
            if progress:
                progress(label, count, len(items))
    return done, errors


def eval_wb(
    refs: list[Reference],
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    folders: list[Path] | None = None,
    workers: int = 4,
    progress: Progress | None = None,
) -> EvalResult:
    result = EvalResult("wb")
    folders = folders or list(dict.fromkeys(r.folder for r in refs))
    picks = [(r, custom_white_balance(r.sidecar)) for r in refs]
    picks = [(r, s) for r, s in picks if s is not None]
    work = cfg.paths.cache / "eval" / "probe"
    meta: dict[Path, dict[str, dict[str, Any]]] = {}
    scene_of: dict[tuple[Path, str], int] = {}
    for folder in dict.fromkeys(r.folder for r, _ in picks):
        meta[folder] = metadata(folder)
        for name, number in scenes(meta[folder]).items():
            scene_of[(folder, name)] = folders.index(folder) * 100_000 + number
    faces = {(r.folder, r.name): _faces(conn, r.folder / r.name, cfg) for r, _ in picks}  # SQLite stays here

    def one(item: tuple[Reference, dict[str, str]]) -> tuple[Any, ...]:
        ref, section = item
        path = ref.folder / ref.name
        lin = read_linear(path.read_bytes())
        ctx = ImageContext(
            name=ref.name, path=path, meta=meta[ref.folder].get(ref.name, {}), faces=faces[(ref.folder, ref.name)]
        )
        ctx.linear = lin
        neutral, warm = wb.propose(ctx, keep_warm=False), wb.propose(ctx)  # `rie edit` keeps warmth by default
        owner = measured_multipliers(path, section, ref.sidecar.get("Version", {}), lin.as_shot, cfg, work)
        return lin.camera, lin.as_shot, owner, neutral, warm

    done, result.errors = _run_all(picks, one, "white balance", workers, progress)
    scene = [scene_of.get((ref.folder, ref.name)) for (ref, _), _ in done]
    for index in (3, 4):  # neutral, then warm: each harmonized over its scenes as `rie edit` would
        harmonize.harmonize(
            [harmonize.Frame(ref.name, s, out[index], None, {}) for ((ref, _), out), s in zip(done, scene, strict=True)]
        )
    for ((ref, section), (camera, as_shot, owner, neutral, warm)), s in zip(done, scene, strict=True):
        owner_t, owner_g = camera.temperature(owner)
        camera_t, camera_g = camera.temperature(as_shot)
        estimate = neutral.evidence["estimate"]

        def error(mul: np.ndarray, owner: np.ndarray = owner) -> float:
            return round(wbmodel.angular_error(1 / mul, 1 / owner), 2)

        def applied(
            proposal: Proposal, camera: wbmodel.CameraModel = camera, as_shot: np.ndarray = as_shot
        ) -> tuple[np.ndarray, Any]:
            values = proposal.route().applied.get("white_balance", {})
            if not values or values.get("as_shot"):
                return as_shot, "camera"
            shown = {"temp": round(values["temperature_k"]), "green": round(values["tint"], 4)}
            return camera.multipliers(values["temperature_k"], values["tint"]), shown

        neutral_mul, neutral_shown = applied(neutral)
        warm_mul, warm_shown = applied(warm)
        result.rows.append(
            {
                "folder": folders.index(ref.folder),
                "name": ref.name,
                "scene": s,
                "owner": {
                    "temp": round(owner_t),
                    "green": round(owner_g, 4),
                    "sidecar": f"{section['Temperature']}/{section['Green']}",
                },
                "camera": {"temp": round(camera_t), "green": round(camera_g, 4)},
                "estimate": estimate,
                "applied_neutral": neutral_shown,
                "applied": warm_shown,
                "band": warm.route().band,
                "confidence": round(warm.confidence, 3),
                "harmonized": "harmonized" in warm.evidence,
                "estimate_error": error(camera.multipliers(estimate["temp"], estimate["green"])),
                "applied_neutral_error": error(neutral_mul),
                "applied_error": error(warm_mul),
                "camera_error": error(as_shot),
                "estimate_mired": round(wbmodel.mired(estimate["temp"]) - wbmodel.mired(owner_t), 1),
                "faces": len(faces[(ref.folder, ref.name)]),
            }
        )
    rows = result.rows
    result.summary = {
        "camera": _stats([r["camera_error"] for r in rows], WB_TARGET),
        "estimate": _stats([r["estimate_error"] for r in rows], WB_TARGET),
        "applied_neutral": _stats([r["applied_neutral_error"] for r in rows], WB_TARGET),
        "applied_neutral_better": _share([r["applied_neutral_error"] < r["camera_error"] for r in rows]),
        "applied_neutral_worse_by_1": _share([r["applied_neutral_error"] > r["camera_error"] + 1 for r in rows]),
        "applied": _stats([r["applied_error"] for r in rows], WB_TARGET),
        "target_degrees": WB_TARGET,
    }
    return result


def eval_geometry(
    refs: list[Reference],
    cfg: Config,
    conn: sqlite3.Connection | None = None,
    *,
    folders: list[Path] | None = None,
    workers: int = 4,
    progress: Progress | None = None,
) -> EvalResult:
    result = EvalResult("geometry")
    folders = folders or list(dict.fromkeys(r.folder for r in refs))
    items = [r for r in refs if abs(r.turn) > TURNED] + level_sample(refs)
    faces = {(r.folder, r.name): _faces(conn, r.folder / r.name, cfg) for r in items}  # SQLite stays here
    recipe = parse_recipe({"schema": SCHEMA, "params": {}})

    def one(ref: Reference) -> dict[str, Any]:
        path = ref.folder / ref.name
        render = cached_render(path, recipe, cfg, image_key=quick_hash(path), size=2048, preview=True)
        ctx = ImageContext(name=ref.name, path=path, meta={}, faces=faces[(ref.folder, ref.name)])
        ctx.before = _linear(render.output).astype(np.float32)
        proposal = geometry.propose(ctx)
        routed = proposal.route()
        applied = float(routed.applied.get("geometry", {}).get("rotate_deg", 0.0) or 0.0)
        return {
            "folder": folders.index(ref.folder),
            "name": ref.name,
            "owner": ref.turn,
            "roll": proposal.evidence["roll"],
            "lines": proposal.evidence["lines"],
            "support": proposal.evidence["support"],
            "confidence": round(proposal.confidence, 3),
            "band": routed.band,
            "applied": round(applied, 2),
            "error": round(abs(applied - ref.turn), 2),
        }

    done, result.errors = _run_all(items, one, "straightening", workers, progress)
    result.rows = [row for _, row in done]
    turned = [r for r in result.rows if abs(r["owner"]) > TURNED]
    level = [r for r in result.rows if abs(r["owner"]) <= TURNED]
    acted = [r for r in turned if r["applied"] != 0]
    result.summary = {
        "turned": _stats([r["error"] for r in turned], ROLL_TARGET),
        "turned_acted": _share([r["applied"] != 0 for r in turned]),
        "acted": _stats([r["error"] for r in acted], ROLL_TARGET),
        "acted_same_direction": _share([(r["applied"] > 0) == (r["owner"] > 0) for r in acted]),
        "level_left_level": _share([r["applied"] == 0 for r in level]),
        "target_degrees": ROLL_TARGET,
    }
    return result


def run(
    folders: list[Path],
    cfg: Config,
    *,
    agents: tuple[str, ...] = ("wb", "geometry"),
    workers: int = 4,
    progress: Progress | None = None,
) -> tuple[list[EvalResult], Path]:
    """Evaluate the agents on the sidecars of these folders; returns the results and the JSON report's path."""
    refs = [r for folder in folders for r in references(folder)]
    # Faces only: read the catalog at whatever schema it has, never migrate it.
    conn = catalog.connect(cfg.catalog_path, migrate=False) if cfg.catalog_path.is_file() else None
    started = time.perf_counter()
    try:
        results = []
        if "wb" in agents:
            results.append(eval_wb(refs, cfg, conn, folders=folders, workers=workers, progress=progress))
        if "geometry" in agents:
            results.append(eval_geometry(refs, cfg, conn, folders=folders, workers=workers, progress=progress))
    finally:
        if conn is not None:
            conn.close()
    out = cfg.paths.cache / "eval" / f"eval-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "folders": len(folders),
        "sidecars": len(refs),
        "seconds": round(time.perf_counter() - started, 1),
        "agents": {"wb": wb.VERSION, "geometry": geometry.VERSION, "harmonize": harmonize.VERSION},
        "results": [{"agent": r.agent, "summary": r.summary, "rows": r.rows, "errors": r.errors} for r in results],
    }
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return results, out
