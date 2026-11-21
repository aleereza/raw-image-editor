"""`rie eval`: the agents against the owner's own edits in existing RawTherapee sidecars (plan §4.5, §4.6, §9).

Only reads the photo folders: the raw files and their `.pp3` sidecars. Renders and the report go to the cache.

- **White balance (§4.6, decision 21):** the owner set a custom white balance by clicking something neutral. What
  that means in raw multipliers is measured, not modelled: the raw is rendered twice with no input profile and an
  sRGB working space, once with the camera's white balance and once with the sidecar's white balance section
  (and its version line, so RawTherapee reads an old profile the way it was written: 5.8 profiles use the
  10-degree observer, ADR 0012). The ratio of the two renders is the ratio of the multipliers. The agent's neutral
  estimate (without the warm-light intent) and the camera's own white balance are both compared with it as the
  angle between illuminant colours, the usual colour-constancy error (target: median 2.5 degrees or less).
- **Straightening (§4.5):** the owner's `[Rotation] Degree` against the roll the geometry agent measures on the
  as-shot render (target: median error 0.3 degrees or less), over the photos the owner turned and an equal number
  the owner left level.
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
from .agents import geometry, wb
from .agents.context import Face, ImageContext
from .agents.linear import read_linear
from .config import Config
from .engine import pp3, wbmodel
from .engine.recipe import SCHEMA
from .engine.recipe import parse as parse_recipe
from .render import cached_render, engine, size_overlay
from .scan import files
from .scan.files import quick_hash

type Progress = Callable[[str, int, int], None]

# Measurement only: camera RGB times the white balance multipliers, through the sRGB curve (ADR 0012).
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


@dataclass
class Reference:
    folder: Path
    name: str  # the raw file
    sidecar: pp3.Profile


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


def eval_wb(
    refs: list[Reference],
    cfg: Config,
    conn: sqlite3.Connection | None,
    *,
    workers: int = 4,
    progress: Progress | None = None,
) -> EvalResult:
    result = EvalResult("wb")
    picks = [(r, custom_white_balance(r.sidecar)) for r in refs]
    picks = [(r, s) for r, s in picks if s is not None]
    work = cfg.paths.cache / "eval" / "probe"
    faces = {r.name: _faces(conn, r.folder / r.name, cfg) for r, _ in picks}  # SQLite stays in this thread

    def one(item: tuple[Reference, dict[str, str]]) -> dict[str, Any]:
        ref, section = item
        path = ref.folder / ref.name
        lin = read_linear(path.read_bytes())
        ctx = ImageContext(name=ref.name, path=path, meta={}, faces=faces[ref.name])
        ctx.linear = lin
        proposal = wb.propose(ctx, keep_warm=False)
        owner_mul = measured_multipliers(path, section, ref.sidecar.get("Version", {}), lin.as_shot, cfg, work)
        estimate = proposal.evidence["estimate"]
        agent_mul = lin.camera.multipliers(estimate["temp"], estimate["green"])
        owner_t, owner_g = lin.camera.temperature(owner_mul)
        camera_t, _ = lin.camera.temperature(lin.as_shot)
        return {
            "name": ref.name,
            "owner": {
                "temp": round(owner_t),
                "green": round(owner_g, 4),
                "sidecar": f"{section['Temperature']}/{section['Green']}",
            },
            "agent": estimate,
            "camera": round(camera_t),
            "agent_error": round(wbmodel.angular_error(1 / agent_mul, 1 / owner_mul), 2),
            "camera_error": round(wbmodel.angular_error(1 / lin.as_shot, 1 / owner_mul), 2),
            "agent_mired": round(wbmodel.mired(estimate["temp"]) - wbmodel.mired(owner_t), 1),
            "confidence": round(proposal.confidence, 3),
            "faces": len(ctx.faces),
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, item) for item in picks]
        for done, future in enumerate(futures, start=1):
            try:
                result.rows.append(future.result())
            except Exception as exc:  # one photo must not stop the evaluation
                result.errors.append(f"{picks[done - 1][0].name}: {type(exc).__name__}: {exc}")
            if progress:
                progress("white balance", done, len(picks))
    result.summary = {
        "agent": _stats([r["agent_error"] for r in result.rows], WB_TARGET),
        "camera": _stats([r["camera_error"] for r in result.rows], WB_TARGET),
        "agent_confident": _stats([r["agent_error"] for r in result.rows if r["confidence"] >= 0.8], WB_TARGET),
        "target_degrees": WB_TARGET,
    }
    return result


def eval_geometry(
    refs: list[Reference], cfg: Config, *, workers: int = 4, progress: Progress | None = None
) -> EvalResult:
    result = EvalResult("geometry")
    turned = [r for r in refs if abs(float(r.sidecar.get("Rotation", {}).get("Degree", "0") or 0)) > 0.05]
    level = [r for r in refs if r not in turned][: len(turned)]

    def one(ref: Reference) -> dict[str, Any]:
        path = ref.folder / ref.name
        owner = float(ref.sidecar.get("Rotation", {}).get("Degree", "0") or 0)
        render = cached_render(
            path,
            parse_recipe({"schema": SCHEMA, "params": {}}),
            cfg,
            image_key=quick_hash(path),
            size=2048,
            preview=True,
        )
        image = _linear(render.output).astype(np.float32)
        roll, support, lines = geometry.estimate(geometry.gray_of(image))
        agent = 0.0 if roll is None or abs(roll) < geometry.MIN_ROLL else roll
        return {
            "name": ref.name,
            "owner": owner,
            "agent": round(agent, 2),
            "lines": lines,
            "support": round(support, 2),
            "error": round(abs(agent - owner), 2),
        }

    items = turned + level
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, ref) for ref in items]
        for done, future in enumerate(futures, start=1):
            try:
                result.rows.append(future.result())
            except Exception as exc:
                result.errors.append(f"{items[done - 1].name}: {type(exc).__name__}: {exc}")
            if progress:
                progress("straightening", done, len(items))
    result.summary = {
        "turned": _stats([r["error"] for r in result.rows if abs(r["owner"]) > 0.05], ROLL_TARGET),
        "level_left_level": round(float(np.mean([r["agent"] == 0 for r in result.rows if abs(r["owner"]) <= 0.05])), 3)
        if level
        else None,
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
    conn = catalog.connect(cfg.catalog_path) if cfg.catalog_path.is_file() else None
    started = time.perf_counter()
    try:
        results = []
        if "wb" in agents:
            results.append(eval_wb(refs, cfg, conn, workers=workers, progress=progress))
        if "geometry" in agents:
            results.append(eval_geometry(refs, cfg, workers=workers, progress=progress))
    finally:
        if conn is not None:
            conn.close()
    out = cfg.paths.cache / "eval" / f"eval-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "folders": len(folders),
        "sidecars": len(refs),
        "seconds": round(time.perf_counter() - started, 1),
        "results": [{"agent": r.agent, "summary": r.summary, "rows": r.rows, "errors": r.errors} for r in results],
    }
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return results, out
