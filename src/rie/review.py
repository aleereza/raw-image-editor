"""Edited photos in the catalog, and the owner's review actions (plan §2 step 6, §6 Review).

`store_run` keeps an edit run: every proposal with what of it went into the recipe, a new recipe version per photo
(the recipe holds provenance and locks, `recipe_history` every version), and each photo's review state (why it
needs a look, QA, its as-shot and edited previews in the render cache).

The owner's actions each make a new recipe version and lock what they touch, so the next edit run keeps it
(plan §0: anything you change is locked): nudges (exposure ±0.15 EV, cooler or warmer by 10 mired, a turn of
±0.1 degrees), resetting a group to as shot, locking or unlocking a value, and applying a photo's values to its
whole scene. Every change to a value the app set is also written to `overrides`, the style model's training data
(plan §4.11).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import catalog
from .config import Config
from .edit import EditRun, PhotoEdit
from .engine import wbmodel
from .engine.recipe import DEFAULT_BASE_PROFILE, SCHEMA
from .engine.recipe import parse as parse_recipe
from .render import cached_render
from .scan.folder import folder_path

SOURCE_OWNER = "human:review"
GROUPS = ("geometry", "white_balance", "exposure", "color", "tone", "detail")
NUDGES = {
    "ev": ("exposure", "ev", 0.15),
    "warmth": ("white_balance", "temperature_k", 10.0),  # mired per step; positive is warmer
    "rotate": ("geometry", "rotate_deg", 0.1),
}


class ReviewError(RuntimeError):
    """An action that cannot be done (no edit for the photo, unknown parameter)."""


# --- storing a run ---------------------------------------------------------------------------------------------


def load_recipe(conn: sqlite3.Connection, image_id: int) -> tuple[int, dict[str, Any]]:
    row = conn.execute("SELECT version, recipe FROM recipes WHERE image_id = ?", (image_id,)).fetchone()
    if row is None:
        return 0, {"schema": SCHEMA, "params": {}, "provenance": {}}
    return int(row["version"]), catalog.loads(row["recipe"], {})


def save_recipe(conn: sqlite3.Connection, image_id: int, recipe: dict[str, Any], source: str) -> int:
    """Store a new version of a photo's recipe unless it is the same as the current one; returns the version."""
    parse_recipe(recipe)  # never store a recipe RawTherapee would misread
    version, current = load_recipe(conn, image_id)
    if version and current == recipe:
        return version
    version += 1
    text = catalog.dumps(recipe)
    now = catalog.now()
    conn.execute(
        "INSERT INTO recipes (image_id, version, recipe, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT (image_id) DO "
        "UPDATE SET version = excluded.version, recipe = excluded.recipe, updated_at = excluded.updated_at",
        (image_id, version, text, now),
    )
    conn.execute(
        "INSERT INTO recipe_history (image_id, version, recipe, source, at) VALUES (?, ?, ?, ?, ?)",
        (image_id, version, text, source, now),
    )
    return version


def _store_photo(conn: sqlite3.Connection, run_id: int, photo: PhotoEdit, look: str) -> None:
    image_id = photo.ctx.image_id
    assert image_id is not None
    now = catalog.now()
    for name, proposal in photo.proposals.items():
        routed = photo.routed.get(name)
        band = "abstained" if proposal.abstain else routed.band if routed else "as_shot"
        if photo.ctx.is_locked(name):
            band = "locked"
        conn.execute(
            "INSERT INTO proposals (image_id, run_id, agent, params, as_shot, applied, confidence, band, rationale, "
            "evidence, flags, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                image_id,
                run_id,
                proposal.agent,
                catalog.dumps(proposal.params),
                catalog.dumps(proposal.as_shot),
                catalog.dumps(routed.applied if routed else {}),
                proposal.confidence,
                band,
                routed.reason if routed else proposal.rationale,
                catalog.dumps(proposal.evidence),
                catalog.dumps(proposal.flags),
                now,
            ),
        )
    save_recipe(conn, image_id, photo.recipe(), f"edit-run:{run_id}")
    conn.execute(
        "INSERT INTO edits (image_id, run_id, look, review, reasons, qa, before, after, reviewed_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?) ON CONFLICT (image_id) DO UPDATE SET run_id = excluded.run_id, "
        "look = excluded.look, review = excluded.review, reasons = excluded.reasons, qa = excluded.qa, "
        "before = excluded.before, after = excluded.after, reviewed_at = NULL, updated_at = excluded.updated_at",
        (
            image_id,
            run_id,
            look,
            int(photo.review),
            catalog.dumps([*photo.reasons, *photo.errors]),
            catalog.dumps([c.to_json() for c in photo.checks]),
            str(photo.before) if photo.before else None,
            str(photo.after) if photo.after else None,
            now,
        ),
    )


def store_run(conn: sqlite3.Connection, run: EditRun) -> int:
    """Keep an edit run in the catalog; returns its id. Photos that failed keep their earlier edit."""
    with catalog.transaction(conn):
        cur = conn.execute(
            "INSERT INTO edit_runs (folder_id, look, settings, metrics, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run.folder_id,
                run.settings.look.id,
                catalog.dumps({"keep_warm": run.settings.keep_warm, "qa": run.settings.qa, "size": run.settings.size}),
                catalog.dumps(run.metrics()),
                catalog.now(),
                catalog.now(),
            ),
        )
        run_id = int(cur.lastrowid or 0)
        for photo in run.photos:
            if photo.ctx.image_id is not None and not photo.errors:
                _store_photo(conn, run_id, photo, run.settings.look.id)
    return run_id


# --- reading the review state ----------------------------------------------------------------------------------


def edits_of_folder(conn: sqlite3.Connection, folder_id: int) -> list[dict[str, Any]]:
    """Every edited photo of a folder with its recipe, review state and the latest run's proposals."""
    rows = conn.execute(
        "SELECT i.id, i.name, i.capture_time, e.look, e.review, e.reasons, e.qa, e.before, e.after, e.reviewed_at, "
        "e.run_id, e.updated_at, r.version, r.recipe, (SELECT g.parent_id FROM groups g JOIN group_members m ON "
        "m.group_id = g.id WHERE g.kind = 'burst' AND m.image_id = i.id) AS scene "
        "FROM edits e JOIN images i ON i.id = e.image_id LEFT JOIN recipes r ON r.image_id = i.id "
        "WHERE i.folder_id = ? AND i.state = 'present' ORDER BY i.capture_time, i.shutter_count, i.name",
        (folder_id,),
    ).fetchall()
    proposals: dict[int, list[dict[str, Any]]] = {}
    for p in conn.execute(
        "SELECT p.image_id, p.agent, p.params, p.applied, p.confidence, p.band, p.rationale, p.flags FROM proposals p "
        "JOIN edits e ON e.image_id = p.image_id AND e.run_id = p.run_id JOIN images i ON i.id = p.image_id "
        "WHERE i.folder_id = ? ORDER BY p.id",
        (folder_id,),
    ):
        proposals.setdefault(p["image_id"], []).append(
            {
                "agent": p["agent"],
                "params": catalog.loads(p["params"], {}),
                "applied": catalog.loads(p["applied"], {}),
                "confidence": round(p["confidence"], 3),
                "band": p["band"],
                "why": p["rationale"],
                "flags": catalog.loads(p["flags"], []),
            }
        )
    out = []
    for r in rows:
        recipe = catalog.loads(r["recipe"], {}) or {}
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "time": r["capture_time"],
                "scene": r["scene"],
                "look": r["look"],
                "review": bool(r["review"]),
                "reviewed": r["reviewed_at"] is not None,
                "reasons": catalog.loads(r["reasons"], []),
                "qa": catalog.loads(r["qa"], []),
                "has_before": bool(r["before"]) and Path(r["before"]).is_file(),
                "has_after": bool(r["after"]) and Path(r["after"]).is_file(),
                "version": r["version"],
                "params": recipe.get("params", {}),
                "provenance": recipe.get("provenance", {}),
                "proposals": proposals.get(r["id"], []),
                "v": f"{r['version']}-{r['updated_at']}",
            }
        )
    return out


def preview_path(conn: sqlite3.Connection, image_id: int, kind: str, cache_root: Path) -> Path:
    """A photo's cached as-shot ('before') or edited ('after') preview; only paths inside the render cache."""
    if kind not in ("before", "after"):
        raise ReviewError(f"unknown preview {kind!r}")
    row = conn.execute(f"SELECT {kind} AS path FROM edits WHERE image_id = ?", (image_id,)).fetchone()
    if row is None or not row["path"]:
        raise ReviewError(f"photo {image_id} has no {kind} preview")
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(cache_root.resolve()) or not path.is_file():
        raise ReviewError(f"photo {image_id}'s {kind} preview is gone")
    return path


# --- the owner's actions ---------------------------------------------------------------------------------------


def _camera_white_balance(conn: sqlite3.Connection, image_id: int) -> tuple[float, float] | None:
    row = conn.execute(
        "SELECT evidence FROM proposals WHERE image_id = ? AND agent LIKE 'wb@%' ORDER BY id DESC LIMIT 1", (image_id,)
    ).fetchone()
    camera = (catalog.loads(row["evidence"], {}) or {}).get("camera") if row else None
    return (float(camera["temp"]), float(camera["green"])) if camera else None


def _record(conn: sqlite3.Connection, image_id: int, key: str, old: Any, new: Any, source: Any, action: str) -> None:
    conn.execute(
        "INSERT INTO overrides (image_id, param, app_value, owner_value, source, action, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            image_id,
            key,
            None if old is None else catalog.dumps(old),
            None if new is None else catalog.dumps(new),
            source,
            action,
            catalog.now(),
        ),
    )


def change(conn: sqlite3.Connection, image_id: int, values: dict[str, dict[str, Any]], action: str) -> int:
    """Set recipe values as the owner's (locked), recording each change; returns the new recipe version."""
    with catalog.transaction(conn):
        _version, recipe = load_recipe(conn, image_id)
        params = recipe.setdefault("params", {})
        provenance = recipe.setdefault("provenance", {})
        recipe.setdefault("schema", SCHEMA)
        recipe.setdefault("engine", {"name": "rawtherapee", "version": "5.13", "base_profile": DEFAULT_BASE_PROFILE})
        for group, items in values.items():
            if group not in GROUPS:
                raise ReviewError(f"unknown parameter group {group!r}")
            target = params.setdefault(group, {})
            if group == "white_balance" and "temperature_k" in items:
                target.pop("as_shot", None)
                provenance.pop("white_balance.as_shot", None)
            for name, value in items.items():
                key = f"{group}.{name}"
                old = target.get(name)
                if old != value:
                    _record(conn, image_id, key, old, value, (provenance.get(key) or {}).get("source"), action)
                target[name] = value
                provenance[key] = {"source": SOURCE_OWNER, "locked": True, "action": action}
        recipe["params"] = {g: v for g, v in params.items() if v}
        version = save_recipe(conn, image_id, recipe, SOURCE_OWNER)
        conn.execute("UPDATE edits SET updated_at = ? WHERE image_id = ?", (catalog.now(), image_id))
    return version


def nudge(conn: sqlite3.Connection, image_id: int, kind: str, steps: int) -> int:
    """Exposure, warmth or rotation by `steps` steps (negative: darker, cooler, clockwise)."""
    if kind not in NUDGES:
        raise ReviewError(f"unknown nudge {kind!r}; known: {', '.join(NUDGES)}")
    group, name, step = NUDGES[kind]
    _version, recipe = load_recipe(conn, image_id)
    current = recipe.get("params", {}).get(group, {})
    if kind == "warmth":
        if "temperature_k" in current:
            temp, green = float(current["temperature_k"]), float(current["tint"])
        else:
            camera = _camera_white_balance(conn, image_id)
            if camera is None:
                raise ReviewError("no white balance to start from; edit the folder first")
            temp, green = camera
        mired = max(wbmodel.mired(temp) - step * steps, 15.0)
        values = {"white_balance": {"temperature_k": round(wbmodel.from_mired(mired)), "tint": round(green, 4)}}
    else:
        value = round(float(current.get(name, 0.0)) + step * steps, 3)
        values = {group: {name: value}}
    return change(conn, image_id, values, "nudge")


def reset(conn: sqlite3.Connection, image_id: int, group: str) -> int:
    """Back to as shot for one group (white balance, exposure, geometry, colour), locked so the agents leave it."""
    as_shot: dict[str, dict[str, Any]] = {
        "white_balance": {"as_shot": True},
        "exposure": {"ev": 0.0, "highlight_compression": 0, "shadows": 0},
        "geometry": {"rotate_deg": 0.0},
        "color": {"vibrance": 0, "saturation": 0},
    }
    if group not in as_shot:
        raise ReviewError(f"cannot reset {group!r}; one of {', '.join(as_shot)}")
    with catalog.transaction(conn):
        _version, recipe = load_recipe(conn, image_id)
        params, provenance = recipe.setdefault("params", {}), recipe.setdefault("provenance", {})
        for key in [k for k in provenance if k.startswith(f"{group}.")]:
            name = key.split(".", 1)[1]
            _record(conn, image_id, key, params.get(group, {}).get(name), None, provenance[key].get("source"), "reset")
            provenance.pop(key)
        params[group] = dict(as_shot[group])
        for name in params[group]:
            provenance[f"{group}.{name}"] = {"source": SOURCE_OWNER, "locked": True, "action": "reset"}
        recipe["params"] = {g: v for g, v in params.items() if v}
        version = save_recipe(conn, image_id, recipe, SOURCE_OWNER)
        conn.execute("UPDATE edits SET updated_at = ? WHERE image_id = ?", (catalog.now(), image_id))
    return version


def set_lock(conn: sqlite3.Connection, image_id: int, key: str, locked: bool) -> int:
    """Lock a value so the agents keep it, or unlock it so the next edit run may change it."""
    with catalog.transaction(conn):
        _version, recipe = load_recipe(conn, image_id)
        group, _, name = key.partition(".")
        if name not in recipe.get("params", {}).get(group, {}):
            raise ReviewError(f"{key} is not set for photo {image_id}")
        prov = dict(recipe.setdefault("provenance", {}).get(key) or {})
        if bool(prov.get("locked")) == locked:
            return int(_version)
        prov["locked"] = locked
        recipe["provenance"][key] = prov
        _record(conn, image_id, key, None, None, prov.get("source"), "lock" if locked else "unlock")
        version = save_recipe(conn, image_id, recipe, SOURCE_OWNER)
        conn.execute("UPDATE edits SET updated_at = ? WHERE image_id = ?", (catalog.now(), image_id))
    return version


def scene_of(conn: sqlite3.Connection, image_id: int) -> list[int]:
    """The edited photos of the photo's scene, itself included."""
    row = conn.execute(
        "SELECT g.parent_id FROM groups g JOIN group_members m ON m.group_id = g.id "
        "WHERE g.kind = 'burst' AND m.image_id = ?",
        (image_id,),
    ).fetchone()
    if row is None or row["parent_id"] is None:
        return [image_id]
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT m.image_id FROM groups g JOIN group_members m ON m.group_id = g.id "
            "JOIN edits e ON e.image_id = m.image_id WHERE g.parent_id = ? AND g.kind = 'burst'",
            (row["parent_id"],),
        )
    ]


def apply_to_scene(conn: sqlite3.Connection, image_id: int, groups: tuple[str, ...]) -> list[int]:
    """Give the photo's values of these groups to every edited photo of its scene; returns the photos changed."""
    _version, recipe = load_recipe(conn, image_id)
    values = {g: dict(recipe.get("params", {}).get(g, {})) for g in groups}
    changed = []
    for other in scene_of(conn, image_id):
        if other == image_id:
            continue
        _v, theirs = load_recipe(conn, other)
        if all(theirs.get("params", {}).get(g, {}) == values[g] for g in groups):
            continue
        for group in groups:
            if not values[group]:
                reset(conn, other, group)
        change(conn, other, {g: v for g, v in values.items() if v}, "scene")
        changed.append(other)
    return changed


def mark_reviewed(conn: sqlite3.Connection, image_id: int, reviewed: bool = True) -> None:
    conn.execute("UPDATE edits SET reviewed_at = ? WHERE image_id = ?", (catalog.now() if reviewed else None, image_id))


def rerender(conn: sqlite3.Connection, cfg: Config, image_id: int) -> Path:
    """Render the photo's current recipe as its edited preview."""
    row = conn.execute("SELECT folder_id, name, quick_hash FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise ReviewError(f"no photo {image_id}")
    _version, recipe = load_recipe(conn, image_id)
    raw = folder_path(conn, row["folder_id"], cfg) / row["name"]
    result = cached_render(
        raw, parse_recipe(recipe), cfg, image_key=row["quick_hash"], size=cfg.render.preview_size, preview=True
    )
    conn.execute(
        "UPDATE edits SET after = ?, updated_at = ? WHERE image_id = ?", (str(result.output), catalog.now(), image_id)
    )
    return result.output


def overrides_count(conn: sqlite3.Connection, folder_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(DISTINCT o.image_id) FROM overrides o JOIN images i ON i.id = o.image_id "
            "WHERE i.folder_id = ?",
            (folder_id,),
        ).fetchone()[0]
    )


def no_override_share(conn: sqlite3.Connection, folder_id: int) -> float | None:
    """The plan's Phase 2 exit metric: the share of edited photos the owner did not override (plan §10)."""
    edited = conn.execute(
        "SELECT COUNT(*) FROM edits e JOIN images i ON i.id = e.image_id WHERE i.folder_id = ? AND i.state = 'present'",
        (folder_id,),
    ).fetchone()[0]
    if not edited:
        return None
    return round(1 - overrides_count(conn, folder_id) / edited, 3)
