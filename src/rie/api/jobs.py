"""Background jobs for the web server: opening a folder runs in a worker thread with its own catalog connection.

Jobs run one at a time, in order (plan §3.4: one scheduler, worker pools inside the steps). Progress lives in memory
for the event stream and in the `jobs` table, so a job that a crash interrupted shows up as failed and opening the
folder again resumes it: every step skips work that is already done.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import catalog
from ..config import Config
from ..pipeline import open_folder


@dataclass
class Job:
    id: int
    kind: str
    path: str
    state: str = "queued"  # queued, running, done, failed
    step: str = ""
    done: int = 0
    total: int = 0
    message: str = ""
    folder_id: int | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class JobRunner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.jobs: dict[int, Job] = {}
        self.version = 0  # bumps on every change, for the event stream
        self._lock = threading.Lock()
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="rie-jobs", daemon=True)
        self._thread.start()
        conn = catalog.connect(cfg.catalog_path)
        try:
            conn.execute(
                "UPDATE jobs SET state = 'failed', message = 'interrupted; open the folder again to resume' "
                "WHERE state IN ('queued', 'running')"
            )
        finally:
            conn.close()

    def _touch(self) -> None:
        with self._lock:
            self.version += 1

    def submit_open(self, path: Path) -> Job:
        conn = catalog.connect(self.cfg.catalog_path)
        try:
            cur = conn.execute(
                "INSERT INTO jobs (kind, state, message, started_at) VALUES ('open', 'queued', ?, ?)",
                (str(path), catalog.now()),
            )
            job = Job(id=int(cur.lastrowid or 0), kind="open", path=str(path))
        finally:
            conn.close()
        with self._lock:
            self.jobs[job.id] = job
            self.version += 1
        self._queue.put(job.id)
        return job

    def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            return self.version, [j.to_json() for j in sorted(self.jobs.values(), key=lambda j: j.id)]

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.jobs[job_id]
            job.state = "running"
            self._touch()
            conn = catalog.connect(self.cfg.catalog_path)
            try:

                def progress(step: str, done: int, total: int, job: Job = job) -> None:
                    job.step, job.done, job.total = step, done, total
                    self._touch()

                def found(folder_id: int, job: Job = job) -> None:
                    job.folder_id = folder_id
                    self._touch()

                result = open_folder(conn, self.cfg, Path(job.path), progress=progress, on_folder=found)
                job.folder_id = result.scan.folder_id
                paused = result.analysis is not None and result.analysis.paused
                piles = result.sort.piles if result.sort else {}
                job.message = (
                    "paused: the photo drive went away; open the folder again to resume"
                    if paused
                    else f"{result.scan.shots} photos: {piles.get('your_call', 0)} your call, "
                    f"{piles.get('auto_reject', 0)} auto-reject, {piles.get('auto_keep', 0)} auto-keep"
                )
                job.state = "done"
                catalog.backup(conn, self.cfg.paths.data)
            except Exception as exc:  # report every failure to the UI
                job.state = "failed"
                job.message = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            finally:
                job.finished = time.time()
                conn.execute(
                    "UPDATE jobs SET folder_id = ?, state = ?, step = ?, done = ?, total = ?, message = ?, "
                    "finished_at = ? WHERE id = ?",
                    (job.folder_id, job.state, job.step, job.done, job.total, job.message, catalog.now(), job.id),
                )
                conn.close()
                self._touch()
