"""Phase 2's review routes: edits, previews from the cache only, review actions, looks (no rendering needed)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rie import catalog, config, review
from rie.api.app import create_app
from rie.engine.recipe import SCHEMA


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    conn = catalog.connect(cfg.catalog_path)
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    folder = conn.execute(
        "INSERT INTO folders (drive_uuid, path, name, opened_at) VALUES ('', ?, 'shoot', ?)",
        (str(shoot), catalog.now()),
    ).lastrowid
    scene = conn.execute("INSERT INTO groups (folder_id, kind, position) VALUES (?, 'scene', 0)", (folder,)).lastrowid
    preview = cfg.paths.cache / "renders" / "cache" / "ab" / ("ab" * 32 + ".jpg")
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"\xff\xd8\xff\xd9")
    images = []
    for n in range(2):
        image = conn.execute(
            "INSERT INTO images (folder_id, name, kind, size, mtime_ns, quick_hash, scanned_at) "
            "VALUES (?, ?, 'raw', 1, 1, ?, ?)",
            (folder, f"DSC_000{n}.NEF", f"q{n}", catalog.now()),
        ).lastrowid
        burst = conn.execute(
            "INSERT INTO groups (folder_id, kind, parent_id, position) VALUES (?, 'burst', ?, ?)", (folder, scene, n)
        ).lastrowid
        conn.execute("INSERT INTO group_members (group_id, image_id, position) VALUES (?, ?, 0)", (burst, image))
        recipe = {
            "schema": SCHEMA,
            "params": {"exposure": {"ev": 0.4}},
            "provenance": {"exposure.ev": {"source": "agent:exposure@1"}},
        }
        review.save_recipe(conn, int(image or 0), recipe, "edit-run:1")
        conn.execute(
            "INSERT INTO edits (image_id, look, review, reasons, before, after, updated_at) "
            "VALUES (?, 'neutral@1', ?, '[\"exposure: applied halfway\"]', ?, ?, ?)",
            (image, int(n == 0), str(preview), str(tmp_path / "outside.jpg"), catalog.now()),
        )
        images.append(image)
    monkeypatch.setattr(review, "rerender", lambda conn, cfg, image_id: preview)
    return cfg, conn, TestClient(create_app(cfg, start_jobs=False)), folder, images


def test_the_folders_edits(env):
    _cfg, _conn, client, folder, images = env
    data = client.get(f"/api/folders/{folder}/edits").json()
    assert [e["id"] for e in data["edits"]] == images
    assert data["edits"][0]["review"] is True and data["edits"][0]["has_before"] is True
    assert data["edits"][0]["has_after"] is False  # the path is gone
    assert data["looks"][0]["id"] == "neutral@1"
    assert data["no_override_share"] == 1.0


def test_previews_come_only_from_the_render_cache(env):
    _cfg, _conn, client, _folder, images = env
    assert client.get(f"/api/photos/{images[0]}/preview/before").status_code == 200
    assert client.get(f"/api/photos/{images[0]}/preview/after").status_code == 404
    assert client.get(f"/api/photos/{images[0]}/preview/sidecar").status_code == 404
    assert client.get("/api/looklab/..%2F..%2Fcatalog").status_code == 404


def test_review_actions(env):
    _cfg, _conn, client, folder, images = env
    nudged = client.post(f"/api/photos/{images[0]}/nudge", json={"kind": "ev", "steps": -2}).json()
    assert nudged["params"]["exposure"]["ev"] == pytest.approx(0.1)
    assert nudged["provenance"]["exposure.ev"]["locked"] is True
    assert client.post(f"/api/photos/{images[0]}/nudge", json={"kind": "hue", "steps": 1}).status_code == 400
    locked = client.post(f"/api/photos/{images[1]}/lock", json={"key": "exposure.ev", "locked": True}).json()
    assert locked["provenance"]["exposure.ev"]["locked"] is True
    seen = client.post(f"/api/photos/{images[0]}/reviewed", json={"reviewed": True}).json()
    assert seen["reviewed"] is True
    reset = client.post(f"/api/photos/{images[0]}/reset", json={"group": "exposure"}).json()
    assert reset["params"]["exposure"]["ev"] == 0.0
    assert client.get(f"/api/folders/{folder}/edits").json()["no_override_share"] == 0.5


def test_accept_proposal_route(env):
    _cfg, conn, client, _folder, images = env
    run_id = int(
        conn.execute(
            "INSERT INTO edit_runs (folder_id, look, started_at) VALUES (?, 'neutral@1', ?)", (env[3], catalog.now())
        ).lastrowid
        or 0
    )
    conn.execute("UPDATE edits SET run_id = ? WHERE image_id = ?", (run_id, images[0]))
    conn.execute(
        "INSERT INTO proposals (image_id, run_id, agent, params, as_shot, applied, confidence, band, rationale, "
        "created_at) VALUES (?, ?, 'geometry@4', ?, ?, '{}', 0.44, 'as_shot', 'level the lines', ?)",
        (images[0], run_id, '{"geometry": {"rotate_deg": -3.65}}', '{"geometry": {"rotate_deg": 0.0}}', catalog.now()),
    )
    conn.commit()
    accepted = client.post(f"/api/photos/{images[0]}/accept", json={"group": "geometry"}).json()
    assert accepted["params"]["geometry"]["rotate_deg"] == pytest.approx(-3.65)
    assert accepted["provenance"]["geometry.rotate_deg"]["locked"] is True
    assert client.post(f"/api/photos/{images[0]}/accept", json={"group": "color"}).status_code == 400


def test_looks_are_saved_in_versions(env):
    _cfg, _conn, client, _folder, images = env
    look = client.get(f"/api/folders/{env[3]}/edits").json()["looks"][0]
    first = client.post("/api/looks", json={"name": "Indoor family", "look": {**look, "contrast": 8}}).json()
    second = client.post("/api/looks", json={"name": "indoor-family", "look": {**look, "warmth": 5}}).json()
    assert (first["id"], second["id"]) == ("indoor-family@1", "indoor-family@2")
    assert client.post("/api/looks", json={"name": "neutral", "look": look}).status_code == 400
    used = client.post(f"/api/photos/{images[0]}/look", json={"look": "indoor-family@1", "scene": False}).json()
    assert used["photo"]["look"] == "indoor-family@1"
    assert used["photo"]["params"]["tone"]["contrast"] == 8
