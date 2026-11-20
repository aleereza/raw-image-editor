from pathlib import Path

import pytest

from rie import catalog, config, sidecars
from tests import guard

FIXTURE = Path(__file__).parent / "data" / "rt58-edited.pp3"


def test_read_sidecar_imports_changed_values_and_ignores_the_rating():
    info = sidecars.read_sidecar(FIXTURE.read_text(), "DSC_0001.NEF.pp3")
    assert (info.color_label, info.in_trash, info.app_version) == (None, False, "5.8")
    assert not hasattr(info, "rank")  # the fixture's Rank=3 is ignored (ADR 0010)
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
    assert info.params == {"exposure": {"ev": 0.6}}
    assert info.provenance["exposure.ev"]["auto"] is True
    assert "White Balance: Auto" in info.unmodeled and "Exposure: auto levels" in info.unmodeled


def test_out_of_range_values_are_reported_not_imported():
    info = sidecars.read_sidecar("[Exposure]\nCompensation=-9\n")
    assert info.params == {}
    assert any("outside the recipe's range" in u for u in info.unmodeled)


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


def test_import_stores_the_locked_recipe_once_and_no_rating(setup):
    _cfg, conn, shoot = setup
    (shoot / "DSC_0001.NEF.pp3").write_bytes(FIXTURE.read_bytes())
    with guard.watch(shoot) as seen:
        stats = sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"])
    assert seen.events == []  # the app only reads sidecars
    assert (shoot / "DSC_0001.NEF.pp3").read_bytes() == FIXTURE.read_bytes()
    assert (stats.imported, stats.edits) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0  # the fixture's Rank=3 is ignored
    assert conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 0
    recipe = catalog.loads(conn.execute("SELECT recipe FROM recipes WHERE image_id = 1").fetchone()[0])
    assert recipe["params"]["white_balance"] == {"temperature_k": 4850, "tint": 1.04}
    assert all(p["locked"] for p in recipe["provenance"].values())
    assert sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"]).imported == 0  # unchanged: skipped

    # The owner edits the photo in RawTherapee: the new values replace the imported ones.
    (shoot / "DSC_0001.NEF.pp3").write_text(FIXTURE.read_text().replace("Compensation=0.45", "Compensation=0.8"))
    assert sidecars.import_sidecars(conn, 1, shoot, ["DSC_0001.NEF.pp3"]).edits == 1
    version, text = conn.execute("SELECT version, recipe FROM recipes WHERE image_id = 1").fetchone()
    assert version == 2 and catalog.loads(text)["params"]["exposure"]["ev"] == 0.8
