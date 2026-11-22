"""FastAPI routes for the culling UI (plan §6), served by `rie serve` on localhost.

Previews come from the cache and work while the drive is away; anything that needs the photo files (100% zoom,
face crops, Done culling, restore) reports the drive's state instead of waiting on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import Scope

from .. import catalog, locations, safety
from ..analysis.faces import models_available
from ..config import REPO_ROOT, Config
from ..cull import actions, piles
from ..cull import model as model_mod
from ..scan import previews
from .jobs import JobRunner

WEB_DIST = REPO_ROOT / "web" / "dist"
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
# The page, and any other file that keeps its name from one build to the next: the browser checks with the server on
# each load (an unchanged file costs a 304) instead of guessing how long its copy stays good, so a new build shows at
# the next reload.
REVALIDATE = {"Cache-Control": "no-cache"}


class WebFiles(StaticFiles):
    """The built web UI. Vite names the files in assets/ after their content, so those are kept for good."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(IMMUTABLE if Path(path).parts[:1] == ("assets",) else REVALIDATE)
        return response


class OpenRequest(BaseModel):
    path: str


class DecisionRequest(BaseModel):
    decision: str | None
    reason: str | None = None


class BurstDecisionRequest(BaseModel):
    decision: str | None


class ReasonRequest(BaseModel):
    reason: str


class TrustRequest(BaseModel):
    kind: str
    mode: str


class FullCache:
    """The last few full-size JPEGs, so zooming back and forth does not read the drive again."""

    def __init__(self, size: int = 8) -> None:
        self.size = size
        self.items: OrderedDict[int, bytes] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: int) -> bytes | None:
        with self.lock:
            data = self.items.get(key)
            if data is not None:
                self.items.move_to_end(key)
            return data

    def put(self, key: int, data: bytes) -> None:
        with self.lock:
            self.items[key] = data
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


def create_app(cfg: Config, *, start_jobs: bool = True) -> FastAPI:
    app = FastAPI(title="Raw Image Editor", docs_url="/api/docs", openapi_url="/api/openapi.json")
    catalog.connect(cfg.catalog_path).close()  # create or migrate before the first request
    runner = JobRunner(cfg) if start_jobs else None
    full_cache = FullCache()
    space_cache: dict[str, Any] = {"at": 0.0, "value": None}
    findings = safety.trash_autoempty_findings()

    def db() -> Iterator[sqlite3.Connection]:
        conn = catalog.connect(cfg.catalog_path, migrate=False, any_thread=True)
        try:
            yield conn
        finally:
            conn.close()

    def photo_row(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT i.*, f.drive_uuid, f.path AS folder_path FROM images i JOIN folders f ON f.id = i.folder_id "
            "WHERE i.id = ?",
            (image_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"no photo {image_id}")
        return row

    def photo_path(row: sqlite3.Row) -> Path:
        location = locations.Location(row["drive_uuid"], row["folder_path"])
        if not locations.available(location, cfg):
            raise HTTPException(503, "the photo drive is not connected")
        return locations.resolve(location, cfg) / row["name"]

    # -- status and folders ---------------------------------------------------------------------------------------

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        state = locations.status(cfg)
        space = None
        if state.connected and state.mountpoint is not None:
            if time.time() - space_cache["at"] > 60:
                report = safety.space_report(state.mountpoint, cfg.safety.min_free_gb)
                space_cache.update(at=time.time(), value=report)
            report = space_cache["value"]
            space = {
                "trash_files": report.trash_files,
                "trash_bytes": report.trash_bytes,
                "free_bytes": report.free_bytes,
                "total_bytes": report.total_bytes,
                "low": report.low,
                "text": report.describe(),
            }
        return {
            "drive": {"state": str(state.state), "detail": state.detail, "configured": bool(cfg.drive.uuid)},
            "space": space,
            "trash_autoempty": findings,
            "face_models": models_available(),
            "photos_root": str(cfg.paths.photos) if cfg.paths.photos else None,
        }

    @app.get("/api/folders")
    def folders(conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM images i WHERE i.folder_id = f.id AND i.state = 'present') AS photos, "
            "(SELECT COUNT(*) FROM images i WHERE i.folder_id = f.id AND i.state = 'trashed') AS trashed, "
            "(SELECT COUNT(*) FROM trash_entries t WHERE t.folder_id = f.id AND t.restored_at IS NULL) AS in_trash "
            "FROM folders f ORDER BY f.opened_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            location = locations.Location(r["drive_uuid"], r["path"])
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "path": r["path"],
                    "on_drive": bool(r["drive_uuid"]),
                    "available": locations.available(location, cfg),
                    "photos": r["photos"],
                    "trashed": r["trashed"],
                    "in_trash": r["in_trash"],
                    "opened_at": r["opened_at"],
                    "analyzed_at": r["analyzed_at"],
                    "culled_at": r["culled_at"],
                }
            )
        return out

    @app.post("/api/folders/open")
    def open_folder(request: OpenRequest) -> dict[str, Any]:
        if runner is None:
            raise HTTPException(503, "jobs are not running")
        if not request.path.strip():
            raise HTTPException(400, "choose a folder: click one in the list or type its path")
        path = Path(request.path.strip()).expanduser()
        try:
            folder = locations.resolve(locations.locate(path, cfg), cfg)
        except locations.DriveUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if not folder.is_dir():
            raise HTTPException(404, f"not a folder: {folder}")
        return runner.submit_open(path).to_json()

    @app.get("/api/browse")
    def browse(path: str | None = None, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        """The subfolders of a folder (by default the photos folder), newest name first, to pick one to open."""
        start = Path(path).expanduser() if path else (cfg.paths.photos or Path.home())
        try:
            location = locations.locate(start, cfg)
            folder = locations.resolve(location, cfg)
        except locations.DriveUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if not folder.is_dir():
            raise HTTPException(404, f"not a folder: {folder}")
        opened = {
            (r["drive_uuid"], r["path"]): r["id"] for r in conn.execute("SELECT id, drive_uuid, path FROM folders")
        }
        entries = []
        try:
            with os.scandir(folder) as listing:
                for entry in listing:
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    child = locations.Location(
                        location.drive_uuid,
                        os.path.join(location.path, entry.name) if location.on_drive else str(folder / entry.name),
                    )
                    entries.append(
                        {
                            "name": entry.name,
                            "path": str(folder / entry.name),
                            "folder_id": opened.get((child.drive_uuid, os.path.normpath(child.path))),
                        }
                    )
        except PermissionError as exc:
            raise HTTPException(403, f"cannot list {folder}") from exc
        entries.sort(key=lambda e: e["name"].lower(), reverse=True)
        parent = folder.parent if folder.parent != folder else None
        return {"path": str(folder), "parent": str(parent) if parent else None, "folders": entries}

    @app.get("/api/folders/{folder_id}")
    def folder(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no folder {folder_id}")
        counts = dict(
            conn.execute(
                "SELECT s.pile, COUNT(*) FROM suggestions s JOIN images i ON i.id = s.image_id "
                "WHERE i.folder_id = ? AND i.state = 'present' GROUP BY s.pile",
                (folder_id,),
            ).fetchall()
        )
        decided = conn.execute(
            "SELECT COUNT(*) FROM decisions d JOIN images i ON i.id = d.image_id "
            "WHERE i.folder_id = ? AND i.state = 'present' AND d.decision IS NOT NULL AND d.decided_by = 'owner'",
            (folder_id,),
        ).fetchone()[0]
        metrics = None
        if row["culled_at"]:
            m = actions.metrics(conn, folder_id)
            metrics = {
                "photos": m.photos,
                "automatic": m.automatic,
                "changed": m.changed,
                "share_automatic": m.share_automatic,
                "change_rate": m.change_rate,
                "minutes": m.minutes,
                "agreement": {k: {"agreed": a, "total": t} for k, (a, t) in m.agreement.items()},
            }
        model = conn.execute("SELECT id, agreement, baseline, n_train FROM cull_models WHERE active = 1").fetchone()
        location = locations.Location(row["drive_uuid"], row["path"])
        return {
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "available": locations.available(location, cfg),
            "culled_at": row["culled_at"],
            "piles": {p: counts.get(p, 0) for p in ("your_call", "auto_reject", "auto_keep")},
            "decided": decided,
            "metrics": metrics,
            "trust": piles.trust_modes(conn),
            "proposals": actions.trust_proposals(conn),
            "model": dict(model) if model else None,
        }

    @app.get("/api/folders/{folder_id}/photos")
    def photos(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT i.id, i.name, i.jpeg_name, i.capture_time, i.state, i.width, i.height, i.quick_hash, i.meta, "
            "s.pile, s.decision AS suggestion, s.kind, s.reason, s.probability, d.decision, d.reason "
            "AS owner_reason, fo.result AS focus, bu.result AS burst, lo.result AS look, sc.unmodeled, "
            "(SELECT m.group_id FROM group_members m JOIN groups g ON g.id = m.group_id "
            " WHERE g.kind = 'scene' AND m.image_id = i.id) AS scene_id, "
            "(SELECT COUNT(*) FROM faces fa WHERE fa.image_id = i.id) AS faces, "
            "(SELECT 1 FROM recipes r WHERE r.image_id = i.id) AS has_recipe "
            "FROM images i LEFT JOIN suggestions s ON s.image_id = i.id LEFT JOIN decisions d ON d.image_id = i.id "
            "LEFT JOIN analyses fo ON fo.image_id = i.id AND fo.kind = 'focus' "
            "LEFT JOIN analyses bu ON bu.image_id = i.id AND bu.kind = 'burst' "
            "LEFT JOIN analyses lo ON lo.image_id = i.id AND lo.kind = 'look' "
            "LEFT JOIN sidecars sc ON sc.folder_id = i.folder_id AND sc.name = i.name || '.pp3' "
            "WHERE i.folder_id = ? AND i.state != 'missing' ORDER BY i.capture_time, i.shutter_count, i.name",
            (folder_id,),
        ).fetchall()
        out = []
        for r in rows:
            meta = catalog.loads(r["meta"], {})
            focus = catalog.loads(r["focus"], {}) or {}
            burst = catalog.loads(r["burst"], {}) or {}
            look = catalog.loads(r["look"], {}) or {}
            exposure = meta.get("exposure_time")
            settings = " ".join(
                part
                for part in (
                    (f"1/{round(1 / exposure)}" if exposure and exposure < 1 else f"{exposure}s") if exposure else "",
                    f"f/{meta['fnumber']:g}" if meta.get("fnumber") else "",
                    f"ISO {meta['iso']:g}" if meta.get("iso") else "",
                    f"{meta['focal_length']:g} mm" if meta.get("focal_length") else "",
                    "flash" if meta.get("flash_fired") else "",
                )
                if part
            )
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "jpeg": r["jpeg_name"],
                    "time": r["capture_time"],
                    "state": r["state"],
                    "w": r["width"],
                    "h": r["height"],
                    "v": (r["quick_hash"] or "").split(":")[-1][:12],
                    "pile": r["pile"],
                    "suggestion": r["suggestion"],
                    "kind": r["kind"],
                    "reason": r["reason"],
                    "p": r["probability"],
                    "decision": r["decision"],
                    "owner_reason": r["owner_reason"],
                    "scene": r["scene_id"],
                    "burst": {
                        "id": burst.get("burst_id"),
                        "size": burst.get("size", 1),
                        "rank": burst.get("rank", 1),
                        "position": burst.get("position", 1),
                        "best": burst.get("best"),
                        "issues": burst.get("issues", []),
                    },
                    "focus": {
                        "verdict": focus.get("verdict"),
                        "blur": focus.get("subject_blur"),
                        "subject": focus.get("subject"),
                        "reason": focus.get("reason"),
                    },
                    "faces": r["faces"],
                    "closed": burst.get("closed_eyes", 0),
                    "away": burst.get("looking_away", 0),
                    "smile": burst.get("smile"),
                    "clip": look.get("clip_high"),
                    "lens": meta.get("lens"),
                    "settings": settings,
                    "unmodeled": len(catalog.loads(r["unmodeled"], []) or []),
                    "edits": bool(r["has_recipe"]),
                }
            )
        return out

    # -- images -----------------------------------------------------------------------------------------------------

    def cached(conn: sqlite3.Connection, image_id: int, kind: str) -> Response:
        row = photo_row(conn, image_id)
        path = (previews.thumb_path if kind == "thumb" else previews.screen_path)(cfg, row["quick_hash"])
        if not path.exists():
            source = photo_path(row)
            result = previews.make_previews(
                row["id"],
                source,
                row["kind"],
                catalog.loads(row["previews"], {}),
                row["orientation"],
                row["quick_hash"],
                cfg,
            )
            if result.error:
                raise HTTPException(500, result.error)
        return FileResponse(path, media_type="image/jpeg", headers=IMMUTABLE)

    @app.get("/api/photos/{image_id}/thumb")
    def thumb(image_id: int, conn: sqlite3.Connection = Depends(db)) -> Response:
        return cached(conn, image_id, "thumb")

    @app.get("/api/photos/{image_id}/screen")
    def screen(image_id: int, conn: sqlite3.Connection = Depends(db)) -> Response:
        return cached(conn, image_id, "screen")

    @app.get("/api/photos/{image_id}/full")
    def full(image_id: int, conn: sqlite3.Connection = Depends(db)) -> Response:
        data = full_cache.get(image_id)
        if data is None:
            row = photo_row(conn, image_id)
            try:
                data = previews.full_size(
                    photo_path(row), row["kind"], catalog.loads(row["previews"], {}), row["orientation"]
                )
            except previews.PreviewError as exc:
                raise HTTPException(500, str(exc)) from exc
            full_cache.put(image_id, data)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/photos/{image_id}/face/{idx}")
    def face(image_id: int, idx: int, conn: sqlite3.Connection = Depends(db)) -> Response:
        row = photo_row(conn, image_id)
        face_row = conn.execute("SELECT box FROM faces WHERE image_id = ? AND idx = ?", (image_id, idx)).fetchone()
        if face_row is None:
            raise HTTPException(404, "no such face")
        digest = row["quick_hash"].split(":")[-1]
        path = cfg.paths.cache / "faces" / digest[:2] / digest / f"{idx}.jpg"
        if not path.exists():
            data = full_cache.get(image_id)
            if data is None:
                data = previews.full_size(
                    photo_path(row), row["kind"], catalog.loads(row["previews"], {}), row["orientation"]
                )
                full_cache.put(image_id, data)
            # libvips loads the stored pixels and ignores the orientation tag, so the crop is turned here.
            crop = previews.face_crop(data, row["orientation"], catalog.loads(face_row["box"]))
            previews.write_cached(path, crop, cfg.paths.cache)
        return FileResponse(path, media_type="image/jpeg", headers=IMMUTABLE)

    @app.get("/api/photos/{image_id}/analysis")
    def analysis(image_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        photo_row(conn, image_id)
        results = {
            r["kind"]: catalog.loads(r["result"], {})
            for r in conn.execute("SELECT kind, result FROM analyses WHERE image_id = ?", (image_id,))
        }
        sharp = results.get("sharpness")
        heat = None
        if sharp:
            from ..analysis.sharpness import decode_map

            values = decode_map(sharp["blur"], sharp["rows"], sharp["cols"])
            heat = {
                "rows": sharp["rows"],
                "cols": sharp["cols"],
                "blur": [None if v != v else round(float(v), 2) for v in values.flatten()],
            }
        faces = [
            {
                "idx": r["idx"],
                "box": catalog.loads(r["box"]),
                "eyes": (catalog.loads(r["landmarks"], {}) or {}).get("eyes", []),
                "blink": None if r["blink_l"] is None else max(r["blink_l"], r["blink_r"]),
                "smile": r["smile"],
                "yaw": r["yaw"],
                "sharpness": r["sharpness"],
                "cut_off": bool(r["cut_off"]),
                "track": r["track"],
            }
            for r in conn.execute("SELECT * FROM faces WHERE image_id = ? ORDER BY idx", (image_id,))
        ]
        focus = results.get("focus", {})
        recipe = conn.execute("SELECT recipe FROM recipes WHERE image_id = ?", (image_id,)).fetchone()
        sidecar = conn.execute(
            "SELECT s.unmodeled, s.rank, s.in_trash FROM sidecars s JOIN images i ON i.folder_id = s.folder_id "
            "AND s.name = i.name || '.pp3' WHERE i.id = ?",
            (image_id,),
        ).fetchone()
        return {
            "heat": heat,
            "faces": faces,
            "focus": focus,
            "burst": results.get("burst", {}),
            "look": results.get("look", {}),
            "recipe": catalog.loads(recipe["recipe"]) if recipe else None,
            "unmodeled": catalog.loads(sidecar["unmodeled"], []) if sidecar else [],
        }

    # -- decisions ----------------------------------------------------------------------------------------------------

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(409 if isinstance(exc, actions.CullError) else 400, str(exc))

    @app.post("/api/photos/{image_id}/decision")
    def decision(image_id: int, request: DecisionRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            actions.set_decision(conn, image_id, request.decision, request.reason)
        except actions.CullError as exc:
            raise fail(exc) from exc
        return {"ok": True}

    @app.post("/api/photos/{image_id}/reason")
    def reason(image_id: int, request: ReasonRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            actions.set_reason(conn, image_id, request.reason)
        except actions.CullError as exc:
            raise fail(exc) from exc
        return {"ok": True}

    @app.post("/api/photos/{image_id}/pick")
    def pick(image_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            changed = actions.pick(conn, image_id)
        except actions.CullError as exc:
            raise fail(exc) from exc
        return {"ok": True, "changed": changed}

    @app.post("/api/photos/{image_id}/burst-decision")
    def burst_decision(
        image_id: int, request: BurstDecisionRequest, conn: sqlite3.Connection = Depends(db)
    ) -> dict[str, Any]:
        try:
            changed = actions.decide_burst(conn, image_id, request.decision)
        except actions.CullError as exc:
            raise fail(exc) from exc
        return {"ok": True, "changed": changed}

    @app.get("/api/folders/{folder_id}/done")
    def done_preview(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            preview = actions.done_preview(conn, cfg, folder_id)
        except actions.CullError as exc:
            raise fail(exc) from exc
        return {**preview.__dict__, "size": safety.format_bytes(preview.bytes)}

    @app.post("/api/folders/{folder_id}/done")
    def done(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            result = actions.done_culling(conn, cfg, folder_id)
        except actions.CullError as exc:
            raise fail(exc) from exc
        space_cache["at"] = 0.0
        training = result.training
        return {
            "trashed": result.trashed,
            "files": result.files,
            "size": safety.format_bytes(result.bytes),
            "kept": result.kept,
            "errors": result.errors,
            "model": None if training is None else {"promoted": training.promoted, "note": training.note},
        }

    @app.post("/api/folders/{folder_id}/restore")
    def restore_folder(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            count = actions.restore(conn, cfg, folder_id=folder_id)
        except actions.CullError as exc:
            raise fail(exc) from exc
        space_cache["at"] = 0.0
        return {"restored": count}

    @app.post("/api/photos/{image_id}/restore")
    def restore_photo(image_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            count = actions.restore(conn, cfg, image_id=image_id)
        except actions.CullError as exc:
            raise fail(exc) from exc
        space_cache["at"] = 0.0
        return {"restored": count}

    @app.get("/api/trust")
    def trust(conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        models = [
            dict(r)
            for r in conn.execute(
                "SELECT id, created_at, n_train, agreement, baseline, active FROM cull_models ORDER BY id"
            )
        ]
        return {"modes": piles.trust_modes(conn), "proposals": actions.trust_proposals(conn), "models": models}

    @app.post("/api/trust")
    def set_trust(request: TrustRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            piles.set_trust(conn, request.kind, request.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        for (folder_id,) in conn.execute("SELECT id FROM folders WHERE culled_at IS NULL").fetchall():
            piles.sort_folder(conn, folder_id)
        return {"modes": piles.trust_modes(conn)}

    @app.post("/api/models/{model_id}/activate")
    def activate(model_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            model_mod.rollback(conn, model_id)
        except model_mod.OutdatedModel as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        for (folder_id,) in conn.execute("SELECT id FROM folders WHERE culled_at IS NULL").fetchall():
            piles.sort_folder(conn, folder_id)
        return {"ok": True}

    # -- events and the web app -------------------------------------------------------------------------------------

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            seen = -1
            last = time.time()
            server = getattr(request.app.state, "server", None)
            # Ends when the page goes away or the server is stopping, so Ctrl+C does not wait on open pages.
            while not await request.is_disconnected() and not (server is not None and server.should_exit):
                if runner is not None:
                    version, jobs = runner.snapshot()
                    if version != seen:
                        seen = version
                        last = time.time()
                        yield f"data: {json.dumps({'jobs': jobs})}\n\n"
                if time.time() - last > 15:
                    last = time.time()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if (WEB_DIST / "index.html").is_file():
        app.mount("/", WebFiles(directory=WEB_DIST, html=True), name="web")
    else:

        @app.get("/")
        def no_build() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Raw Image Editor</h1><p>The web UI is not built yet: run <code>npm install</code> and "
                "<code>npm run build</code> in <code>web/</code>, then reload. The API is at /api/docs.</p>",
                headers=REVALIDATE,
            )

    return app
