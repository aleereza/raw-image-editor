import io
import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rie import catalog, config
from rie.api import app as api_app
from rie.api.app import create_app
from rie.cull import actions, piles
from rie.scan import previews
from tests.conftest import make_jpeg
from tests.test_previews import fake_nef, jpeg_bytes

SHARP = {"verdict": "sharp", "subject_blur": 0.3, "background_blur": 0.8, "reason": "the eyes are sharp"}
SOFT = {"verdict": "soft", "subject_blur": 2.4, "background_blur": 2.0, "reason": "the face is soft"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    conn = catalog.connect(cfg.catalog_path)
    top = tmp_path / "drive"
    shoot = top / "shoot"
    shoot.mkdir(parents=True)
    monkeypatch.setattr(actions, "topdir_for", lambda conn, cfg, folder_id, folder: top)
    conn.execute("INSERT INTO folders (id, path, name, opened_at) VALUES (1, ?, 'shoot', 'now')", (str(shoot),))
    for i, focus in ((1, SOFT), (2, SHARP)):
        name = f"DSC_000{i}.NEF"
        ranges = fake_nef(shoot / name, jpeg_bytes((30, 20)), jpeg_bytes((60, 40)))
        conn.execute(
            "INSERT INTO images (id, folder_id, name, kind, size, mtime_ns, quick_hash, orientation, previews, "
            "capture_time, width, height, scanned_at) VALUES (?, 1, ?, 'raw', 1, 0, ?, 8, ?, ?, 40, 60, 'now')",
            (i, name, f"q1:{i:064d}", json.dumps(ranges), f"2026-05-01T10:00:0{i}"),
        )
        for kind, result in (("focus", focus), ("burst", {"size": 1, "rank": 1, "gap": 0.0}), ("look", {"luma": 0.4})):
            conn.execute(
                "INSERT INTO analyses (image_id, kind, version, input_hash, result, created_at) "
                "VALUES (?, ?, 'v', 'q', ?, 'now')",
                (i, kind, json.dumps(result)),
            )
        for path in (previews.thumb_path(cfg, f"q1:{i:064d}"), previews.screen_path(cfg, f"q1:{i:064d}")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(jpeg_bytes((40, 60)))
    conn.execute(
        "INSERT INTO faces (image_id, idx, box, landmarks, score, source, blink_l, blink_r, smile, sharpness) "
        "VALUES (2, 0, '[0.2, 0.2, 0.5, 0.5]', '{\"eyes\": [[0.4, 0.4]]}', 0.9, 'whole', 0.1, 0.1, 0.7, 0.3)"
    )
    piles.sort_folder(conn, 1)
    return cfg, conn, TestClient(create_app(cfg, start_jobs=False)), shoot, top


def test_status_folders_and_photos(env):
    _cfg, _conn, client, _shoot, _top = env
    status = client.get("/api/status").json()
    assert status["drive"] == {"state": "unconfigured", "detail": status["drive"]["detail"], "configured": False}
    assert [f["name"] for f in client.get("/api/folders").json()] == ["shoot"]
    folder = client.get("/api/folders/1").json()
    assert folder["piles"] == {"your_call": 0, "auto_reject": 1, "auto_keep": 1}
    assert folder["trust"]["technical"] == "automatic"
    photos = client.get("/api/folders/1/photos").json()
    assert [(p["name"], p["pile"], p["focus"]["verdict"]) for p in photos] == [
        ("DSC_0001.NEF", "auto_reject", "soft"),
        ("DSC_0002.NEF", "auto_keep", "sharp"),
    ]
    assert client.get("/").status_code == 200


def test_a_new_build_shows_at_the_next_reload(tmp_path, monkeypatch):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setattr(api_app, "WEB_DIST", dist)
    page = TestClient(create_app(cfg, start_jobs=False)).get("/")
    assert "not built yet" in page.text and page.headers["cache-control"] == "no-cache"

    (dist / "assets").mkdir()
    (dist / "index.html").write_text('<script type="module" src="/assets/index-abc123.js"></script>')
    (dist / "assets" / "index-abc123.js").write_text("export {};")
    (dist / "favicon.svg").write_text("<svg/>")
    client = TestClient(create_app(cfg, start_jobs=False))
    page = client.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    unchanged = client.get("/", headers={"if-none-match": page.headers["etag"]})
    assert unchanged.status_code == 304 and unchanged.headers["cache-control"] == "no-cache"
    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"
    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]


def test_images_come_from_the_cache_and_the_raw(env):
    _cfg, _conn, client, _shoot, _top = env
    thumb = client.get("/api/photos/1/thumb?v=x")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert "immutable" in thumb.headers["cache-control"]
    full = client.get("/api/photos/1/full")
    with Image.open(io.BytesIO(full.content)) as im:
        assert im.size == (60, 40) and im.getexif()[0x0112] == 8
    face = client.get("/api/photos/2/face/0")
    with Image.open(io.BytesIO(face.content)) as im:
        assert im.size[0] > 0
    analysis = client.get("/api/photos/2/analysis").json()
    assert analysis["faces"][0]["eyes"] == [[0.4, 0.4]] and analysis["focus"]["verdict"] == "sharp"
    assert client.get("/api/photos/99/thumb").status_code == 404


def test_the_browser_keeps_no_copy_of_the_api_answers(env):
    # A tab restored after the browser restarts takes its fetches from the browser's cache: a stored photo list would
    # show the folder without the decisions made since the page last loaded it.
    _cfg, _conn, client, _shoot, _top = env
    answers = [client.get(url) for url in ("/api/status", "/api/folders", "/api/folders/1", "/api/folders/1/photos")]
    answers += [client.post("/api/photos/1/decision", json={"decision": "keep"}), client.get("/api/folders/99")]
    assert answers[-1].status_code == 404
    for answer in answers:
        assert answer.headers["content-type"] == "application/json"
        assert answer.headers["cache-control"] == "no-store", answer.url
    assert "immutable" in client.get("/api/photos/1/thumb?v=x").headers["cache-control"]
    assert client.get("/api/photos/1/full").headers["cache-control"] == "private, max-age=3600"


def test_decisions_done_and_restore(env):
    _cfg, _conn, client, shoot, _top = env
    assert client.post("/api/photos/2/decision", json={"decision": "reject", "reason": "blur"}).json() == {"ok": True}
    assert client.post("/api/photos/2/decision", json={"decision": "maybe"}).status_code == 409
    assert client.post("/api/photos/2/reason", json={"reason": "exposure"}).json() == {"ok": True}
    assert client.post("/api/photos/1/rating", json={"rating": 4}).status_code in (404, 405)  # no ratings
    (shoot / "DSC_0001.NEF.pp3").write_text("[General]\nRank=0\n")  # RawTherapee's sidecar for the photo
    preview = client.get("/api/folders/1/done").json()
    assert (preview["rejects"], preview["files"]) == (2, 3)  # the auto-rejected photo with its sidecar
    result = client.post("/api/folders/1/done").json()
    assert (result["trashed"], result["files"]) == (2, 3)
    assert sorted(p.name for p in shoot.iterdir()) == []
    assert client.get("/api/folders").json()[0]["in_trash"] == 3
    assert client.post("/api/folders/1/restore").json() == {"restored": 3}
    assert sorted(p.name for p in shoot.iterdir()) == ["DSC_0001.NEF", "DSC_0001.NEF.pp3", "DSC_0002.NEF"]
    assert client.post("/api/folders/1/restore").status_code == 409


def test_picks_and_whole_burst_decisions(env):
    _cfg, _conn, client, _shoot, _top = env
    assert client.post("/api/photos/2/pick").json() == {"ok": True, "changed": [2]}
    response = client.post("/api/photos/2/burst-decision", json={"decision": "reject"})
    assert response.json() == {"ok": True, "changed": [2]}  # a single photo is a burst of one
    assert client.post("/api/photos/1/burst-decision", json={"decision": "reject"}).json()["changed"] == []
    assert client.post("/api/photos/2/burst-decision", json={"decision": None}).json()["changed"] == [2]
    assert client.post("/api/photos/2/burst-decision", json={"decision": "keep"}).status_code == 409
    assert client.post("/api/photos/99/burst-decision", json={"decision": "reject"}).status_code == 409
    photos = {p["id"]: p["decision"] for p in client.get("/api/folders/1/photos").json()}
    assert photos == {1: None, 2: None}
    assert client.get("/api/folders/1/done").json()["open_bursts"] == 0


def test_trust_dial_resorts_open_folders(env):
    _cfg, _conn, client, _shoot, _top = env
    assert client.post("/api/trust", json={"kind": "technical", "mode": "suggest"}).status_code == 200
    assert client.get("/api/folders/1").json()["piles"] == {"your_call": 2, "auto_reject": 0, "auto_keep": 0}
    assert client.post("/api/trust", json={"kind": "nonsense", "mode": "suggest"}).status_code == 400


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_opening_a_folder_runs_as_a_job(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    shoot = tmp_path / "jpegs"
    make_jpeg(shoot / "IMG_0001.JPG", size=(300, 200))
    client = TestClient(create_app(cfg))
    job = client.post("/api/folders/open", json={"path": str(shoot)}).json()
    assert job["state"] == "queued"
    folders: list = []
    for _ in range(100):  # the job runs in the server's worker thread
        folders = client.get("/api/folders").json()
        if folders and folders[0]["analyzed_at"]:
            break
        time.sleep(0.1)
    assert folders and folders[0]["analyzed_at"]
    photos = client.get(f"/api/folders/{folders[0]['id']}/photos").json()
    assert [p["name"] for p in photos] == ["IMG_0001.JPG"] and photos[0]["pile"] is not None


def test_browse_lists_subfolders_newest_name_first(tmp_path, monkeypatch):
    photos = tmp_path / "Photos"
    for name in ("20260101 - (raw) - a", "20261116 - (raw) - b", ".hidden"):
        (photos / name).mkdir(parents=True)
    (photos / "notes.txt").write_text("not a folder")
    cfg = config.parse(
        {"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache"), "photos": str(photos)}}
    )
    conn = catalog.connect(cfg.catalog_path)
    conn.execute(
        "INSERT INTO folders (path, name, opened_at) VALUES (?, 'b', 'now')", (str(photos / "20261116 - (raw) - b"),)
    )
    client = TestClient(create_app(cfg))
    listing = client.get("/api/browse").json()
    assert listing["path"] == str(photos) and listing["parent"] == str(tmp_path)
    assert [(f["name"], f["folder_id"] is not None) for f in listing["folders"]] == [
        ("20261116 - (raw) - b", True),
        ("20260101 - (raw) - a", False),
    ]
    assert client.get("/api/browse", params={"path": str(photos / "missing")}).status_code == 404
    # Opening checks the folder first instead of failing later in the background.
    assert client.post("/api/folders/open", json={"path": "  "}).status_code == 400
    assert client.post("/api/folders/open", json={"path": str(photos / "missing")}).status_code == 404
    assert client.post("/api/folders/open", json={"path": str(photos / "notes.txt")}).status_code == 404

    from rie import drive, locations

    def unplugged(_path, _cfg):
        raise locations.DriveUnavailable(drive.DriveStatus(drive.DriveState.UNPLUGGED, "ABCD"))

    monkeypatch.setattr(locations, "locate", unplugged)
    response = client.get("/api/browse")
    assert response.status_code == 503 and "unplugged" in response.json()["detail"]
