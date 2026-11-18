from pathlib import Path

import pytest

from rie import catalog, config, drive, fileops, locations, sidecars
from tests import guard

FIXTURE = Path(__file__).parent / "data" / "rt58-edited.pp3"


def test_read_sidecar_imports_ratings_and_changed_values():
    info = sidecars.read_sidecar(FIXTURE.read_text(), "DSC_0001.NEF.pp3")
    assert (info.rank, info.color_label, info.in_trash, info.app_version) == (3, None, False, "5.8")
    assert info.params == {
        "exposure": {"ev": 0.45, "highlight_compression": 40},
        "white_balance": {"temperature_k": 4850, "tint": 1.04},
        "geometry": {"rotate_deg": -1.2, "crop": {"x": 120, "y": 80, "w": 5700, "h": 3800}},
    }
    assert info.provenance["exposure.ev"] == {
        "source": "human:rawtherapee",
        "locked": True,
        "sidecar": "DSC_0001.NEF.pp3",
        "rawtherapee": "5.8",
    }
    assert info.unmodeled == ["Sharpening"]


def test_defaults_import_nothing_and_auto_values_are_marked():
    text = "[General]\nRank=0\n[Exposure]\nAuto=true\nCompensation=0.6\n[White Balance]\nSetting=Auto\n"
    info = sidecars.read_sidecar(text)
    assert info.rank == 0
    assert info.params == {"exposure": {"ev": 0.6}}
    assert info.provenance["exposure.ev"]["auto"] is True
    assert "White Balance: Auto" in info.unmodeled and "Exposure: auto levels" in info.unmodeled


def test_out_of_range_values_are_reported_not_imported():
    info = sidecars.read_sidecar("[Exposure]\nCompensation=-9\n")
    assert info.params == {}
    assert any("outside the recipe's range" in u for u in info.unmodeled)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "[Version]\nAppVersion=5.8\n\n[General]\nRank=3\nInTrash=false\n",
            "[Version]\nAppVersion=5.8\n\n[General]\nRank=5\nInTrash=false\n",
        ),
        ("[General]\nColorLabel=0\n\n[Exposure]\nRank=1\n", "[General]\nRank=5\nColorLabel=0\n\n[Exposure]\nRank=1\n"),
        ("[Exposure]\nCompensation=1\n", "[Exposure]\nCompensation=1\n\n[General]\nRank=5\n"),
        ("[General]\r\nRank=2\r\n", "[General]\r\nRank=5\r\n"),
    ],
)
def test_with_rank_changes_only_the_rank(text, expected):
    assert sidecars.with_rank(text, 5) == expected


# --- with a catalog ------------------------------------------------------------------------------------------------


@pytest.fixture
def setup(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    conn = catalog.connect(cfg.catalog_path)
    shoot = tmp_path / "drive" / "shoot"
    shoot.mkdir(parents=True)
    (shoot / "DSC_0001.NEF").write_bytes(b"raw")
    (shoot / "DSC_0002.NEF").write_bytes(b"raw2")
    conn.execute("INSERT INTO folders (id, path, name, opened_at) VALUES (1, ?, 'shoot', 'now')", (str(shoot),))
    for i, name in ((1, "DSC_0001.NEF"), (2, "DSC_0002.NEF")):
        conn.execute(
            "INSERT INTO images (id, folder_id, name, kind, size, mtime_ns, quick_hash, scanned_at) "
            "VALUES (?, 1, ?, 'raw', 3, 0, ?, 'now')",
            (i, name, f"q1:{i}"),
        )
    return cfg, conn, shoot


def test_import_stores_the_rating_and_locked_recipe_once(setup):
    _cfg, conn, shoot = setup
    (shoot / "DSC_0001.NEF.pp3").write_bytes(FIXTURE.read_bytes())
    stats = sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"])
    assert (stats.imported, stats.ratings, stats.edits) == (1, 1, 1)
    assert conn.execute("SELECT rating FROM decisions WHERE image_id = 1").fetchone()[0] == 3
    recipe = catalog.loads(conn.execute("SELECT recipe FROM recipes WHERE image_id = 1").fetchone()[0])
    assert recipe["params"]["white_balance"] == {"temperature_k": 4850, "tint": 1.04}
    assert all(p["locked"] for p in recipe["provenance"].values())
    assert sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"]).imported == 0  # unchanged: skipped

    # The owner edits the photo in RawTherapee: the new values replace the imported ones.
    (shoot / "DSC_0001.NEF.pp3").write_text(FIXTURE.read_text().replace("Compensation=0.45", "Compensation=0.8"))
    assert sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"]).edits == 1
    version, text = conn.execute("SELECT version, recipe FROM recipes WHERE image_id = 1").fetchone()
    assert version == 2 and catalog.loads(text)["params"]["exposure"]["ev"] == 0.8


def test_mirroring_a_rating_backs_up_once_and_changes_only_the_rank(setup):
    cfg, conn, shoot = setup
    sidecar = shoot / "DSC_0001.NEF.pp3"
    sidecar.write_bytes(FIXTURE.read_bytes())
    sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"])
    with guard.watch(shoot) as seen:
        assert sidecars.write_rank(conn, cfg, 1, 5) == "written"
        assert sidecars.write_rank(conn, cfg, 1, 4) == "written"
    assert seen.violations() == []
    assert sidecar.read_text() == FIXTURE.read_text().replace("Rank=3", "Rank=4")
    backups = list(sidecars.backup_dir(cfg, 1).iterdir())
    assert len(backups) == 1 and backups[0].read_bytes() == FIXTURE.read_bytes()
    assert sorted(p.name for p in shoot.iterdir()) == ["DSC_0001.NEF", "DSC_0001.NEF.pp3", "DSC_0002.NEF"]
    # The app's own write is not imported again as an edit from RawTherapee.
    assert sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"]).imported == 0


def test_a_rating_without_a_sidecar_creates_one_holding_only_the_rating(setup):
    cfg, conn, shoot = setup
    with guard.watch(shoot) as seen:
        assert sidecars.write_rank(conn, cfg, 2, 2) == "created"
        assert sidecars.write_rank(conn, cfg, 2, 3) == "written"
    assert seen.violations() == []
    assert (shoot / "DSC_0002.NEF.pp3").read_text() == "[General]\nRank=3\n"
    row = conn.execute("SELECT created_by_app, backup_path FROM sidecars WHERE name = 'DSC_0002.NEF.pp3'").fetchone()
    assert tuple(row) == (1, None)  # the app's own file needs no backup


def test_a_sidecar_changed_meanwhile_is_not_overwritten(setup, monkeypatch):
    cfg, conn, shoot = setup
    sidecar = shoot / "DSC_0001.NEF.pp3"
    sidecar.write_bytes(FIXTURE.read_bytes())
    real_update = fileops.update_sidecar

    def racing_update(path, data, **kwargs):
        path.write_text("[General]\nRank=1\n")  # RawTherapee writes the file just before the app does
        return real_update(path, data, **kwargs)

    monkeypatch.setattr(fileops, "update_sidecar", racing_update)
    with pytest.raises(fileops.FileOpsError, match="changed since it was read"):
        sidecars.write_rank(conn, cfg, 1, 5)
    assert sidecar.read_text() == "[General]\nRank=1\n"


def test_ratings_wait_while_the_drive_is_away(setup, monkeypatch):
    cfg, conn, shoot = setup
    conn.execute("UPDATE folders SET drive_uuid = 'ABCD', path = 'shoot' WHERE id = 1")
    monkeypatch.setattr(locations, "available", lambda location, cfg: False)
    assert sidecars.write_rank(conn, cfg, 1, 4) == "pending"
    assert not (shoot / "DSC_0001.NEF.pp3").exists()
    monkeypatch.setattr(locations, "available", lambda location, cfg: True)
    monkeypatch.setattr(locations, "resolve", lambda location, cfg: shoot)
    assert sidecars.flush_pending(conn, cfg, 1) == 1
    assert (shoot / "DSC_0001.NEF.pp3").read_text() == "[General]\nRank=4\n"
    assert drive.DriveState.CONNECTED  # the real drive state is never consulted here
