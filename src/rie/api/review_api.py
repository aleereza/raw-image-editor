"""FastAPI routes for Phase 2's review page (plan §6 Review, Export, Look lab), served at /review.html.

Editing, exporting and re-rendering a whole scene run as background jobs (their progress goes out on the event
stream); one photo's nudge, reset or lock re-renders its preview before answering, so the page can swap the real
render in for its instant approximation.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import locations, looklab, managed, review
from ..agents.looks import Look
from ..config import Config
from ..edit import EditSettings, edit_folder
from ..export import export_folder
from ..scan.folder import folder_path
from .jobs import JobRunner

REVALIDATE = {"Cache-Control": "no-cache"}


class EditRequest(BaseModel):
    all: bool = False


class NudgeRequest(BaseModel):
    kind: str
    steps: int


class ResetRequest(BaseModel):
    group: str


class AcceptRequest(BaseModel):
    group: str


class LockRequest(BaseModel):
    key: str
    locked: bool


class SceneRequest(BaseModel):
    groups: list[str]


class ReviewedRequest(BaseModel):
    reviewed: bool = True


class LookLabRequest(BaseModel):
    look: dict[str, Any] | None = None
    round: int = 1


class SaveLookRequest(BaseModel):
    name: str
    look: dict[str, Any]
    description: str = ""


class UseLookRequest(BaseModel):
    look: str
    scene: bool = False


def add_review_routes(
    app: FastAPI,
    cfg: Config,
    db: Callable[[], Iterator[sqlite3.Connection]],
    runner: JobRunner | None,
) -> None:
    def folder_row(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no folder {folder_id}")
        return row

    def edit_row(conn: sqlite3.Connection, image_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT folder_id FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no photo {image_id}")
        for item in review.edits_of_folder(conn, row["folder_id"]):
            if item["id"] == image_id:
                return item
        raise HTTPException(404, f"photo {image_id} has not been edited")

    def needs_runner() -> JobRunner:
        if runner is None:
            raise HTTPException(503, "background jobs are off")
        return runner

    def rerender(conn: sqlite3.Connection, image_id: int) -> None:
        try:
            review.rerender(conn, cfg, image_id)
        except locations.DriveUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    def act(conn: sqlite3.Connection, image_id: int, action: Callable[[], Any]) -> dict[str, Any]:
        try:
            action()
        except review.ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc
        rerender(conn, image_id)
        return edit_row(conn, image_id)

    @app.get("/api/folders/{folder_id}/edits")
    def folder_edits(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        folder = folder_row(conn, folder_id)
        exported = conn.execute(
            "SELECT COUNT(*), MAX(x.exported_at) FROM exports x JOIN images i ON i.id = x.image_id "
            "WHERE i.folder_id = ? AND x.trash_entry IS NULL",
            (folder_id,),
        ).fetchone()
        location = locations.Location(folder["drive_uuid"], folder["path"])
        return {
            "folder": {"id": folder["id"], "name": folder["name"], "available": locations.available(location, cfg)},
            "edits": review.edits_of_folder(conn, folder_id),
            "looks": [look.to_json() | {"id": look.id} for look in looklab.all_looks(conn)],
            "exports": {"count": exported[0], "last": exported[1]},
            "no_override_share": review.no_override_share(conn, folder_id),
        }

    @app.post("/api/folders/{folder_id}/edit")
    def start_edit(folder_id: int, body: EditRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        folder = folder_row(conn, folder_id)
        jobs = needs_runner()

        def task(job_conn: sqlite3.Connection, progress: Callable[[str, int, int], None]) -> str:
            run = edit_folder(job_conn, cfg, folder_id, settings=EditSettings(), all_photos=body.all, progress=progress)
            review.store_run(job_conn, run)
            m = run.metrics()
            return f"edited {m['photos'] - m['failed']} photos; {m['review']} need a look"

        return jobs.submit_task("edit", folder_id, f"edit {folder['name']}", task).to_json()

    @app.post("/api/folders/{folder_id}/export")
    def start_export(folder_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        folder = folder_row(conn, folder_id)
        jobs = needs_runner()

        def task(job_conn: sqlite3.Connection, progress: Callable[[str, int, int], None]) -> str:
            result = export_folder(job_conn, cfg, folder_id, progress=progress)
            text = f"exported {result.exported}, {result.skipped} unchanged, {result.replaced} replaced"
            return text + (f"; {result.failed} failed, first: {result.errors[0]}" if result.failed else "")

        return jobs.submit_task("export", folder_id, f"export {folder['name']}", task).to_json()

    @app.get("/api/photos/{image_id}/preview/{kind}")
    def preview(image_id: int, kind: str, conn: sqlite3.Connection = Depends(db)) -> FileResponse:
        try:
            path = review.preview_path(conn, image_id, kind, cfg.paths.cache)
        except review.ReviewError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg", headers=REVALIDATE)

    @app.post("/api/photos/{image_id}/nudge")
    def nudge(image_id: int, body: NudgeRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        return act(conn, image_id, lambda: review.nudge(conn, image_id, body.kind, body.steps))

    @app.post("/api/photos/{image_id}/reset")
    def reset(image_id: int, body: ResetRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        return act(conn, image_id, lambda: review.reset(conn, image_id, body.group))

    @app.post("/api/photos/{image_id}/accept")
    def accept(image_id: int, body: AcceptRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        return act(conn, image_id, lambda: review.apply_proposal(conn, image_id, body.group))

    @app.post("/api/photos/{image_id}/lock")
    def lock(image_id: int, body: LockRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            review.set_lock(conn, image_id, body.key, body.locked)
        except review.ReviewError as exc:
            raise HTTPException(400, str(exc)) from exc
        return edit_row(conn, image_id)

    @app.post("/api/photos/{image_id}/reviewed")
    def reviewed(image_id: int, body: ReviewedRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        review.mark_reviewed(conn, image_id, body.reviewed)
        return edit_row(conn, image_id)

    @app.post("/api/photos/{image_id}/scene")
    def to_scene(image_id: int, body: SceneRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        bad = [g for g in body.groups if g not in review.GROUPS]
        if bad or not body.groups:
            raise HTTPException(400, f"groups must be some of {', '.join(review.GROUPS)}")
        changed = review.apply_to_scene(conn, image_id, tuple(body.groups))
        if changed:
            folder_id = conn.execute("SELECT folder_id FROM images WHERE id = ?", (image_id,)).fetchone()[0]

            def task(job_conn: sqlite3.Connection, progress: Callable[[str, int, int], None]) -> str:
                for done, other in enumerate(changed, start=1):
                    review.rerender(job_conn, cfg, other)
                    progress("render", done, len(changed))
                return f"{len(changed)} photos of the scene re-rendered"

            needs_runner().submit_task("rerender", folder_id, "apply to the scene", task)
        return {"changed": changed, "photo": edit_row(conn, image_id)}

    @app.post("/api/photos/{image_id}/looklab")
    def look_lab(image_id: int, body: LookLabRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            base = Look.from_json(body.look) if body.look else looklab.get_look(conn, edit_row(conn, image_id)["look"])
            items = looklab.render_variations(conn, cfg, image_id, base, max(1, body.round))
        except (review.ReviewError, KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except locations.DriveUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"round": body.round, "variations": [{k: v for k, v in i.items() if k != "path"} for i in items]}

    @app.get("/api/looklab/{key}")
    def look_lab_render(key: str) -> FileResponse:
        try:
            return FileResponse(looklab.variation_path(cfg, key), media_type="image/jpeg", headers=REVALIDATE)
        except review.ReviewError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/looks")
    def save_look(body: SaveLookRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            saved = looklab.save_look(conn, Look.from_json({**body.look, "name": "draft"}), body.name, body.description)
        except (review.ReviewError, KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return saved.to_json() | {"id": saved.id}

    @app.post("/api/photos/{image_id}/look")
    def use_look(image_id: int, body: UseLookRequest, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        try:
            look = looklab.get_look(conn, body.look)
        except (review.ReviewError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        changed = looklab.apply_look(conn, image_id, look, scene=body.scene)
        others = [i for i in changed if i != image_id]
        if image_id in changed:
            rerender(conn, image_id)
        if others:
            folder_id = conn.execute("SELECT folder_id FROM images WHERE id = ?", (image_id,)).fetchone()[0]

            def task(job_conn: sqlite3.Connection, progress: Callable[[str, int, int], None]) -> str:
                for done, other in enumerate(others, start=1):
                    review.rerender(job_conn, cfg, other)
                    progress("render", done, len(others))
                return f"{look.id} on {len(others) + 1} photos of the scene"

            needs_runner().submit_task("rerender", folder_id, f"use {look.id}", task)
        return {"changed": changed, "photo": edit_row(conn, image_id)}

    @app.post("/api/photos/{image_id}/rawtherapee")
    def open_in_rawtherapee(image_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
        """Write the managed sidecar and open the raw file in the RawTherapee GUI (plan §6 Review: `E`)."""
        row = conn.execute("SELECT folder_id, name FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no photo {image_id}")
        try:
            raw = folder_path(conn, row["folder_id"], cfg) / row["name"]
        except locations.DriveUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        gui = shutil.which("rawtherapee")
        if gui is None:
            raise HTTPException(503, "the RawTherapee GUI (rawtherapee) is not installed")
        try:
            written = managed.write_sidecar(conn, cfg, image_id)  # so RawTherapee shows the app's edit
        except (managed.SidecarError, OSError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        subprocess.Popen([gui, str(raw)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"opened": raw.name, "sidecar": written.action, "keys": written.keys}
