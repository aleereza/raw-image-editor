"""RawTherapee sidecars next to the photos: import the owner's edits.

Plan §3.3 and decisions 5 and 14: an existing sidecar (`DSC_0001.NEF.pp3`) holds the owner's edits. Every value the
recipe can express that differs from RawTherapee's neutral default becomes one of the owner's locked parameters.
Keys the recipe cannot express are kept in the file as they are and listed, so the UI can say they exist.

The app has no ratings (ADR 0010): it ignores a sidecar's rating (`[General] Rank`) and writes nothing back here.
Phase 2's managed sidecars (`rie.managed`) are written elsewhere; when a sidecar the app wrote changes, only the
values that differ from what the app wrote are the owner's, so only those are imported as locked.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catalog, fileops
from .engine import pp3
from .engine.recipe import SCHEMA, RecipeError
from .engine.recipe import parse as parse_recipe

SOURCE = "human:rawtherapee"

# Tools that change a render when enabled and that the recipe does not model (yet); kept as they are.
UNMODELED_TOOLS = (
    "Sharpening",
    "Local Contrast",
    "Directional Pyramid Denoising",
    "Impulse Denoising",
    "Defringing",
    "Dehaze",
    "Vignetting Correction",
    "Color toning",
    "Film Simulation",
    "Black & White",
    "HSV Equalizer",
    "Channel Mixer",
    "Locallab",
    "Wavelet",
    "Gradient",
    "PCVignette",
    "Perspective",
    "Retinex",
    "Luminance Curve",
)


@dataclass
class SidecarInfo:
    color_label: int | None = None
    in_trash: bool = False
    app_version: str | None = None
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    unmodeled: list[str] = field(default_factory=list)


def _float(profile: pp3.Profile, section: str, key: str) -> float | None:
    try:
        return float(profile[section][key])
    except (KeyError, ValueError):
        return None


def _bool(profile: pp3.Profile, section: str, key: str) -> bool | None:
    value = profile.get(section, {}).get(key)
    return None if value is None else value.strip().lower() == "true"


def read_sidecar(text: str, name: str = "") -> SidecarInfo:
    """What a RawTherapee sidecar says: colour label, trash flag, edits the recipe can hold, and the rest.

    Its rating (`[General] Rank`) is ignored (ADR 0010).
    """
    profile = pp3.parse(text)
    info = SidecarInfo()
    label = _float(profile, "General", "ColorLabel")
    info.color_label = int(label) if label is not None and label > 0 else None
    info.in_trash = bool(_bool(profile, "General", "InTrash"))
    info.app_version = profile.get("Version", {}).get("AppVersion")
    auto_exposure = bool(_bool(profile, "Exposure", "Auto"))

    params: dict[str, dict[str, Any]] = {}

    def put(group: str, key: str, value: Any, auto: bool = False) -> None:
        params.setdefault(group, {})[key] = value
        prov = {"source": SOURCE, "locked": True, "sidecar": name}
        if info.app_version:
            prov["rawtherapee"] = info.app_version
        if auto:
            prov["auto"] = True  # RawTherapee's auto levels set this value
        info.provenance[f"{group}.{key}"] = prov

    ev = _float(profile, "Exposure", "Compensation")
    if ev:
        put("exposure", "ev", round(ev, 3), auto_exposure)
    highlight = _float(profile, "Exposure", "HighlightCompr")
    if highlight:
        put("exposure", "highlight_compression", int(highlight), auto_exposure)
    if _bool(profile, "Shadows & Highlights", "Enabled"):
        shadows = _float(profile, "Shadows & Highlights", "Shadows")
        if shadows:
            put("exposure", "shadows", int(shadows))
    saturation = _float(profile, "Exposure", "Saturation")
    if saturation:
        put("color", "saturation", int(saturation))
    if _bool(profile, "Vibrance", "Enabled"):
        pastels = _float(profile, "Vibrance", "Pastels")
        if pastels:
            put("color", "vibrance", int(pastels))
    wb = profile.get("White Balance", {})
    setting = wb.get("Setting", "Camera")
    if setting == "Custom":
        temperature, green = _float(profile, "White Balance", "Temperature"), _float(profile, "White Balance", "Green")
        if temperature and green:
            put("white_balance", "temperature_k", round(temperature))
            put("white_balance", "tint", round(green, 4))
    elif setting not in ("Camera", ""):
        info.unmodeled.append(f"White Balance: {setting}")
    rotation = _float(profile, "Rotation", "Degree")
    if rotation:
        put("geometry", "rotate_deg", round(rotation, 3))
    if _bool(profile, "Crop", "Enabled"):
        box = {k.lower(): _float(profile, "Crop", k) for k in ("X", "Y", "W", "H")}
        if all(v is not None and v >= 0 for v in box.values()):
            put("geometry", "crop", {k: int(v) for k, v in box.items() if v is not None})

    coarse = _float(profile, "Coarse Transformation", "Rotate")
    if coarse:
        info.unmodeled.append(f"Coarse rotation {int(coarse)} degrees")
    if _bool(profile, "Coarse Transformation", "HorizontalFlip") or _bool(
        profile, "Coarse Transformation", "VerticalFlip"
    ):
        info.unmodeled.append("Flip")
    if auto_exposure:
        info.unmodeled.append("Exposure: auto levels")
    if _bool(profile, "Exposure", "HistogramMatching"):
        info.unmodeled.append("Exposure: auto-matched tone curve")
    for tool in UNMODELED_TOOLS:
        if _bool(profile, tool, "Enabled"):
            info.unmodeled.append(tool)

    # Keep only values the recipe schema accepts; anything out of its range is reported, not imported. White
    # balance is checked as a pair, because a custom white balance needs both values.
    def accepted(group: str, values: dict[str, Any]) -> bool:
        try:
            parse_recipe({"schema": SCHEMA, "params": {group: values}})
        except RecipeError:
            return False
        return True

    for group in list(params):
        keys = [list(params[group])] if group == "white_balance" else [[k] for k in params[group]]
        for subset in keys:
            values = {k: params[group][k] for k in subset}
            if not accepted(group, values):
                for k in subset:
                    info.unmodeled.append(f"{group}.{k} = {params[group].pop(k)} (outside the recipe's range)")
                    info.provenance.pop(f"{group}.{k}", None)
        if not params[group]:
            params.pop(group)
    info.params = params
    return info


def _drop_written(info: SidecarInfo, written: SidecarInfo) -> None:
    """Leave out the values the app itself wrote into the sidecar: what remains is what the owner changed."""
    for group in list(info.params):
        theirs = written.params.get(group, {})
        if group == "white_balance" and info.params[group] == theirs:
            keys = list(info.params[group])
        else:
            keys = [k for k, v in info.params[group].items() if group != "white_balance" and theirs.get(k) == v]
        for key in keys:
            info.params[group].pop(key, None)
            info.provenance.pop(f"{group}.{key}", None)
        if not info.params[group]:
            info.params.pop(group)


# --- catalog -------------------------------------------------------------------------------------------------------


def _image_for(conn: sqlite3.Connection, folder_id: int, sidecar_name: str) -> sqlite3.Row | None:
    base = sidecar_name[: -len(".pp3")] if sidecar_name.lower().endswith(".pp3") else sidecar_name
    return conn.execute(
        "SELECT id, name, jpeg_name FROM images WHERE folder_id = ? AND (name = ? OR jpeg_name = ?) "
        "ORDER BY name = ? DESC LIMIT 1",
        (folder_id, base, base, base),
    ).fetchone()


@dataclass
class ImportStats:
    read: int = 0
    imported: int = 0
    edits: int = 0
    errors: list[str] = field(default_factory=list)


def import_sidecars(conn: sqlite3.Connection, folder_id: int, folder: Path, names: list[str]) -> ImportStats:
    """Read the folder's sidecars; import those that are new or changed since the app last read them."""
    stats = ImportStats()
    known = {r["name"]: r for r in conn.execute("SELECT * FROM sidecars WHERE folder_id = ?", (folder_id,)).fetchall()}
    for name in names:
        path = folder / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            stats.errors.append(f"{name}: {exc}")
            continue
        stats.read += 1
        digest = fileops.sha256_bytes(data)
        row = known.get(name)
        if row is not None and row["sha256"] == digest:
            continue
        try:
            info = read_sidecar(data.decode("utf-8", errors="replace"), name)
            if row is not None and row["written"]:
                _drop_written(info, read_sidecar(row["written"], name))
        except pp3.ProfileError as exc:
            stats.errors.append(f"{name}: {exc}")
            continue
        image = _image_for(conn, folder_id, name)
        image_id = image["id"] if image is not None else None
        is_raw_sidecar = image is not None and name[: -len(".pp3")] == image["name"]
        now = catalog.now()
        with catalog.transaction(conn):
            conn.execute(
                "INSERT INTO sidecars (folder_id, image_id, name, sha256, imported_at, color_label, in_trash, "
                "edits, unmodeled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (folder_id, name) DO UPDATE SET image_id = excluded.image_id, sha256 = excluded.sha256, "
                "imported_at = excluded.imported_at, color_label = excluded.color_label, "
                "in_trash = excluded.in_trash, edits = excluded.edits, unmodeled = excluded.unmodeled",
                (
                    folder_id,
                    image_id,
                    name,
                    digest,
                    now,
                    info.color_label,
                    int(info.in_trash),
                    catalog.dumps(info.params),
                    catalog.dumps(info.unmodeled),
                ),
            )
            stats.imported += 1
            if image is None or image_id is None:
                continue
            if is_raw_sidecar and info.params:
                _store_recipe(conn, image_id, info, now)
                stats.edits += 1
    return stats


def _store_recipe(conn: sqlite3.Connection, image_id: int, info: SidecarInfo, now: str) -> None:
    current = conn.execute("SELECT version, recipe FROM recipes WHERE image_id = ?", (image_id,)).fetchone()
    recipe = {"schema": SCHEMA, "params": info.params, "provenance": info.provenance}
    if current is not None:
        old = catalog.loads(current["recipe"], {})
        # Locked values from the sidecar replace earlier imported ones; values from other sources stay.
        merged = catalog.loads(current["recipe"], {})
        params = merged.get("params", {})
        provenance = merged.get("provenance", {})
        for key, prov in list(provenance.items()):
            if prov.get("source") == SOURCE:
                group, name = key.split(".", 1)
                params.get(group, {}).pop(name, None)
                provenance.pop(key)
        for group, values in info.params.items():
            params.setdefault(group, {}).update(values)
        provenance.update(info.provenance)
        recipe = {"schema": SCHEMA, "params": {g: v for g, v in params.items() if v}, "provenance": provenance}
        if recipe == old:
            return
    version = (current["version"] + 1) if current is not None else 1
    text = catalog.dumps(recipe)
    conn.execute(
        "INSERT INTO recipes (image_id, version, recipe, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (image_id) DO UPDATE SET version = excluded.version, recipe = excluded.recipe, "
        "updated_at = excluded.updated_at",
        (image_id, version, text, now),
    )
    conn.execute(
        "INSERT INTO recipe_history (image_id, version, recipe, source, at) VALUES (?, ?, ?, 'rawtherapee-sidecar', ?)",
        (image_id, version, text, now),
    )
