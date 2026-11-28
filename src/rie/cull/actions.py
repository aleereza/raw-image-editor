"""The owner's culling actions: decisions, burst picks and whole-burst decisions, Done culling, restore, and what
they teach.

Every action is written to `decision_log` with the pile and suggestion the photo had at that moment. Until Done
culling, a reject is only a flag. Done culling then moves each rejected photo's files (the raw, its paired camera
JPEG and their RawTherapee sidecars) to the drive's trash through `rie.fileops`, records where each file went,
writes one `cull_outcomes` row per photo (the training data), and retrains the personal model. Open photos (no
decision by the owner, and no automatic pile) are kept.
"""

from __future__ import annotations

import itertools
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import catalog, drive, fileops, locations
from ..config import Config
from . import KINDS
from . import model as model_mod

REASONS = ("blur", "eyes", "expression", "duplicate", "composition", "exposure")
IDLE_CAP = 120.0  # seconds: longer pauses between actions do not count as time spent culling


class CullError(RuntimeError):
    """An action that cannot be done (unknown photo, drive away, nothing to restore)."""


def _photo(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT id, folder_id, name, state FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise CullError(f"no photo {image_id}")
    return row


def _log(conn: sqlite3.Connection, image: sqlite3.Row, action: str, value: str | None, reason: str | None) -> None:
    s = conn.execute("SELECT pile, decision, kind FROM suggestions WHERE image_id = ?", (image["id"],)).fetchone()
    conn.execute(
        "INSERT INTO decision_log (folder_id, image_id, action, value, pile, suggestion, kind, reason, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            image["folder_id"],
            image["id"],
            action,
            value,
            s["pile"] if s else None,
            s["decision"] if s else None,
            s["kind"] if s else None,
            reason,
            catalog.now(),
        ),
    )


def set_decision(conn: sqlite3.Connection, image_id: int, decision: str | None, reason: str | None = None) -> None:
    """Keep, reject or unflag one photo; `reason` is the optional quick reason when overruling the app."""
    if decision not in ("keep", "reject", None):
        raise CullError(f"unknown decision {decision!r}")
    if reason is not None and reason not in REASONS:
        raise CullError(f"unknown reason {reason!r}; one of {', '.join(REASONS)}")
    image = _photo(conn, image_id)
    with catalog.transaction(conn):
        _store(conn, image_id, decision, catalog.now(), reason)
        _log(conn, image, decision or "unflag", decision, reason)


def _store(conn: sqlite3.Connection, image_id: int, decision: str | None, now: str, reason: str | None = None) -> None:
    """Record the owner's decision on one photo; an earlier quick reason stays unless a new one is given."""
    conn.execute(
        "INSERT INTO decisions (image_id, decision, decided_by, reason, updated_at) VALUES (?, ?, 'owner', ?, ?) "
        "ON CONFLICT (image_id) DO UPDATE SET decision = excluded.decision, decided_by = 'owner', "
        "reason = COALESCE(excluded.reason, decisions.reason), updated_at = excluded.updated_at",
        (image_id, decision, reason, now),
    )


def set_reason(conn: sqlite3.Connection, image_id: int, reason: str) -> None:
    """Add the optional quick reason to the latest decision."""
    if reason not in REASONS:
        raise CullError(f"unknown reason {reason!r}")
    image = _photo(conn, image_id)
    with catalog.transaction(conn):
        conn.execute("UPDATE decisions SET reason = ? WHERE image_id = ?", (reason, image_id))
        _log(conn, image, "reason", reason, reason)


def _burst_frames(conn: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    """The present frames of the photo's burst in shooting order, or the photo alone when it is not in a burst,
    each with the owner's decision and the app's pile."""
    columns = "SELECT i.id AS image_id, d.decision, d.decided_by, s.pile FROM "
    joins = "LEFT JOIN decisions d ON d.image_id = i.id LEFT JOIN suggestions s ON s.image_id = i.id "
    burst = conn.execute(
        "SELECT g.id FROM groups g JOIN group_members m ON m.group_id = g.id WHERE g.kind = 'burst' AND m.image_id = ?",
        (image_id,),
    ).fetchone()
    if burst is None:
        return conn.execute(
            columns + "images i " + joins + "WHERE i.id = ? AND i.state = 'present'", (image_id,)
        ).fetchall()
    return conn.execute(
        columns
        + "group_members m JOIN images i ON i.id = m.image_id "
        + joins
        + "WHERE m.group_id = ? AND i.state = 'present' ORDER BY m.position, i.id",
        (burst["id"],),
    ).fetchall()


def _owner(frame: sqlite3.Row) -> str | None:
    """The owner's decision on a frame, if any."""
    return frame["decision"] if frame["decided_by"] == "owner" else None


def _rejected_by_app(frame: sqlite3.Row) -> bool:
    """The app rejects this frame on its own: the owner hasn't decided it, and it is in the automatic reject pile."""
    return _owner(frame) is None and frame["pile"] == "auto_reject"


def pick(conn: sqlite3.Connection, image_id: int) -> list[int]:
    """Pick a frame of its burst: it is kept, and the burst's open frames become the owner's rejects.

    The frames the owner kept or rejected stay as they are, so picking another frame adds a keep, and moving the keep
    takes a reject on the old one (ADR 0011). A frame the app already rejects on its own keeps the app's call: its
    final call is a reject anyway, and the share decided automatically stays right. Returns the picked frame's id,
    then the ids of the other frames whose decision changed.
    """
    image = _photo(conn, image_id)
    others = [
        f["image_id"]
        for f in _burst_frames(conn, image_id)
        if f["image_id"] != image_id and _owner(f) is None and not _rejected_by_app(f)
    ]
    now = catalog.now()
    with catalog.transaction(conn):
        _store(conn, image_id, "keep", now)
        for other in others:
            _store(conn, other, "reject", now)
        _log(conn, image, "pick", "reject_open", None)
    return [image_id, *others]


def decide_burst(conn: sqlite3.Connection, image_id: int, decision: str | None) -> list[int]:
    """Reject every present frame of the photo's burst, or clear the owner's decision on each of them (ADR 0011).

    As with a pick, a frame the app already rejects on its own keeps the app's call. A photo that is not in a burst
    is treated as a burst of one. Each frame that changes is logged on its own (action "burst"); returns their ids.
    """
    if decision not in ("reject", None):
        raise CullError(f"a whole burst can only be rejected or cleared, not {decision!r}")
    _photo(conn, image_id)
    frames = _burst_frames(conn, image_id)
    if decision == "reject":
        changed = [f["image_id"] for f in frames if _owner(f) != "reject" and not _rejected_by_app(f)]
    else:
        changed = [f["image_id"] for f in frames if _owner(f) is not None]
    now = catalog.now()
    with catalog.transaction(conn):
        for frame in changed:
            _store(conn, frame, decision, now)
            _log(conn, _photo(conn, frame), "burst", decision or "unflag", None)
    return changed


# --- Done culling --------------------------------------------------------------------------------------------------


@dataclass
class FinalCall:
    image_id: int
    name: str
    final: str
    decided_by: str  # owner, accepted, default
    pile: str | None
    suggestion: str | None
    kind: str | None
    probability: float | None
    model: str | None
    features: str
    burst_id: int | None


def final_calls(conn: sqlite3.Connection, folder_id: int) -> list[FinalCall]:
    rows = conn.execute(
        "SELECT i.id, i.name, d.decision, d.decided_by, s.pile, s.decision AS suggestion, s.kind, s.probability, "
        "s.model, s.features, (SELECT g.id FROM groups g JOIN group_members m ON m.group_id = g.id "
        "WHERE g.kind = 'burst' AND m.image_id = i.id) AS burst_id "
        "FROM images i LEFT JOIN decisions d ON d.image_id = i.id LEFT JOIN suggestions s ON s.image_id = i.id "
        "WHERE i.folder_id = ? AND i.state = 'present' ORDER BY i.capture_time, i.name",
        (folder_id,),
    ).fetchall()
    calls = []
    for r in rows:
        if r["decision"] in ("keep", "reject") and r["decided_by"] == "owner":
            final, by = r["decision"], "owner"
        elif r["pile"] == "auto_reject":
            final, by = "reject", "accepted"
        elif r["pile"] == "auto_keep":
            final, by = "keep", "accepted"
        else:
            final, by = "keep", "default"  # undecided photos are kept: nothing goes to the trash unasked
        calls.append(
            FinalCall(
                r["id"],
                r["name"],
                final,
                by,
                r["pile"],
                r["suggestion"],
                r["kind"],
                r["probability"],
                r["model"],
                r["features"] or "{}",
                r["burst_id"],
            )
        )
    return calls


def _files_of(folder: Path, image: sqlite3.Row) -> list[Path]:
    """The photo's files that exist: the raw (or JPEG), its paired JPEG, and their RawTherapee sidecars."""
    names = [image["name"], f"{image['name']}.pp3"]
    if image["jpeg_name"]:
        names += [image["jpeg_name"], f"{image['jpeg_name']}.pp3"]
    return [folder / n for n in names if (folder / n).is_file()]


@dataclass
class DonePreview:
    rejects: int
    bytes: int
    keeps: int
    undecided: int  # open photos: no decision by the owner and no automatic pile; they are kept
    auto: int  # photos decided by an automatic pile without a change by the owner
    files: int
    open_bursts: int  # bursts with open frames and no keep yet; their open frames are kept


def _open_bursts(calls: list[FinalCall]) -> int:
    """Bursts of two or more present frames where some frame is open and none is kept by the owner or the app."""
    bursts: dict[int, list[FinalCall]] = {}
    for call in calls:
        if call.burst_id is not None:
            bursts.setdefault(call.burst_id, []).append(call)
    return sum(
        len(frames) > 1
        and any(c.decided_by == "default" for c in frames)
        and not any(c.final == "keep" and c.decided_by != "default" for c in frames)
        for frames in bursts.values()
    )


def done_preview(conn: sqlite3.Connection, cfg: Config, folder_id: int) -> DonePreview:
    """What Done culling would do: how many rejects and how much space they take, and how many open photos and
    bursts without a keep stay."""
    calls = final_calls(conn, folder_id)
    folder = _folder_path(conn, cfg, folder_id)
    size = files = 0
    for call in calls:
        if call.final == "reject":
            image = conn.execute("SELECT name, jpeg_name FROM images WHERE id = ?", (call.image_id,)).fetchone()
            for path in _files_of(folder, image):
                size += path.stat().st_size
                files += 1
    return DonePreview(
        rejects=sum(c.final == "reject" for c in calls),
        bytes=size,
        keeps=sum(c.final == "keep" for c in calls),
        undecided=sum(c.decided_by == "default" for c in calls),
        auto=sum(c.decided_by == "accepted" for c in calls),
        files=files,
        open_bursts=_open_bursts(calls),
    )


def _folder_path(conn: sqlite3.Connection, cfg: Config, folder_id: int) -> Path:
    row = conn.execute("SELECT drive_uuid, path FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise CullError(f"no folder {folder_id}")
    location = locations.Location(row["drive_uuid"], row["path"])
    if not locations.available(location, cfg):
        raise CullError("the photo drive is not connected; Done culling waits for it")
    return locations.resolve(location, cfg)


def topdir_for(conn: sqlite3.Connection, cfg: Config, folder_id: int, folder: Path) -> Path | None:
    """Where the trash lives: the photo drive's mount point, or (elsewhere) the trash the specification picks."""
    row = conn.execute("SELECT drive_uuid FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row["drive_uuid"]:
        status = drive.drive_status(cfg.drive.uuid, cfg.drive.mountpoint)
        if not status.connected or status.mountpoint is None:
            raise CullError("the photo drive is not connected")
        return Path(status.mountpoint)
    return None


@dataclass
class DoneResult:
    trashed: int = 0
    files: int = 0
    bytes: int = 0
    kept: int = 0
    errors: list[str] = field(default_factory=list)
    training: model_mod.TrainResult | None = None


def done_culling(conn: sqlite3.Connection, cfg: Config, folder_id: int, *, train: bool = True) -> DoneResult:
    """Move the folder's rejects to the drive's trash, record every final call, and retrain the personal model."""
    folder = _folder_path(conn, cfg, folder_id)
    topdir = topdir_for(conn, cfg, folder_id, folder)
    drive_uuid = conn.execute("SELECT drive_uuid FROM folders WHERE id = ?", (folder_id,)).fetchone()[0]
    calls = final_calls(conn, folder_id)
    result = DoneResult()
    now = catalog.now()
    for call in calls:
        if call.final != "reject":
            result.kept += 1
            continue
        image = conn.execute("SELECT id, name, jpeg_name FROM images WHERE id = ?", (call.image_id,)).fetchone()
        moved = 0
        for path in _files_of(folder, image):
            try:
                trashed = fileops.trash(path, topdir=topdir)
            except (OSError, fileops.FileOpsError) as exc:
                result.errors.append(f"{path.name}: {exc}")
                continue
            moved += 1
            result.files += 1
            result.bytes += trashed.size
            conn.execute(
                "INSERT INTO trash_entries (folder_id, image_id, drive_uuid, topdir, original, trashed, info, size, "
                "trashed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    folder_id,
                    call.image_id,
                    drive_uuid,
                    str(trashed.topdir),
                    os.path.relpath(trashed.original, trashed.topdir),
                    os.path.relpath(trashed.trashed, trashed.topdir),
                    os.path.relpath(trashed.info, trashed.topdir),
                    trashed.size,
                    now,
                ),
            )
        if moved:
            conn.execute("UPDATE images SET state = 'trashed' WHERE id = ?", (call.image_id,))
            result.trashed += 1
    with catalog.transaction(conn):
        conn.execute("DELETE FROM cull_outcomes WHERE folder_id = ?", (folder_id,))
        picked = {
            r["image_id"]
            for r in conn.execute(
                "SELECT image_id FROM decision_log WHERE folder_id = ? AND action = 'pick'", (folder_id,)
            )
        }
        for call in calls:
            conn.execute(
                "INSERT INTO cull_outcomes (folder_id, image_id, final, decided_by, pile, suggestion, kind, "
                "probability, model, features, burst_id, picked, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    folder_id,
                    call.image_id,
                    call.final,
                    call.decided_by,
                    call.pile,
                    call.suggestion,
                    call.kind,
                    call.probability,
                    call.model,
                    call.features,
                    call.burst_id,
                    int(call.image_id in picked and call.final == "keep"),  # it may have been rejected after the pick
                    now,
                ),
            )
        conn.execute("UPDATE folders SET culled_at = ? WHERE id = ?", (now, folder_id))
        conn.execute(
            "INSERT INTO decision_log (folder_id, action, value, at) VALUES (?, 'done', ?, ?)",
            (folder_id, f"{result.trashed} rejects", now),
        )
    catalog.backup(conn, cfg.paths.data)
    if train:
        result.training = model_mod.train(conn, cfg)
    return result


# --- restore -------------------------------------------------------------------------------------------------------


def _rebase(conn: sqlite3.Connection, cfg: Config, entry: sqlite3.Row) -> Path:
    """The trash entry's volume top directory now (the drive may be mounted elsewhere)."""
    if entry["drive_uuid"]:
        status = drive.drive_status(cfg.drive.uuid, cfg.drive.mountpoint)
        if entry["drive_uuid"] != cfg.drive.uuid or not status.connected or status.mountpoint is None:
            raise CullError("the photo drive is not connected")
        return Path(status.mountpoint)
    return Path(entry["topdir"])


def restore(conn: sqlite3.Connection, cfg: Config, *, image_id: int | None = None, folder_id: int | None = None) -> int:
    """Restore one photo's files, or all of a folder's trashed rejects; returns how many files came back."""
    if (image_id is None) == (folder_id is None):
        raise CullError("restore one photo or one folder")
    where, value = ("image_id", image_id) if image_id is not None else ("folder_id", folder_id)
    entries = conn.execute(
        f"SELECT * FROM trash_entries WHERE {where} = ? AND restored_at IS NULL ORDER BY id", (value,)
    ).fetchall()
    if not entries:
        raise CullError("nothing of this to restore")
    restored = 0
    images = set()
    for entry in entries:
        top = _rebase(conn, cfg, entry)
        try:
            fileops.restore(top / entry["trashed"], top / entry["original"], top / entry["info"])
        except fileops.FileOpsError as exc:
            raise CullError(str(exc)) from exc
        conn.execute("UPDATE trash_entries SET restored_at = ? WHERE id = ?", (catalog.now(), entry["id"]))
        restored += 1
        if entry["image_id"] is not None:
            images.add(entry["image_id"])
    now = catalog.now()
    with catalog.transaction(conn):
        for i in images:
            conn.execute("UPDATE images SET state = 'present' WHERE id = ?", (i,))
            conn.execute(
                "INSERT INTO decisions (image_id, decision, decided_by, updated_at) VALUES (?, 'keep', 'owner', ?) "
                "ON CONFLICT (image_id) DO UPDATE SET decision = 'keep', decided_by = 'owner', updated_at = ?",
                (i, now, now),
            )
            image = _photo(conn, i)
            _log(conn, image, "restore", "keep", None)
    return restored


# --- what the app learned ------------------------------------------------------------------------------------------


@dataclass
class FolderMetrics:
    photos: int
    automatic: int  # photos the app decided (an automatic pile the owner left alone)
    changed: int  # photos in an automatic pile whose call the owner changed
    share_automatic: float
    change_rate: float
    minutes: float
    agreement: dict[str, tuple[int, int]]  # kind -> (agreed, total), the app's suggestion against the final call


def metrics(conn: sqlite3.Connection, folder_id: int) -> FolderMetrics:
    rows = conn.execute("SELECT * FROM cull_outcomes WHERE folder_id = ?", (folder_id,)).fetchall()
    auto = [r for r in rows if r["pile"] in ("auto_reject", "auto_keep")]
    automatic = sum(r["decided_by"] == "accepted" for r in auto)
    changed = sum(r["decided_by"] == "owner" and r["final"] != _pile_call(r["pile"]) for r in auto)
    agreement: dict[str, tuple[int, int]] = {}
    for r in rows:
        if r["kind"] is None or r["suggestion"] is None:
            continue
        agreed, total = agreement.get(r["kind"], (0, 0))
        agreement[r["kind"]] = (agreed + (r["suggestion"] == r["final"]), total + 1)
    times = [
        r[0]
        for r in conn.execute(
            "SELECT at FROM decision_log WHERE folder_id = ? "
            "AND (reason IS NULL OR reason != 'rawtherapee') ORDER BY id",
            (folder_id,),
        )
    ]
    seconds = 0.0
    stamps = [datetime.fromisoformat(t) for t in times]
    for a, b in itertools.pairwise(stamps):
        seconds += min((b - a).total_seconds(), IDLE_CAP)
    n = len(rows)
    return FolderMetrics(
        photos=n,
        automatic=automatic,
        changed=changed,
        share_automatic=automatic / n if n else 0.0,
        change_rate=changed / len(auto) if auto else 0.0,
        minutes=round(seconds / 60, 1),
        agreement=agreement,
    )


def _pile_call(pile: str | None) -> str | None:
    return {"auto_reject": "reject", "auto_keep": "keep"}.get(pile or "")


def trust_proposals(
    conn: sqlite3.Connection, *, folders: int = 3, minimum: int = 20, target: float = 0.95
) -> list[dict]:
    """Kinds on "suggest" whose suggestions matched the owner's final call at least 95% of the time recently."""
    modes = {r["kind"]: r["mode"] for r in conn.execute("SELECT kind, mode FROM trust")}
    recent = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM folders WHERE culled_at IS NOT NULL ORDER BY culled_at DESC LIMIT ?", (folders,)
        )
    ]
    if not recent:
        return []
    marks = ",".join("?" * len(recent))
    proposals = []
    for kind in KINDS:
        if modes.get(kind) == "automatic":
            continue
        row = conn.execute(
            f"SELECT COUNT(*), SUM(suggestion = final) FROM cull_outcomes WHERE kind = ? AND folder_id IN ({marks})",
            [kind, *recent],
        ).fetchone()
        total, agreed = row[0], row[1] or 0
        if total >= minimum and agreed / total >= target:
            proposals.append({"kind": kind, "agreement": agreed / total, "photos": total, "folders": len(recent)})
    return proposals
