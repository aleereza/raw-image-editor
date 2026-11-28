"""The personal culling model: how likely the owner is to reject a photo, learned from their own decisions.

Plan §4.11. After each Done culling, a small gradient-boosted classifier (scikit-learn's histogram gradient
boosting, which handles missing values) retrains on every photo the owner has culled, in seconds. A new version
becomes active only if, on the most recent folder, which no earlier version has seen, it agrees with the owner at
least as well as the rules and the version before it (cross-validated by burst, so frames of one burst never sit
on both sides). Every version is kept in `cull_models` and in `<paths.data>/models/`, so a worse one can be rolled
back. Until enough decisions exist (60 photos, 10 of each kind of call), the rules alone sort the folders.
"""

from __future__ import annotations

import math
import pickle
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .. import catalog
from ..config import Config
from .rules import VERDICTS

FEATURES = (
    "subject_blur",
    "background_blur",
    "shake",
    "motion",
    "handheld_ratio",
    "af_blur",
    "af_gap",
    "faces",
    "faces_cut",
    "face_size",
    "closed_eyes",
    "looking_away",
    "smile",
    "burst_size",
    "rank",
    "gap",
    "sharp_rel",
    "is_best",
    "duplicate",
    "luma",
    "clip_high",
    "clip_low",
    "lum_p01",
    "lum_p50",
    "lum_p99",
    "iso",
    "exposure_time",
    "focal_length",
    "fnumber",
    "flash",
    "rt_trash",
    *(f"verdict_{v}" for v in VERDICTS),
)
MIN_SAMPLES = 60
MIN_PER_CLASS = 10


def vectorize(rows: list[dict[str, Any]]) -> np.ndarray:
    data = np.full((len(rows), len(FEATURES)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        for j, name in enumerate(FEATURES):
            value = row.get(name)
            if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
                data[i, j] = value
            elif isinstance(value, bool):
                data[i, j] = float(value)
    return data


def _new_classifier() -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_depth=3, max_iter=150, learning_rate=0.08, min_samples_leaf=8, l2_regularization=1.0, random_state=0
    )


@dataclass
class TrainResult:
    promoted: bool
    n: int
    agreement: float | None = None  # the new model on the latest folder (cross-validated)
    baseline: float | None = None  # the app's suggestions at the time, on the same photos
    previous: float | None = None  # the active model before, on the same photos
    model_id: int | None = None
    note: str = ""


def _outcomes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.folder_id, o.image_id, o.final, o.suggestion, o.features, o.burst_id, f.culled_at "
        "FROM cull_outcomes o JOIN folders f ON f.id = o.folder_id ORDER BY o.id"
    ).fetchall()


def _active(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cull_models WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()


class OutdatedModel(ValueError):
    """A stored version trained on features the app no longer computes."""


def current(row: sqlite3.Row) -> bool:
    """Whether a stored version was trained on today's features.

    A version trained before ADR 0010 also used the photo's rating. It is set aside: the rules sort until the next
    Done culling trains a new version, and it can't be rolled back to.
    """
    return catalog.loads(row["info"], {}).get("features") == list(FEATURES)


def usable(row: sqlite3.Row) -> bool:
    return current(row) and Path(row["path"]).is_file()


_loaded: dict[str, Any] = {}


def load(path: str) -> Any:
    if path not in _loaded:
        with open(path, "rb") as fh:
            _loaded[path] = pickle.load(fh)  # the app's own file in its data folder
    return _loaded[path]


def active_model(conn: sqlite3.Connection) -> tuple[str, Any] | None:
    row = _active(conn)
    if row is None or not usable(row):
        return None
    return f"model:{row['id']}", load(row["path"])


def predict(model: Any, rows: list[dict[str, Any]]) -> list[float]:
    if not rows:
        return []
    return [float(p) for p in model.predict_proba(vectorize(rows))[:, 1]]


def train(conn: sqlite3.Connection, cfg: Config) -> TrainResult:
    """Retrain on every culled photo and activate the new version if it agrees with the owner at least as well."""
    rows = _outcomes(conn)
    n = len(rows)
    y = np.array([r["final"] == "reject" for r in rows], dtype=int)
    if n < MIN_SAMPLES or y.sum() < MIN_PER_CLASS or (n - y.sum()) < MIN_PER_CLASS:
        return TrainResult(False, n, note=f"needs {MIN_SAMPLES} culled photos with {MIN_PER_CLASS} keeps and rejects")
    features = [catalog.loads(r["features"], {}) for r in rows]
    x = vectorize(features)
    # A feature never measured in the training set (all missing) breaks the binning; it carries no information,
    # so it is zero for training and stays missing at prediction, where no split uses it.
    x[:, np.all(np.isnan(x), axis=0)] = 0.0
    groups = np.array([r["burst_id"] if r["burst_id"] is not None else -r["image_id"] for r in rows])

    from sklearn.model_selection import GroupKFold

    folds = min(5, len(set(groups.tolist())))
    cv_p = np.full(n, np.nan)
    for train_idx, test_idx in GroupKFold(n_splits=folds).split(x, y, groups):
        if len(set(y[train_idx].tolist())) < 2:
            cv_p[test_idx] = y[train_idx].mean()
            continue
        clf = _new_classifier()
        clf.fit(x[train_idx], y[train_idx])
        cv_p[test_idx] = clf.predict_proba(x[test_idx])[:, 1]

    latest_folder = max(rows, key=lambda r: r["culled_at"] or "")["folder_id"]
    replay = np.array([r["folder_id"] == latest_folder for r in rows])
    finals = y[replay]
    agreement = float(np.mean((cv_p[replay] >= 0.5) == finals))
    suggested = np.array([r["suggestion"] == "reject" for r in rows], dtype=int)
    baseline = float(np.mean(suggested[replay] == finals))
    previous_row = _active(conn)
    previous = None
    if previous_row is not None and usable(previous_row):
        old = load(previous_row["path"])
        previous = float(np.mean((old.predict_proba(x[replay])[:, 1] >= 0.5) == finals))

    clf = _new_classifier()
    clf.fit(x, y)
    models_dir = cfg.paths.data / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    number = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM cull_models").fetchone()[0])
    path = models_dir / f"cull-{number}.pkl"
    with open(path, "wb") as fh:  # the app's own file in its data folder
        pickle.dump(clf, fh)
    promoted = agreement >= baseline and (previous is None or agreement >= previous)
    info = {"features": list(FEATURES), "replay_folder": latest_folder, "replay_photos": int(replay.sum())}
    with catalog.transaction(conn):
        if promoted:
            conn.execute("UPDATE cull_models SET active = 0")
        cur = conn.execute(
            "INSERT INTO cull_models (created_at, path, n_train, agreement, baseline, active, info) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (catalog.now(), str(path), n, agreement, baseline, int(promoted), catalog.dumps(info)),
        )
    note = (
        f"agrees with you on {agreement:.0%} of the latest folder (the app's sort: {baseline:.0%}"
        + (f", the previous model: {previous:.0%}" if previous is not None else "")
        + ")"
    )
    return TrainResult(promoted, n, agreement, baseline, previous, int(cur.lastrowid or 0), note)


def rollback(conn: sqlite3.Connection, model_id: int) -> None:
    """Make an earlier model version the active one."""
    row = conn.execute("SELECT * FROM cull_models WHERE id = ?", (model_id,)).fetchone()
    if row is None:
        raise ValueError(f"no model version {model_id}")
    if not current(row):
        raise OutdatedModel(f"model version {model_id} was trained on features the app no longer uses")
    with catalog.transaction(conn):
        conn.execute("UPDATE cull_models SET active = 0")
        conn.execute("UPDATE cull_models SET active = 1 WHERE id = ?", (model_id,))
