"""Editing a folder (plan §2 step 5, §3.4, §4): everything kept is edited, and only the exceptions need a look.

First, C5's histograms of up to seven photos spread through the photos the folder's edit covers (also when only
some are edited again), which the white balance reads beside each photo (ADR 0017). Then, for each photo kept
after culling (or every present photo with `all_photos`), several at a time:

1. **Read** the raw file once (quarter-resolution linear camera RGB) and render it **as shot** with the fast
   preview profile: the base profile plus the owner's locked edits, and nothing else. That render is the "before".
2. **Agents:** geometry measures the lines of the as-shot render and GeoCalib's reading of it; white balance (C5)
   and colour the linear raw; exposure the as-shot render, through the look's tone curve.

Then, for the folder: **harmonize** each scene's white balance and exposure, **route** every proposal by its
confidence (applied, applied halfway, or as shot), add the technical baseline (noise reduction by the final
effective ISO) and the look, and keep every parameter the owner locked. Last, several at a time again: **render**
the edited preview, run **QA** on it, adjust once and render again when a check fails, and queue for review what
was uncertain or still fails.

Nothing here writes into a photo folder: the raw files are only read, and renders and the report go to the cache.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import locations
from .agents import baseline, c5, color, exposure, geometry, harmonize, qa, wb
from .agents import colorspace as cs
from .agents.context import ImageContext, load_contexts
from .agents.linear import read_linear
from .agents.looks import NEUTRAL, Look
from .agents.proposal import Params, Proposal, Routed
from .config import Config
from .cull.actions import final_calls
from .engine import wbmodel
from .engine.recipe import DEFAULT_BASE_PROFILE, SCHEMA, Recipe, RecipeError
from .engine.recipe import parse as parse_recipe
from .render import cached_render
from .scan.folder import folder_path

type Progress = Callable[[str, int, int], None]

QA_EDGE = 1024  # renders are checked at this long edge
AGENTS = ("geometry", "white_balance", "exposure", "color", "baseline")
GROUP_OF = {"geometry": "geometry", "white_balance": "white_balance", "exposure": "exposure", "color": "color"}


class EditError(RuntimeError):
    """A folder that cannot be edited (not in the catalog, drive away)."""


@dataclass
class EditSettings:
    look: Look = NEUTRAL
    keep_warm: bool = True  # warm light keeps some warmth (plan §4.6)
    qa: bool = True
    workers: int = 4  # photos measured and rendered at a time (each runs one rawtherapee-cli)
    size: int | None = None  # long edge of the previews; None: render.preview_size


@dataclass
class PhotoEdit:
    ctx: ImageContext
    proposals: dict[str, Proposal] = field(default_factory=dict)
    routed: dict[str, Routed] = field(default_factory=dict)
    params: Params = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    before: Path | None = None
    after: Path | None = None
    checks: list[qa.Check] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    probe: qa.NeutralProbe | None = None
    neutral: tuple[float, float] | None = None

    @property
    def review(self) -> bool:
        return bool(self.reasons or self.errors)

    def recipe(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "image": f"quick:{self.ctx.quick_hash}" if self.ctx.quick_hash else None,
            "engine": {"name": "rawtherapee", "version": "5.13", "base_profile": DEFAULT_BASE_PROFILE},
            "params": self.params,
            "provenance": self.provenance,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "image_id": self.ctx.image_id,
            "name": self.ctx.name,
            "scene": self.ctx.scene,
            "burst": self.ctx.burst,
            "proposals": {k: p.to_json() for k, p in self.proposals.items()},
            "routed": {k: {"band": r.band, "fraction": r.fraction, "review": r.review} for k, r in self.routed.items()},
            "recipe": self.recipe(),
            "before": str(self.before) if self.before else None,
            "after": str(self.after) if self.after else None,
            "qa": [c.to_json() for c in self.checks],
            "adjusted": self.adjusted,
            "review": self.review,
            "reasons": self.reasons,
            "errors": self.errors,
            "seconds": {k: round(v, 2) for k, v in self.seconds.items()},
        }


@dataclass
class EditRun:
    folder_id: int
    folder: Path
    settings: EditSettings
    photos: list[PhotoEdit] = field(default_factory=list)
    scenes_harmonized: int = 0
    seconds: float = 0.0

    def metrics(self) -> dict[str, Any]:
        return metrics(self)


def photos_to_edit(conn: sqlite3.Connection, folder_id: int, *, all_photos: bool = False) -> list[int]:
    """The photos Done culling keeps (owner's keeps, the app's keeps, and open photos), or every present one."""
    if all_photos:
        rows = conn.execute("SELECT id FROM images WHERE folder_id = ? AND state = 'present'", (folder_id,))
        return [r[0] for r in rows]
    return [call.image_id for call in final_calls(conn, folder_id) if call.final == "keep"]


def _load_render(path: Path, edge: int | None = None) -> np.ndarray:
    data = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if data is None:
        raise EditError(f"cannot read the render {path}")
    rgb = cv2.cvtColor(data, cv2.COLOR_BGR2RGB)
    if edge is not None and max(rgb.shape[:2]) > edge:
        h, w = rgb.shape[:2]
        scale = edge / max(h, w)
        rgb = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return cs.decode_srgb(rgb)


def _recipe(params: Params) -> Recipe:
    return parse_recipe({"schema": SCHEMA, "params": params})


def _measure(photo: PhotoEdit, cfg: Config, settings: EditSettings) -> None:
    """Step 1 and the agents for one photo; runs in a worker thread and releases the big arrays at the end."""
    ctx = photo.ctx
    start = time.perf_counter()
    try:
        ctx.linear = read_linear(ctx.path.read_bytes())
        photo.seconds["read"] = time.perf_counter() - start
        size = settings.size or cfg.render.preview_size
        t = time.perf_counter()
        result = cached_render(ctx.path, _recipe(ctx.locked), cfg, image_key=ctx.quick_hash, size=size, preview=True)
        photo.before = result.output
        photo.seconds["render_before"] = time.perf_counter() - t
        ctx.before = _load_render(result.output, geometry.WORK_EDGE)
        t = time.perf_counter()
        photo.proposals["geometry"] = geometry.propose(ctx)
        photo.proposals["white_balance"] = wb.propose(ctx, keep_warm=settings.keep_warm)
        photo.proposals["exposure"] = exposure.propose(ctx, settings.look)
        final = photo.proposals["white_balance"].evidence.get("final")
        multipliers = ctx.linear.camera.multipliers(final["temp"], final["green"]) if final else ctx.linear.as_shot
        photo.proposals["color"] = color.propose(ctx, multipliers)
        estimate = photo.proposals["white_balance"].evidence.get("estimate")
        photo.neutral = (float(estimate["temp"]), float(estimate["green"])) if estimate else None
        photo.probe = qa.NeutralProbe.from_linear(ctx.linear)
        photo.seconds["agents"] = time.perf_counter() - t
    except Exception as exc:  # one photo's failure must not stop the folder
        photo.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        ctx.linear = None
        ctx.before = None


def _provenance(proposal: Proposal, routed: Routed) -> dict[str, Any]:
    return {
        "source": f"agent:{proposal.agent}",
        "confidence": round(proposal.confidence, 3),
        "why": routed.reason,
        "band": routed.band,
    }


def compose(photo: PhotoEdit, look: Look) -> None:
    """Route the proposals and build the recipe: look, applied proposals, baseline, then the owner's locks."""
    ctx = photo.ctx
    params: Params = {}
    provenance: dict[str, dict[str, Any]] = {}

    def put(values: Params, source: dict[str, Any]) -> None:
        for group, items in values.items():
            for name, value in items.items():
                params.setdefault(group, {})[name] = value
                provenance[f"{group}.{name}"] = dict(source)

    put(look.params(), {"source": f"look:{look.id}"})
    for name in ("geometry", "white_balance", "exposure", "color"):
        proposal = photo.proposals.get(name)
        if proposal is None:
            continue
        if ctx.is_locked(name):
            proposal.flags = [*proposal.flags]  # computed for comparison only
            photo.routed[name] = Routed(proposal, "as_shot", {}, 0.0, False)
            continue
        routed = proposal.route()
        photo.routed[name] = routed
        applied = routed.applied
        if name == "white_balance" and look.warmth and applied:
            wbp = applied["white_balance"]
            if "temperature_k" in wbp:
                applied = {"white_balance": {**wbp, "temperature_k": round(look.warm(wbp["temperature_k"]))}}
        if name == "color" and look.vibrance:
            vib = int(applied.get("color", {}).get("vibrance", 0)) + look.vibrance
            applied = {"color": {**applied.get("color", {}), "vibrance": vib, "protect_skin": True}}
        if applied:
            put(applied, _provenance(proposal, routed))
        if routed.review:
            photo.reasons.append(f"{name.replace('_', ' ')}: {routed.reason}")
        for flag in proposal.flags:
            if f"{name}: {flag}" not in photo.reasons:
                photo.reasons.append(f"{name.replace('_', ' ')}: {flag}")
    ev = float(params.get("exposure", {}).get("ev", 0.0))
    base = baseline.propose(ctx, ev)
    photo.proposals["baseline"] = base
    routed = base.route()
    photo.routed["baseline"] = routed
    if routed.applied and not ctx.is_locked("detail"):
        put(routed.applied, _provenance(base, routed))
    # The owner's locked values win over everything (decision 5).
    for group, items in ctx.locked.items():
        for name, value in items.items():
            params.setdefault(group, {})[name] = value
            key = f"{group}.{name}"
            provenance[key] = dict(ctx.locked_provenance.get(key) or {"source": "human", "locked": True})
    if "white_balance" in ctx.locked:  # a locked white balance replaces the agent's whole group
        params["white_balance"] = dict(ctx.locked["white_balance"])
    photo.params = {g: v for g, v in params.items() if v}
    photo.provenance = provenance


def _apply_fixes(photo: PhotoEdit, fixes: list[qa.Check]) -> bool:
    changed = False
    for check in fixes:
        for group, items in check.fix.items():
            if photo.ctx.is_locked(group):
                continue
            current = photo.params.setdefault(group, {})
            for name, value in items.items():
                if photo.ctx.is_locked(group, name) or current.get(name) == value:
                    continue
                if group == "white_balance":
                    current.pop("as_shot", None)
                current[name] = value
                photo.provenance[f"{group}.{name}"] = {"source": f"qa:{qa.VERSION}", "why": check.message}
                changed = True
        if check.fix:
            photo.adjusted.append(check.name)
    return changed


def _render_and_check(photo: PhotoEdit, cfg: Config, settings: EditSettings) -> None:
    """The edited preview and QA, with at most one adjustment; runs in a worker thread."""
    if photo.errors:
        return
    size = settings.size or cfg.render.preview_size
    try:
        t = time.perf_counter()
        result = cached_render(
            photo.ctx.path, _recipe(photo.params), cfg, image_key=photo.ctx.quick_hash, size=size, preview=True
        )
        photo.after = result.output
        photo.seconds["render_after"] = time.perf_counter() - t
        if not settings.qa:
            return
        checks = qa.check(
            _load_render(result.output, QA_EDGE), photo.params, photo.ctx.trusted_faces, photo.probe, photo.neutral
        )
        failing = [c for c in checks if not c.ok]
        if failing and _apply_fixes(photo, [c for c in failing if c.fix]):
            t = time.perf_counter()
            result = cached_render(
                photo.ctx.path, _recipe(photo.params), cfg, image_key=photo.ctx.quick_hash, size=size, preview=True
            )
            photo.after = result.output
            photo.seconds["render_adjusted"] = time.perf_counter() - t
            checks = qa.check(
                _load_render(result.output, QA_EDGE), photo.params, photo.ctx.trusted_faces, photo.probe, photo.neutral
            )
        photo.checks = checks
        for c in checks:
            if not c.ok:
                photo.reasons.append(f"QA: {c.message}")
    except (RecipeError, OSError, RuntimeError) as exc:
        photo.errors.append(f"{type(exc).__name__}: {exc}")


def edit_folder(
    conn: sqlite3.Connection,
    cfg: Config,
    folder_id: int,
    *,
    settings: EditSettings | None = None,
    image_ids: list[int] | None = None,
    all_photos: bool = False,
    progress: Progress | None = None,
) -> EditRun:
    """Edit the folder's kept photos (or `image_ids`): agents, harmonization, routing, renders and QA."""
    settings = settings or EditSettings()
    start = time.perf_counter()
    row = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise EditError(f"no folder {folder_id} in the catalog")
    location = locations.Location(row["drive_uuid"], row["path"])
    if not locations.available(location, cfg):
        raise EditError("the photo drive is not connected; editing needs the raw files")
    folder = folder_path(conn, folder_id, cfg)
    ids = image_ids if image_ids is not None else photos_to_edit(conn, folder_id, all_photos=all_photos)
    run = EditRun(folder_id, folder, settings)
    run.photos = [PhotoEdit(ctx) for ctx in load_contexts(conn, folder_id, folder, ids)]
    total = len(run.photos)

    def report(step: str, done: int) -> None:
        if progress:
            progress(step, done, total)

    report("measure", 0)
    if c5.available() and run.photos:
        # C5 reads each photo beside photos spread through everything the folder's edit covers, so editing one
        # photo again gives what editing the whole folder gave.
        paths = [p.ctx.path for p in run.photos]
        if image_ids is not None:
            covered = photos_to_edit(conn, folder_id, all_photos=all_photos)
            paths = [c.path for c in load_contexts(conn, folder_id, folder, covered)] or paths
        references = c5.references(paths, workers=settings.workers)
        for photo in run.photos:
            photo.ctx.references = references
    with ThreadPoolExecutor(max_workers=max(1, settings.workers)) as pool:
        for done, _ in enumerate(pool.map(lambda p: _measure(p, cfg, settings), run.photos), start=1):
            report("measure", done)
    frames = [
        harmonize.Frame(
            p.ctx.name,
            p.ctx.scene,
            None if p.ctx.is_locked("white_balance") else p.proposals.get("white_balance"),
            None if p.ctx.is_locked("exposure", "ev") else p.proposals.get("exposure"),
            p.ctx.meta,
        )
        for p in run.photos
        if not p.errors
    ]
    run.scenes_harmonized = harmonize.harmonize(frames)
    for photo in run.photos:
        if not photo.errors:
            compose(photo, settings.look)
    report("render", 0)
    with ThreadPoolExecutor(max_workers=max(1, settings.workers)) as pool:
        for done, _ in enumerate(pool.map(lambda p: _render_and_check(p, cfg, settings), run.photos), start=1):
            report("render", done)
    run.seconds = time.perf_counter() - start
    return run


# --- metrics -------------------------------------------------------------------------------------------------------


def metrics(run: EditRun) -> dict[str, Any]:
    """Per-agent and per-run numbers for the report (plan §4's metrics, as far as they exist without references)."""
    photos = [p for p in run.photos if not p.errors]
    out: dict[str, Any] = {
        "photos": len(run.photos),
        "failed": len(run.photos) - len(photos),
        "review": sum(p.review for p in run.photos),
        "scenes_harmonized": run.scenes_harmonized,
        "seconds": round(run.seconds, 1),
    }
    out["no_review_share"] = round(1 - out["review"] / max(1, len(run.photos)), 3)
    for name in AGENTS:
        bands: dict[str, int] = {}
        changes = 0
        for p in photos:
            routed = p.routed.get(name)
            if routed is None:
                continue
            key = "abstained" if routed.proposal.abstain else routed.band
            if p.ctx.is_locked(GROUP_OF.get(name, "detail")):
                key = "locked"
            bands[key] = bands.get(key, 0) + 1
            changes += bool(routed.applied) and routed.proposal.changes
        confidences = [p.proposals[name].confidence for p in photos if name in p.proposals]
        out[name] = {
            "bands": bands,
            "changed": changes,
            "median_confidence": round(float(np.median(confidences)), 3) if confidences else None,
        }
    shifts = []
    for p in photos:
        wbp = p.proposals.get("white_balance")
        applied = p.params.get("white_balance", {})
        if wbp is not None and "temperature_k" in applied:
            shifts.append(abs(wbmodel.mired(applied["temperature_k"]) - wbmodel.mired(wbp.evidence["camera"]["temp"])))
    out["white_balance"]["median_shift_mired"] = round(float(np.median(shifts)), 1) if shifts else 0.0
    spreads = []
    for scene in {p.ctx.scene for p in photos if p.ctx.scene is not None}:
        temps = []
        for p in photos:
            if p.ctx.scene != scene:
                continue
            applied = p.params.get("white_balance", {})
            wbp = p.proposals.get("white_balance")
            if "temperature_k" in applied:
                temps.append(float(applied["temperature_k"]))
            elif wbp is not None:
                temps.append(float(wbp.evidence["camera"]["temp"]))
        if len(temps) > 1:
            spreads.append(max(temps) - min(temps))
    out["white_balance"]["scene_spread_k_max"] = round(max(spreads), 0) if spreads else 0.0
    out["white_balance"]["scenes_within_100k"] = sum(s <= 100 for s in spreads)
    out["white_balance"]["scenes"] = len(spreads)
    evs = [float(p.params.get("exposure", {}).get("ev", 0.0)) for p in photos]
    out["exposure"]["ev_median"] = round(float(np.median(evs)), 2) if evs else 0.0
    out["exposure"]["ev_range"] = [round(min(evs), 2), round(max(evs), 2)] if evs else [0.0, 0.0]
    with_faces = [p for p in photos if p.ctx.trusted_faces and p.checks]
    clipped = sum(any(c.name == "faces clipped" and not c.ok for c in p.checks) for p in with_faces)
    out["exposure"]["clipped_faces_share"] = round(clipped / max(1, len(with_faces)), 3)
    failed_checks: dict[str, int] = {}
    for p in photos:
        for c in p.checks:
            if not c.ok:
                failed_checks[c.name] = failed_checks.get(c.name, 0) + 1
    out["qa"] = {
        "adjusted": sum(bool(p.adjusted) for p in photos),
        "adjusted_by": {
            name: sum(name in p.adjusted for p in photos) for name in sorted({a for p in photos for a in p.adjusted})
        },
        "still_failing": failed_checks,
    }
    timing = [p.seconds for p in photos]
    for key in ("read", "render_before", "agents", "render_after"):
        values = [t[key] for t in timing if key in t]
        out.setdefault("timings", {})[key] = round(float(np.median(values)), 2) if values else None
    out["timings"]["per_photo_wall"] = round(run.seconds / max(1, len(run.photos)), 2)
    return out


def describe_change(photo: PhotoEdit) -> list[dict[str, str]]:
    """Reason chips: one per agent that changed something, or that was unsure."""
    chips = []
    labels = {
        "geometry": "Straighten",
        "white_balance": "WB",
        "exposure": "Exposure",
        "color": "Color",
        "baseline": "Detail",
    }
    for name, routed in photo.routed.items():
        p = routed.proposal
        if p.abstain and not p.flags:
            continue
        if not routed.applied and not routed.review and not p.flags:
            continue
        value = ""
        applied = routed.applied
        if name == "white_balance" and "temperature_k" in applied.get("white_balance", {}):
            value = f"{applied['white_balance']['temperature_k']} K"
        elif name == "exposure" and applied.get("exposure", {}).get("ev"):
            value = f"{applied['exposure']['ev']:+.2f} EV"
        elif name == "geometry" and applied.get("geometry", {}).get("rotate_deg"):
            value = f"{applied['geometry']['rotate_deg']:+.2f}°"
        elif name == "color" and applied.get("color", {}).get("vibrance"):
            value = f"vibrance +{applied['color']['vibrance']}"
        elif name == "baseline" and applied.get("detail"):
            value = f"NR {applied['detail'].get('nr_luma', 0)}/{applied['detail'].get('nr_chroma', 0)}"
        chips.append(
            {
                "agent": labels.get(name, name),
                "value": value,
                "confidence": f"{round(p.confidence * 100)} %",
                "band": routed.band if not p.abstain else "abstained",
                "why": routed.reason,
            }
        )
    return chips
