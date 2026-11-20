import json
from pathlib import Path

import pytest

from rie import catalog, config
from rie.cull import actions, piles, rules
from rie.cull import model as model_mod
from tests import guard

SHARP = {"verdict": "sharp", "subject_blur": 0.3, "background_blur": 0.8, "reason": "the eyes are sharp"}
SOFT = {"verdict": "soft", "subject_blur": 2.4, "background_blur": 2.0, "reason": "the face is soft"}
LOOK = {"luma": 0.4, "clip_high": 0.0, "clip_low": 0.0}


def test_rules_in_order():
    single = {"size": 1, "rank": 1, "gap": 0.0}
    assert rules.suggest({}, SOFT, single, LOOK, [], {}).kind == "technical"
    assert rules.suggest({}, SOFT, single, LOOK, [], {}).decision == "reject"
    assert rules.suggest({}, SHARP, single, {"luma": 0.01, "clip_low": 0.95}, [], {}).reason == "almost black frame"
    keep = rules.suggest({}, SHARP, single, LOOK, [], {})
    assert (keep.decision, keep.kind, keep.reject_probability) == ("keep", "technical", 0.1)
    rated = rules.suggest({}, SOFT, single, LOOK, [], {"rating": 3})  # the app ignores ratings (ADR 0010)
    assert (rated.decision, rated.kind) == ("reject", "technical") and "rating" not in rated.features

    burst = {"size": 3, "rank": 2, "gap": 0.2, "position": 1, "issues": ["70% as sharp as the sharpest frame"]}
    frames = {10: (2, 1), 11: (1, 2), 12: (3, 3)}
    twin = rules.suggest({}, SHARP, {**burst, "duplicate_of": [11]}, LOOK, [], {}, frames)
    assert (twin.kind, twin.reason) == (
        "duplicates",
        "almost the same as #2, which is better; this one: 70% as sharp as the sharpest frame",
    )
    eyes = rules.suggest(
        {}, SHARP, {**burst, "closed_eyes": 1, "reason": "#2 is better: 1 of 3 blinking"}, LOOK, [], {}
    )
    assert (eyes.decision, eyes.kind) == ("reject", "eyes")
    moment = rules.suggest({}, SHARP, {**burst, "reason": "#2 is better"}, LOOK, [], {})
    assert (moment.decision, moment.kind) == ("reject", "expression")
    best = rules.suggest({}, SHARP, {**burst, "rank": 1, "gap": 0.0, "reason": "best: #2 of 3"}, LOOK, [], {})
    assert (best.decision, best.kind) == ("keep", "expression")


def test_piles_follow_the_trust_dial():
    modes = {"technical": "automatic", "duplicates": "automatic", "eyes": "suggest"}
    assert piles.pile_for("reject", "technical", 0.925, modes) == "auto_reject"
    assert piles.pile_for("reject", "technical", 0.7, modes) == "your_call"  # not sure enough
    assert piles.pile_for("reject", "eyes", 0.99, modes) == "your_call"  # eyes are on suggest
    assert piles.pile_for("keep", "technical", 0.1, modes) == "auto_keep"


# --- a folder in the catalog ---------------------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A folder of six photos with analysis results: a soft single, a sharp single, and a burst of three plus a
    RAW+JPEG single. Files are small stand-ins; the trash lives inside tmp_path."""
    cfg = config.parse({"paths": {"data": str(tmp_path / "data"), "cache": str(tmp_path / "cache")}})
    conn = catalog.connect(cfg.catalog_path)
    top = tmp_path / "drive"
    shoot = top / "Photos" / "shoot"
    shoot.mkdir(parents=True)
    monkeypatch.setattr(actions, "topdir_for", lambda conn, cfg, folder_id, folder: top)
    conn.execute("INSERT INTO folders (id, path, name, opened_at) VALUES (1, ?, 'shoot', 'now')", (str(shoot),))
    photos = [
        ("DSC_0001.NEF", None, SOFT, {"size": 1, "rank": 1, "gap": 0.0}),
        ("DSC_0002.NEF", "DSC_0002.JPG", SHARP, {"size": 1, "rank": 1, "gap": 0.0}),
        ("DSC_0003.NEF", None, SHARP, {"size": 3, "rank": 2, "gap": 0.3, "position": 1, "duplicate_of": [4]}),
        ("DSC_0004.NEF", None, SHARP, {"size": 3, "rank": 1, "gap": 0.0, "position": 2, "duplicate_of": [3]}),
        ("DSC_0005.NEF", None, SHARP, {"size": 3, "rank": 3, "gap": 0.1, "position": 3, "closed_eyes": 1}),
        ("DSC_0006.NEF", None, SHARP, {"size": 1, "rank": 1, "gap": 0.0}),
    ]
    conn.execute("INSERT INTO groups (id, folder_id, kind, position) VALUES (1, 1, 'burst', 0)")
    for i, (name, jpeg, focus_result, burst) in enumerate(photos, start=1):
        (shoot / name).write_bytes(b"raw" * i)
        if jpeg:
            (shoot / jpeg).write_bytes(b"jpeg")
        conn.execute(
            "INSERT INTO images (id, folder_id, name, kind, jpeg_name, size, mtime_ns, quick_hash, capture_time, "
            "scanned_at) VALUES (?, 1, ?, 'raw', ?, ?, 0, ?, ?, 'now')",
            (i, name, jpeg, 3 * i, f"q1:{i}", f"2026-05-01T10:00:0{i}"),
        )
        burst = {**burst, "burst_id": 1 if burst["size"] > 1 else None}
        for kind, result in (("focus", focus_result), ("burst", burst), ("look", LOOK)):
            conn.execute(
                "INSERT INTO analyses (image_id, kind, version, input_hash, result, created_at) "
                "VALUES (?, ?, 'v', ?, ?, 'now')",
                (i, kind, f"q1:{i}", json.dumps(result)),
            )
        if burst["size"] > 1:
            conn.execute(
                "INSERT INTO group_members (group_id, image_id, position, rank) VALUES (1, ?, ?, ?)",
                (i, burst["position"] - 1, burst["rank"]),
            )
    (shoot / "DSC_0001.NEF.pp3").write_text("[General]\nRank=0\n")
    piles.sort_folder(conn, 1)
    return cfg, conn, shoot, top


def pile_of(conn, image_id):
    return conn.execute("SELECT pile, decision, kind FROM suggestions WHERE image_id = ?", (image_id,)).fetchone()


def test_sort_folder_puts_photos_in_piles(folder):
    _cfg, conn, _shoot, _top = folder
    assert tuple(pile_of(conn, 1)) == ("auto_reject", "reject", "technical")
    assert tuple(pile_of(conn, 2)) == ("auto_keep", "keep", "technical")
    assert tuple(pile_of(conn, 3)) == ("auto_reject", "reject", "duplicates")
    assert tuple(pile_of(conn, 4)) == ("your_call", "keep", "expression")
    assert tuple(pile_of(conn, 5)) == ("your_call", "reject", "eyes")


def test_decisions_are_logged_with_the_app_suggestion(folder):
    _cfg, conn, _shoot, _top = folder
    actions.set_decision(conn, 1, "keep", reason="blur")
    row = conn.execute("SELECT action, pile, suggestion, kind, reason FROM decision_log").fetchone()
    assert tuple(row) == ("keep", "auto_reject", "reject", "technical", "blur")
    with pytest.raises(actions.CullError):
        actions.set_decision(conn, 1, "keep", reason="because")


def decisions_of(conn):
    return dict(conn.execute("SELECT image_id, decision FROM decisions").fetchall())


def test_picking_a_frame_rejects_the_rest_of_its_burst_and_moves_the_keep(folder):
    _cfg, conn, _shoot, _top = folder
    assert actions.pick(conn, 5) == [5, 4]  # frame 3 is the app's own reject (auto-reject) and stays the app's
    assert decisions_of(conn) == {5: "keep", 4: "reject"}
    assert actions.pick(conn, 4) == [4, 5]  # picking another frame moves the keep
    assert decisions_of(conn) == {4: "keep", 5: "reject"}
    assert actions.pick(conn, 5, keep_others=True) == [5]  # Shift: keep a second frame
    assert decisions_of(conn) == {4: "keep", 5: "keep"}
    actions.set_decision(conn, 3, "keep")  # the owner rescues the app's reject
    assert actions.pick(conn, 4) == [4, 3, 5]  # the owner's other keeps become rejects, in shooting order
    assert decisions_of(conn) == {3: "reject", 4: "keep", 5: "reject"}
    assert actions.pick(conn, 4) == [4]  # nothing else changes
    assert actions.pick(conn, 2) == [2]  # a single photo is a burst of one
    log = conn.execute("SELECT image_id, action, value FROM decision_log WHERE action = 'pick'").fetchall()
    assert [tuple(r) for r in log] == [
        (5, "pick", "reject_others"),
        (4, "pick", "reject_others"),
        (5, "pick", "keep_others"),
        (4, "pick", "reject_others"),
        (4, "pick", "reject_others"),
        (2, "pick", "reject_others"),
    ]


def test_rejecting_or_clearing_a_whole_burst(folder):
    _cfg, conn, _shoot, _top = folder
    actions.set_decision(conn, 4, "keep")
    assert actions.decide_burst(conn, 5, "reject") == [4, 5]  # frame 3 stays the app's reject
    assert decisions_of(conn) == {4: "reject", 5: "reject"}
    log = conn.execute("SELECT image_id, action, value, pile FROM decision_log WHERE action = 'burst'").fetchall()
    assert [tuple(r) for r in log] == [(4, "burst", "reject", "your_call"), (5, "burst", "reject", "your_call")]
    assert actions.decide_burst(conn, 4, "reject") == []  # nothing left to change
    assert actions.decide_burst(conn, 3, None) == [4, 5]  # clears every frame the owner decided
    assert decisions_of(conn) == {4: None, 5: None}
    assert actions.decide_burst(conn, 3, None) == []
    assert actions.decide_burst(conn, 2, "reject") == [2]  # a single photo is a burst of one
    assert actions.decide_burst(conn, 1, "reject") == []  # the app already rejects it on its own
    with pytest.raises(actions.CullError, match="only be rejected or cleared"):
        actions.decide_burst(conn, 4, "keep")
    with pytest.raises(actions.CullError, match="no photo"):
        actions.decide_burst(conn, 99, "reject")


def test_after_picks_the_apps_own_rejects_count_as_decided_automatically(folder):
    cfg, conn, _shoot, _top = folder
    assert actions.done_preview(conn, cfg, 1).open_bursts == 1  # frames 4 and 5 are open, nothing is kept
    actions.pick(conn, 5)
    actions.pick(conn, 4)  # moves the keep from 5 to 4
    preview = actions.done_preview(conn, cfg, 1)
    assert (preview.rejects, preview.undecided, preview.open_bursts) == (3, 0, 0)
    actions.done_culling(conn, cfg, 1, train=False)
    outcomes = {
        r["image_id"]: (r["final"], r["decided_by"], r["picked"]) for r in conn.execute("SELECT * FROM cull_outcomes")
    }
    assert outcomes[3] == ("reject", "accepted", 0)
    assert outcomes[4] == ("keep", "owner", 1)
    assert outcomes[5] == ("reject", "owner", 0)  # picked first, but the keep moved away
    m = actions.metrics(conn, 1)
    assert (m.automatic, m.changed) == (4, 0)  # photos 1, 2, 3 and 6 as the app sorted them


def test_done_culling_trashes_rejects_with_their_files_and_restores_them(folder):
    cfg, conn, shoot, top = folder
    actions.set_decision(conn, 6, "reject")  # the owner rejects a photo from auto-keep
    actions.set_decision(conn, 3, "keep")  # and rescues one from auto-reject
    preview = actions.done_preview(conn, cfg, 1)
    assert (preview.rejects, preview.undecided, preview.auto, preview.open_bursts) == (2, 2, 2, 0)  # 3 is kept
    assert preview.files == 3  # DSC_0001.NEF, its sidecar, DSC_0006.NEF
    with guard.watch(shoot, top) as seen:
        result = actions.done_culling(conn, cfg, 1, train=False)
    assert seen.violations() == []
    assert (result.trashed, result.files, result.kept) == (2, 3, 4)
    assert sorted(p.name for p in shoot.iterdir()) == [
        "DSC_0002.JPG",
        "DSC_0002.NEF",
        "DSC_0003.NEF",
        "DSC_0004.NEF",
        "DSC_0005.NEF",
    ]
    trash = top / f".Trash-{__import__('os').getuid()}"
    assert sorted(p.name for p in (trash / "files").iterdir()) == ["DSC_0001.NEF", "DSC_0001.NEF.pp3", "DSC_0006.NEF"]
    states = dict(conn.execute("SELECT name, state FROM images").fetchall())
    assert states["DSC_0001.NEF"] == "trashed" and states["DSC_0006.NEF"] == "trashed"
    outcomes = {r["image_id"]: (r["final"], r["decided_by"]) for r in conn.execute("SELECT * FROM cull_outcomes")}
    assert outcomes == {
        1: ("reject", "accepted"),
        2: ("keep", "accepted"),
        3: ("keep", "owner"),
        4: ("keep", "default"),
        5: ("keep", "default"),
        6: ("reject", "owner"),
    }
    m = actions.metrics(conn, 1)
    assert (m.photos, m.automatic, m.changed) == (6, 2, 2)  # one rescued, one rejected from auto-keep

    with guard.watch(shoot, top) as seen:
        assert actions.restore(conn, cfg, image_id=6) == 1
        assert actions.restore(conn, cfg, folder_id=1) == 2
    assert seen.violations() == []
    assert (shoot / "DSC_0001.NEF.pp3").read_text() == "[General]\nRank=0\n"
    assert not any((trash / "files").iterdir()) and not any((trash / "info").iterdir())
    assert conn.execute("SELECT decision FROM decisions WHERE image_id = 1").fetchone()[0] == "keep"
    with pytest.raises(actions.CullError, match="nothing"):
        actions.restore(conn, cfg, folder_id=1)


def test_trust_proposals_need_agreement_over_recent_folders(folder):
    _cfg, conn, _shoot, _top = folder
    conn.execute("UPDATE folders SET culled_at = 'now' WHERE id = 1")
    for i in range(25):
        conn.execute(
            "INSERT INTO cull_outcomes (folder_id, image_id, final, decided_by, suggestion, kind, at) "
            "VALUES (1, 1, ?, 'owner', 'reject', 'eyes', 'now')",
            ("reject" if i < 24 else "keep",),
        )
    proposals = actions.trust_proposals(conn)
    assert [p["kind"] for p in proposals] == ["eyes"] and proposals[0]["agreement"] == pytest.approx(0.96)
    piles.set_trust(conn, "eyes", "automatic")
    assert actions.trust_proposals(conn) == []


# --- the personal model ------------------------------------------------------------------------------------------


def add_outcomes(conn, folder_id, n, culled_at, *, noise=0):
    conn.execute(
        "INSERT OR IGNORE INTO folders (id, path, name, opened_at, culled_at) VALUES (?, ?, 'f', 'now', ?)",
        (folder_id, f"/f{folder_id}", culled_at),
    )
    conn.execute("UPDATE folders SET culled_at = ? WHERE id = ?", (culled_at, folder_id))
    for i in range(n):
        conn.execute(
            "INSERT INTO images (id, folder_id, name, kind, size, mtime_ns, quick_hash, scanned_at) "
            "VALUES (?, ?, ?, 'raw', 1, 0, 'q', 'now')",
            (folder_id * 1000 + i, folder_id, f"DSC_{i:04d}.NEF"),
        )
        blurry = i % 3 == 0
        final = "reject" if blurry != (i < noise) else "keep"
        features = {"subject_blur": 2.5 if blurry else 0.4, "faces": 1.0, "verdict_sharp": float(not blurry)}
        conn.execute(
            "INSERT INTO cull_outcomes (folder_id, image_id, final, decided_by, suggestion, kind, features, "
            "burst_id, at) VALUES (?, ?, ?, 'owner', 'keep', 'technical', ?, ?, 'now')",
            (folder_id, folder_id * 1000 + i, final, json.dumps(features), folder_id * 1000 + i),
        )


def test_the_model_waits_for_enough_decisions(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path)}})
    conn = catalog.connect(cfg.catalog_path)
    add_outcomes(conn, 1, 20, "2026-01-01")
    result = model_mod.train(conn, cfg)
    assert not result.promoted and "needs" in result.note


def test_the_model_learns_and_activates_when_it_agrees_better(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path)}})
    conn = catalog.connect(cfg.catalog_path)
    add_outcomes(conn, 1, 90, "2026-01-01")
    result = model_mod.train(conn, cfg)
    assert result.promoted and result.agreement is not None and result.baseline is not None
    assert result.agreement > result.baseline  # the suggestions said "keep" for everything
    name, clf = model_mod.active_model(conn) or ("", None)
    assert name == f"model:{result.model_id}"
    p_soft, p_sharp = model_mod.predict(clf, [{"subject_blur": 2.5, "faces": 1.0}, {"subject_blur": 0.4, "faces": 1.0}])
    assert p_soft > 0.8 > 0.2 > p_sharp
    first = result.model_id
    add_outcomes(conn, 2, 90, "2026-02-01")
    second = model_mod.train(conn, cfg)
    assert second.previous is not None and second.promoted
    assert first is not None
    model_mod.rollback(conn, first)
    assert model_mod.active_model(conn)[0] == f"model:{first}"  # type: ignore[index]
    assert Path(conn.execute("SELECT path FROM cull_models WHERE id = ?", (first,)).fetchone()[0]).is_file()


def test_a_model_trained_on_older_features_is_set_aside(tmp_path):
    cfg = config.parse({"paths": {"data": str(tmp_path)}})
    conn = catalog.connect(cfg.catalog_path)
    add_outcomes(conn, 1, 90, "2026-01-01")
    result = model_mod.train(conn, cfg)
    assert result.promoted and result.model_id is not None
    # A version from before ADR 0010 also used the rating.
    info = json.loads(conn.execute("SELECT info FROM cull_models WHERE id = ?", (result.model_id,)).fetchone()[0])
    info["features"] = [*model_mod.FEATURES[:25], "rating", *model_mod.FEATURES[25:]]
    conn.execute("UPDATE cull_models SET info = ? WHERE id = ?", (json.dumps(info), result.model_id))
    assert model_mod.active_model(conn) is None  # the rules sort until the next Done culling
    with pytest.raises(model_mod.OutdatedModel):
        model_mod.rollback(conn, result.model_id)
    add_outcomes(conn, 2, 90, "2026-02-01")
    retrained = model_mod.train(conn, cfg)
    assert retrained.promoted and retrained.previous is None  # the outdated version isn't compared
    assert model_mod.active_model(conn)[0] == f"model:{retrained.model_id}"  # type: ignore[index]
