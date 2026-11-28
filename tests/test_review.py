"""Phase 2: storing edit runs, the owner's review actions, and export into jpg/ (only through rie.fileops)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rie import catalog, config, review
from rie.edit import EditSettings, edit_folder
from rie.engine.recipe import SCHEMA
from rie.export import export_folder, target_name
from rie.scan.folder import scan_folder
from tests import guard
from tests.conftest import sample_nefs

needs_rawtherapee = pytest.mark.skipif(
    shutil.which("rawtherapee-cli") is None or shutil.which("exiftool") is None or not sample_nefs(1),
    reason="needs rawtherapee-cli, exiftool and a sample NEF",
)


@pytest.fixture
def cfg(tmp_path):
    return config.parse({"paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")}})


@pytest.fixture
def conn(cfg):
    connection = catalog.connect(cfg.catalog_path)
    yield connection
    connection.close()


def _photo(conn, folder_id: int, name: str, scene: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO images (folder_id, name, kind, size, mtime_ns, quick_hash, scanned_at) "
        "VALUES (?, ?, 'raw', 1, 1, ?, ?)",
        (folder_id, name, f"q-{name}", catalog.now()),
    )
    image_id = int(cur.lastrowid or 0)
    recipe = {
        "schema": SCHEMA,
        "params": {"exposure": {"ev": 0.5}, "white_balance": {"temperature_k": 5000, "tint": 1.0}},
        "provenance": {
            "exposure.ev": {"source": "agent:exposure@1", "confidence": 0.9},
            "white_balance.temperature_k": {"source": "agent:wb@1"},
            "white_balance.tint": {"source": "agent:wb@1"},
        },
    }
    review.save_recipe(conn, image_id, recipe, "edit-run:1")
    conn.execute("INSERT INTO edits (image_id, look, updated_at) VALUES (?, 'neutral@1', ?)", (image_id, catalog.now()))
    if scene is not None:
        burst = conn.execute(
            "INSERT INTO groups (folder_id, kind, parent_id, position) VALUES (?, 'burst', ?, 0)", (folder_id, scene)
        ).lastrowid
        conn.execute("INSERT INTO group_members (group_id, image_id, position) VALUES (?, ?, 0)", (burst, image_id))
    return image_id


@pytest.fixture
def folder(conn):
    cur = conn.execute(
        "INSERT INTO folders (drive_uuid, path, name, opened_at) VALUES ('', '/x/shoot', 'shoot', ?)", (catalog.now(),)
    )
    return int(cur.lastrowid or 0)


def test_the_migration_adds_the_phase_2_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"edit_runs", "proposals", "edits", "overrides", "exports", "looks"} <= tables
    assert "written" in {r[1] for r in conn.execute("PRAGMA table_info(sidecars)")}
    assert catalog.schema_version(conn) == 2


def test_a_nudge_locks_the_value_records_the_override_and_versions_the_recipe(conn, folder):
    image = _photo(conn, folder, "DSC_0001.NEF")
    assert review.nudge(conn, image, "ev", 2) == 2
    _version, recipe = review.load_recipe(conn, image)
    assert recipe["params"]["exposure"]["ev"] == pytest.approx(0.8)
    assert recipe["provenance"]["exposure.ev"] == {"source": review.SOURCE_OWNER, "locked": True, "action": "nudge"}
    row = conn.execute("SELECT param, app_value, owner_value, source, action FROM overrides").fetchone()
    assert tuple(row) == ("exposure.ev", "0.5", "0.8", "agent:exposure@1", "nudge")
    review.nudge(conn, image, "warmth", 1)
    _, recipe = review.load_recipe(conn, image)
    assert recipe["params"]["white_balance"]["temperature_k"] == round(1e6 / (200 - 10))  # warmer: fewer mired
    assert conn.execute("SELECT COUNT(*) FROM recipe_history WHERE image_id = ?", (image,)).fetchone()[0] == 3
    assert review.no_override_share(conn, folder) == 0.0


def test_reset_and_lock(conn, folder):
    image = _photo(conn, folder, "DSC_0001.NEF")
    review.reset(conn, image, "white_balance")
    _, recipe = review.load_recipe(conn, image)
    assert recipe["params"]["white_balance"] == {"as_shot": True}
    assert recipe["provenance"]["white_balance.as_shot"]["locked"] is True
    review.set_lock(conn, image, "exposure.ev", True)
    _, recipe = review.load_recipe(conn, image)
    assert recipe["provenance"]["exposure.ev"]["locked"] is True
    with pytest.raises(review.ReviewError):
        review.set_lock(conn, image, "geometry.rotate_deg", True)
    with pytest.raises(review.ReviewError):
        review.nudge(conn, image, "saturation", 1)


def test_apply_proposal_sets_the_agents_full_value_and_locks_it(conn, folder):
    image = _photo(conn, folder, "DSC_0001.NEF")
    run_id = int(
        conn.execute(
            "INSERT INTO edit_runs (folder_id, look, started_at) VALUES (?, 'neutral@1', ?)", (folder, catalog.now())
        ).lastrowid
        or 0
    )
    conn.execute("UPDATE edits SET run_id = ? WHERE image_id = ?", (run_id, image))
    conn.execute(
        "INSERT INTO proposals (image_id, run_id, agent, params, as_shot, applied, confidence, band, rationale, "
        "created_at) VALUES (?, ?, 'geometry@4', ?, ?, '{}', 0.44, 'as_shot', 'level the lines', ?)",
        (image, run_id, '{"geometry": {"rotate_deg": -3.65}}', '{"geometry": {"rotate_deg": 0.0}}', catalog.now()),
    )
    review.apply_proposal(conn, image, "geometry")
    _, recipe = review.load_recipe(conn, image)
    assert recipe["params"]["geometry"] == {"rotate_deg": -3.65}
    assert recipe["provenance"]["geometry.rotate_deg"] == {
        "source": review.SOURCE_OWNER,
        "locked": True,
        "action": "accept",
    }
    row = conn.execute(
        "SELECT param, owner_value, action FROM overrides WHERE param = 'geometry.rotate_deg'"
    ).fetchone()
    assert tuple(row) == ("geometry.rotate_deg", "-3.65", "accept")
    with pytest.raises(review.ReviewError):
        review.apply_proposal(conn, image, "color")


def test_apply_to_scene_gives_the_values_to_the_scenes_other_photos(conn, folder):
    scene = int(
        conn.execute("INSERT INTO groups (folder_id, kind, position) VALUES (?, 'scene', 0)", (folder,)).lastrowid or 0
    )
    a = _photo(conn, folder, "DSC_0001.NEF", scene)
    b = _photo(conn, folder, "DSC_0002.NEF", scene)
    c = _photo(conn, folder, "DSC_0003.NEF")
    review.nudge(conn, a, "ev", -4)
    assert review.apply_to_scene(conn, a, ("exposure",)) == [b]
    _, recipe = review.load_recipe(conn, b)
    assert recipe["params"]["exposure"]["ev"] == pytest.approx(-0.1)
    assert recipe["provenance"]["exposure.ev"]["action"] == "scene"
    _, other = review.load_recipe(conn, c)
    assert other["params"]["exposure"]["ev"] == 0.5


@pytest.mark.rawtherapee
@needs_rawtherapee
def test_edit_store_nudge_and_export_twice_through_fileops_only(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    nef = sample_nefs(1)[0]
    shutil.copyfile(nef, shoot / nef.name)
    (shoot / nef.name).chmod(0o444)
    folder_id = scan_folder(conn, cfg, shoot).folder_id
    run = edit_folder(conn, cfg, folder_id, settings=EditSettings(workers=1, size=512))
    run_id = review.store_run(conn, run)
    rows = review.edits_of_folder(conn, folder_id)
    assert len(rows) == 1 and rows[0]["has_before"] and rows[0]["has_after"]
    assert {p["agent"].split("@")[0] for p in rows[0]["proposals"]} >= {"wb", "exposure", "geometry"}
    image = rows[0]["id"]
    assert conn.execute("SELECT COUNT(*) FROM proposals WHERE run_id = ?", (run_id,)).fetchone()[0] == 5
    target = shoot / target_name(nef.name)
    with guard.watch(shoot) as seen:
        first = export_folder(conn, cfg, folder_id, workers=1)
    assert first.exported == 1 and target.is_file()
    assert seen.violations() == []
    again = export_folder(conn, cfg, folder_id, workers=1)
    assert again.skipped == 1 and again.exported == 0
    review.nudge(conn, image, "ev", 1)
    old = target.read_bytes()
    with guard.watch(shoot) as seen:
        third = export_folder(conn, cfg, folder_id, workers=1)
    assert seen.violations() == []
    assert third.exported == 1 and third.replaced == 1
    assert target.read_bytes() != old
    trashed = conn.execute("SELECT trashed, topdir FROM trash_entries").fetchone()
    assert (Path(trashed["topdir"]) / trashed["trashed"]).read_bytes() == old  # the old JPEG is in the trash
    assert sorted(p.name for p in shoot.iterdir()) == sorted([nef.name, f"{nef.name}.pp3", "jpg"])
    assert first.sidecars == 1  # the managed sidecar, so RawTherapee shows the export's edit
