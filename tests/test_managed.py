"""Managed sidecars: created, updated only while unchanged, the owner's backed up and only added to, and the
RawTherapee round trip importing only what the owner changed (plan §3.3, decision 14, ADR 0007)."""

from __future__ import annotations

import pytest

from rie import catalog, config, managed, review, sidecars
from rie.engine import pp3
from rie.engine.recipe import SCHEMA
from tests import guard


@pytest.fixture
def env(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    conn = catalog.connect(cfg.catalog_path)
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    raw = shoot / "DSC_0001.NEF"
    raw.write_bytes(b"raw")
    raw.chmod(0o444)
    folder = conn.execute(
        "INSERT INTO folders (drive_uuid, path, name, opened_at) VALUES ('', ?, 'shoot', ?)",
        (str(shoot), catalog.now()),
    ).lastrowid
    image = conn.execute(
        "INSERT INTO images (folder_id, name, kind, size, mtime_ns, quick_hash, scanned_at) "
        "VALUES (?, 'DSC_0001.NEF', 'raw', 3, 1, 'q', ?)",
        (folder, catalog.now()),
    ).lastrowid
    recipe = {
        "schema": SCHEMA,
        "params": {"white_balance": {"temperature_k": 5200, "tint": 1.02}, "exposure": {"ev": 0.5}},
        "provenance": {
            "white_balance.temperature_k": {"source": "agent:wb@1"},
            "exposure.ev": {"source": "agent:exposure@1"},
        },
    }
    review.save_recipe(conn, int(image or 0), recipe, "edit-run:1")
    yield cfg, conn, shoot, int(folder or 0), int(image or 0)
    conn.close()


def test_a_new_sidecar_holds_the_base_profile_and_the_recipe(env):
    cfg, conn, shoot, _folder, image = env
    with guard.watch(shoot) as seen:
        written = managed.write_sidecar(conn, cfg, image)
    assert seen.violations() == []
    assert written.action == "created"
    profile = pp3.load(shoot / "DSC_0001.NEF.pp3")
    assert profile["White Balance"]["Temperature"] == "5200" and profile["Exposure"]["Compensation"] == "0.5"
    assert profile["RAW Bayer"]["Method"] == "amaze"  # from the base profile
    row = conn.execute("SELECT created_by_app, sha256, written FROM sidecars").fetchone()
    assert row["created_by_app"] == 1 and row["written"].startswith("# written by raw-image-editor")


def test_an_app_sidecar_is_updated_but_not_after_rawtherapee_changed_it(env):
    cfg, conn, shoot, _folder, image = env
    managed.write_sidecar(conn, cfg, image)
    review.nudge(conn, image, "ev", 1)
    assert managed.write_sidecar(conn, cfg, image).action == "updated"
    assert pp3.load(shoot / "DSC_0001.NEF.pp3")["Exposure"]["Compensation"] == "0.65"
    path = shoot / "DSC_0001.NEF.pp3"
    path.write_text(path.read_text().replace("Compensation=0.65", "Compensation=1.2"))  # the owner, in RawTherapee
    with pytest.raises(managed.SidecarError, match="changed in RawTherapee"):
        managed.write_sidecar(conn, cfg, image)


def test_the_owners_sidecar_is_backed_up_and_only_added_to(env):
    cfg, conn, shoot, folder, image = env
    own = "[Version]\nAppVersion=5.8\nVersion=349\n\n[Exposure]\nCompensation=-0.7\n\n[White Balance]\nSetting=Camera\n"
    (shoot / "DSC_0001.NEF.pp3").write_text(own)
    with guard.watch(shoot) as seen:
        written = managed.write_sidecar(conn, cfg, image)
    assert seen.violations() == []
    assert written.action == "added"
    profile = pp3.load(shoot / "DSC_0001.NEF.pp3")
    assert profile["Exposure"]["Compensation"] == "-0.7" and profile["White Balance"]["Setting"] == "Camera"
    assert profile["RAW Bayer"]["Method"] == "amaze"  # added: the owner's sidecar did not have it
    backups = list((cfg.paths.data / "sidecar-backups" / str(folder)).iterdir())
    assert len(backups) == 1 and backups[0].read_text() == own


def test_importing_an_app_sidecar_keeps_only_the_owners_changes(env):
    cfg, conn, shoot, folder, image = env
    managed.write_sidecar(conn, cfg, image)
    path = shoot / "DSC_0001.NEF.pp3"
    path.write_text(path.read_text().replace("Compensation=0.5", "Compensation=1.2"))  # the owner, in RawTherapee
    sidecars.import_sidecars(conn, folder, shoot, ["DSC_0001.NEF.pp3"])
    _version, recipe = review.load_recipe(conn, image)
    assert recipe["params"]["exposure"]["ev"] == 1.2
    assert recipe["provenance"]["exposure.ev"]["source"] == sidecars.SOURCE
    assert recipe["provenance"]["white_balance.temperature_k"]["source"] == "agent:wb@1"  # still the app's
