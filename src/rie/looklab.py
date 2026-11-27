"""The look lab (plan §4.12): find a look like an eye test, save it, and choose a look per scene.

A round renders the photo with the current look and with one step either way on each of four dials: contrast,
warmth, saturation, and lift (how far the tone curve moves from a straight line: less is flatter and darker,
more is brighter and punchier). The owner picks a favourite; the next round centres on it with steps half as big.
After a few rounds the favourite is saved as a named look; saving a name again adds a version. Choosing a look for a
photo, or for its whole scene, sets the recipe's tone and colour parameters from the look and shifts the white
balance by its warmth; the white balance and exposure agents stay in charge of the rest, and locked values win.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from . import catalog
from .agents.looks import NEUTRAL, NEUTRAL_CURVE, Look
from .config import Config
from .engine.recipe import parse as parse_recipe
from .render import cached_render
from .review import ReviewError, _camera_white_balance, load_recipe, save_recipe, scene_of
from .scan.folder import folder_path

STEPS = {"contrast": 12, "warmth": 12.0, "saturation": 10, "lift": 0.35}  # round 1; each round halves them
SOURCE_LOOK = "look"


def lift_of(look: Look) -> float:
    """How far the look's curve is from a straight line, relative to the neutral curve (1 = neutral)."""
    xs = np.array([p[0] for p in NEUTRAL_CURVE])
    neutral = np.array([p[1] for p in NEUTRAL_CURVE]) - xs
    mine = look.tone(xs) - xs
    return float(np.dot(mine, neutral) / np.dot(neutral, neutral))


def with_lift(look: Look, lift: float) -> Look:
    curve = tuple((x, round(min(1.0, max(0.0, x + lift * (y - x))), 4)) for x, y in NEUTRAL_CURVE)
    return replace(look, curve=curve)


def variations(base: Look, round_number: int = 1) -> list[tuple[str, Look]]:
    """The base look and one step either way on each dial; steps halve every round."""
    scale = 0.5 ** max(0, round_number - 1)
    out = [("as it is", base)]
    contrast, warmth, saturation, lift = (STEPS[k] * scale for k in ("contrast", "warmth", "saturation", "lift"))
    for sign, word in ((1, "more"), (-1, "less")):
        out.append((f"{word} contrast", replace(base, contrast=round(base.contrast + sign * contrast))))
    out.append(("warmer", replace(base, warmth=round(base.warmth + warmth, 1))))
    out.append(("cooler", replace(base, warmth=round(base.warmth - warmth, 1))))
    for sign, word in ((1, "more"), (-1, "less")):
        out.append((f"{word} saturation", replace(base, saturation=round(base.saturation + sign * saturation))))
    current = lift_of(base)
    out.append(("brighter curve", with_lift(base, current + lift)))
    out.append(("flatter curve", with_lift(base, max(0.0, current - lift))))
    return out


def recipe_with_look(recipe: dict[str, Any], look: Look, camera: tuple[float, float] | None) -> dict[str, Any]:
    """The recipe with this look's tone and colour, and its warmth on the white balance; locked values stay."""
    params = {g: dict(v) for g, v in recipe.get("params", {}).items()}
    provenance = {k: dict(v) for k, v in recipe.get("provenance", {}).items()}
    locked = {k for k, v in provenance.items() if v.get("locked") and not str(v.get("source", "")).startswith("look")}
    for key in [k for k in provenance if k.startswith("tone.") and k not in locked]:
        params.get("tone", {}).pop(key.split(".", 1)[1], None)
        provenance.pop(key)
    source = {"source": f"{SOURCE_LOOK}:{look.id}", "locked": True}
    for group, values in look.params().items():
        for name, value in values.items():
            if f"{group}.{name}" in locked:
                continue
            params.setdefault(group, {})[name] = value
            provenance[f"{group}.{name}"] = dict(source)
    if look.warmth and "white_balance.temperature_k" not in locked:
        wb = params.get("white_balance", {})
        if "temperature_k" in wb:
            temp, green = float(wb["temperature_k"]), float(wb["tint"])
        elif camera is not None:
            temp, green = camera
        else:
            temp = green = None
        if temp is not None and green is not None:
            params["white_balance"] = {"temperature_k": round(look.warm(temp)), "tint": round(green, 4)}
            for name in ("temperature_k", "tint"):
                provenance[f"white_balance.{name}"] = {**source, "warmth": look.warmth}
            provenance.pop("white_balance.as_shot", None)
    return {**recipe, "params": {g: v for g, v in params.items() if v}, "provenance": provenance}


def render_variations(
    conn: sqlite3.Connection, cfg: Config, image_id: int, base: Look, round_number: int = 1, *, workers: int = 4
) -> list[dict[str, Any]]:
    """Render the photo with each variation of `base`; returns their labels, looks and cached previews."""
    row = conn.execute("SELECT folder_id, name, quick_hash FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise ReviewError(f"no photo {image_id}")
    _version, recipe = load_recipe(conn, image_id)
    camera = _camera_white_balance(conn, image_id)
    raw = folder_path(conn, row["folder_id"], cfg) / row["name"]
    items = variations(base, round_number)

    def one(item: tuple[str, Look]) -> dict[str, Any]:
        label, look = item
        varied = recipe_with_look(recipe, look, camera)
        result = cached_render(
            raw, parse_recipe(varied), cfg, image_key=row["quick_hash"], size=cfg.render.preview_size, preview=True
        )
        return {"label": label, "look": look.to_json(), "path": str(result.output), "key": result.output.stem}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, items))


def variation_path(cfg: Config, key: str) -> Path:
    """A rendered variation in the render cache, by its cache key (hex), never anything else."""
    if not key or any(c not in "0123456789abcdef" for c in key):
        raise ReviewError("not a render key")
    path = cfg.paths.cache / "renders" / "cache" / key[:2] / f"{key}.jpg"
    if not path.is_file():
        raise ReviewError("that render is no longer in the cache")
    return path


def save_look(conn: sqlite3.Connection, look: Look, name: str, description: str = "") -> Look:
    """Save a look under a name; a name already used gets its next version."""
    name = name.strip().lower().replace(" ", "-")
    if not name or not all(c.isalnum() or c == "-" for c in name) or name == NEUTRAL.name:
        raise ReviewError("a look's name is letters, digits and dashes, and not 'neutral'")
    row = conn.execute("SELECT MAX(version) FROM looks WHERE name = ?", (name,)).fetchone()
    saved = replace(look, name=name, version=int(row[0] or 0) + 1, description=description or look.description)
    with catalog.transaction(conn):
        conn.execute(
            "INSERT INTO looks (name, version, definition, created_at) VALUES (?, ?, ?, ?)",
            (saved.name, saved.version, catalog.dumps(saved.to_json()), catalog.now()),
        )
    return saved


def all_looks(conn: sqlite3.Connection) -> list[Look]:
    """The neutral look and the latest version of each saved look."""
    looks = [NEUTRAL]
    for row in conn.execute(
        "SELECT definition FROM looks l WHERE version = (SELECT MAX(version) FROM looks WHERE name = l.name) "
        "ORDER BY name"
    ):
        looks.append(Look.from_json(catalog.loads(row["definition"], {})))
    return looks


def get_look(conn: sqlite3.Connection, look_id: str) -> Look:
    if look_id == NEUTRAL.id:
        return NEUTRAL
    name, _, version = look_id.partition("@")
    row = conn.execute(
        "SELECT definition FROM looks WHERE name = ? AND version = ?", (name, int(version or 0))
    ).fetchone()
    if row is None:
        raise ReviewError(f"no look {look_id}")
    return Look.from_json(catalog.loads(row["definition"], {}))


def apply_look(conn: sqlite3.Connection, image_id: int, look: Look, *, scene: bool = False) -> list[int]:
    """Use a look for a photo, or for every edited photo of its scene; returns the photos changed."""
    targets = scene_of(conn, image_id) if scene else [image_id]
    changed = []
    for target in targets:
        _version, recipe = load_recipe(conn, target)
        updated = recipe_with_look(recipe, look, _camera_white_balance(conn, target))
        with catalog.transaction(conn):
            if updated != recipe:
                save_recipe(conn, target, updated, f"{SOURCE_LOOK}:{look.id}")
                changed.append(target)
            conn.execute(
                "UPDATE edits SET look = ?, updated_at = ? WHERE image_id = ?", (look.id, catalog.now(), target)
            )
    return changed
