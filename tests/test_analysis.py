import shutil

import cv2
import numpy as np
import pytest

from rie import catalog, config
from rie.analysis import burst, faces, focus, grouping, sharpness
from tests.conftest import sample_nefs

RNG = np.random.default_rng(7)


def texture(h=640, w=960, noise=0.002) -> np.ndarray:
    """A linear 'green channel' full of sharp detail: random blocks of 2-6 pixels, plus a little noise."""
    small = RNG.uniform(0.05, 0.6, size=(h // 3, w // 3)).astype(np.float32)
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return np.clip(img + RNG.normal(0, noise, img.shape).astype(np.float32), 0, 1)


def with_flat_area(img: np.ndarray) -> np.ndarray:
    img = img.copy()
    img[:, : img.shape[1] // 4] = 0.3 + RNG.normal(0, 0.002, img[:, : img.shape[1] // 4].shape)
    return np.clip(img, 0, 1).astype(np.float32)


def edges(h=640, w=960, block=24, noise=0.002) -> np.ndarray:
    """Random flat patches with step edges between them, like most of a photo."""
    small = RNG.uniform(0.05, 0.6, size=(h // block, w // block)).astype(np.float32)
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return np.clip(img + RNG.normal(0, noise, img.shape).astype(np.float32), 0, 1)


def median_blur(img: np.ndarray) -> float:
    smap = sharpness.sharpness_map(img)
    return float(np.nanmedian(smap.blur[:, smap.blur.shape[1] // 3 :]))


def test_blur_estimates_a_gaussian_blur_on_edges():
    base = edges()
    for sigma in (1.0, 2.0, 3.0):
        assert median_blur(cv2.GaussianBlur(base, (0, 0), sigma)) == pytest.approx(sigma, rel=0.25)
    assert (
        median_blur(cv2.GaussianBlur(base, (0, 0), 0.5)) < focus.SHARP < median_blur(cv2.GaussianBlur(base, (0, 0), 2))
    )


def test_blur_grows_with_blur_on_fine_texture_too():
    base = with_flat_area(texture())
    values = [median_blur(cv2.GaussianBlur(base, (0, 0), s) if s else base) for s in (0, 1.0, 2.0, 3.0)]
    assert values == sorted(values)


def test_motion_blur_is_directional_along_the_motion():
    base = with_flat_area(texture())
    kernel = np.full((1, 9), 1 / 9, np.float32)  # horizontal motion
    moved = cv2.filter2D(base, -1, kernel)
    smap = sharpness.sharpness_map(moved)
    textured = ~np.isnan(smap.anisotropy)
    assert np.median(smap.anisotropy[textured]) > focus.DIRECTIONAL
    directions = smap.direction[textured & (smap.anisotropy > focus.DIRECTIONAL)]
    assert np.mean(directions == 0) > 0.8


def test_flat_and_clipped_areas_get_no_value():
    img = with_flat_area(texture())
    img[:, -200:] = 1.0  # blown highlights
    smap = sharpness.sharpness_map(img)
    cols = smap.blur.shape[1]
    assert np.isnan(smap.blur[:, : cols // 4 - 1]).mean() > 0.9
    assert np.isnan(smap.blur[:, -5:]).all()


def test_maps_round_trip_through_the_catalog_encoding():
    values = np.array([[0.5, np.nan], [1.25, 3.0]], np.float32)
    decoded = sharpness.decode_map(sharpness.encode_map(values), 2, 2)
    assert np.isnan(decoded[0, 1]) and decoded[1, 0] == pytest.approx(1.25)


# --- focus -----------------------------------------------------------------------------------------------------------


def test_af_layout_and_orientation():
    assert focus.af_point_position("C6") == (0.5, 0.5)
    left, right = focus.af_point_position("B1"), focus.af_point_position("B11")
    assert left is not None and right is not None and left[0] < 0.2 and right[0] > 0.8
    a1, b2 = focus.af_point_position("A1"), focus.af_point_position("B2")
    assert a1 is not None and b2 is not None
    assert a1 == pytest.approx((b2[0], 0.5 - 2 * focus.AF_PITCH_Y))  # A1 sits above B2
    assert focus.af_point_position("F1") is None and focus.af_point_position("C12") is None
    # A point right of centre in the sensor frame is above centre once the frame is turned for display.
    assert focus.to_upright(0.7, 0.5, 8) == pytest.approx((0.5, 0.3))
    assert focus.to_upright(0.7, 0.5, 6) == pytest.approx((0.5, 0.7))
    box = focus.af_box("C6", 8)
    assert box is not None and box[2] == pytest.approx(focus.AF_PITCH_Y)  # width and height swap in portrait


def smap_from(blur: np.ndarray, anisotropy: float = 1.0, direction: float = 0.0) -> sharpness.SharpnessMap:
    return sharpness.SharpnessMap(
        blur=blur.astype(np.float32),
        structure=np.ones_like(blur),
        direction=np.full(blur.shape, direction, np.float32),
        anisotropy=np.where(np.isnan(blur), np.nan, anisotropy).astype(np.float32),
        noise=1e-5,
    )


FACE = {"box": [0.4, 0.2, 0.2, 0.3], "eyes": [[0.45, 0.3], [0.55, 0.3]]}


def test_sharp_eyes_are_sharp():
    blur = np.full((30, 40), 2.0)
    blur[6:12, 16:24] = 0.4  # around the eyes
    result = focus.inspect(smap_from(blur), [FACE], {"af_primary": "C6", "focus_mode": "AF-S"}, 1)
    assert (result.verdict, result.subject) == ("sharp", "eyes")


def test_soft_eyes_with_a_sharp_background_is_a_missed_focus():
    blur = np.full((30, 40), 0.4)
    blur[3:14, 14:26] = 2.0  # the face is soft, everything else sharp
    result = focus.inspect(smap_from(blur), [FACE], {"af_primary": "C6", "focus_mode": "AF-S"}, 1)
    assert result.verdict == "missed"


def test_directional_blur_over_the_frame_is_shake_and_a_slow_shutter_is_motion():
    blur = np.full((30, 40), 1.5)
    assert focus.inspect(smap_from(blur, anisotropy=2.5), [], {}, 1).verdict == "shake"
    slow = {"focal_length": 35.0, "exposure_time": 1 / 15, "af_primary": "C6", "focus_mode": "AF-S"}
    assert focus.inspect(smap_from(blur), [], slow, 1).verdict == "motion"


def test_manual_focus_uses_the_sharpest_detail():
    blur = np.full((30, 40), np.nan)
    blur[20:22, 5:8] = 0.5
    result = focus.inspect(smap_from(blur), [], {"af_primary": "C6", "focus_mode": "Manual"}, 1)
    assert (result.subject, result.verdict) == ("frame", "sharp")


# --- grouping and bursts -----------------------------------------------------------------------------------------


def frame(i, t, *, layout=0.0, dhash=0, ev=9.0, flash=False, focal=35.0):
    return grouping.Frame(
        image_id=i,
        time=t,
        shutter=i,
        name=f"DSC_{i:04d}.NEF",
        focal=focal,
        fnumber=1.8,
        ev=ev,
        flash=flash,
        layout=np.full(192, layout, np.float32),
        dhash=dhash,
    )


def test_bursts_scenes_and_duplicates():
    frames = [
        frame(1, 0.0),
        frame(2, 0.2),
        frame(3, 0.4, layout=0.01),  # a burst of three, 1 and 2 near-identical
        frame(4, 5.0, layout=0.5, dhash=0xFFFF),  # a different look 5 s later: new burst, same scene
        frame(5, 9.0, layout=0.51, dhash=0xFFFE),  # the same shot again by hand: joins burst 4
        frame(6, 20.0, flash=True),  # flash on: new scene
        frame(7, 400.0, flash=True),  # long gap: new scene
    ]
    scenes = grouping.group(frames)
    assert [[[f.image_id for f in b.frames] for b in s.bursts] for s in scenes] == [[[1, 2, 3], [4, 5]], [[6]], [[7]]]
    assert (1, 2) in scenes[0].bursts[0].duplicates


def test_different_settings_break_a_burst():
    scenes = grouping.group([frame(1, 0.0), frame(2, 0.2, focal=100.0)])
    assert [len(s.bursts) for s in scenes] == [2]


def person(box, blink=0.05, smile=0.5, yaw=0.0, blur=0.3):
    return {"box": box, "blink_l": blink, "blink_r": blink, "smile": smile, "yaw": yaw, "pitch": 0.0, "sharpness": blur}


def test_a_relative_blink_loses_the_burst():
    a, b = [0.1, 0.1, 0.1, 0.1], [0.6, 0.1, 0.1, 0.1]
    frames = [
        burst.FrameInput(1, [person(a), person(b, blink=0.9)], 0.3, "sharp"),
        burst.FrameInput(2, [person(a), person(b)], 0.3, "sharp"),
        burst.FrameInput(3, [person(a, blink=0.45), person(b)], 0.3, "sharp"),  # laughing: not a blink
    ]
    ranks = burst.rank_burst(frames)
    assert [r.closed_eyes for r in ranks] == [1, 0, 0]
    assert min(ranks, key=lambda r: r.rank).image_id in (2, 3)
    assert ranks[0].reason == f"#{min(ranks, key=lambda r: r.rank).position} is better: 1 of 2 blinking"


def test_sharper_frames_win_when_people_are_alike():
    box = [0.4, 0.2, 0.2, 0.3]
    ranks = burst.rank_burst(
        [burst.FrameInput(1, [person(box, blur=1.2)], 1.2), burst.FrameInput(2, [person(box, blur=0.3)], 0.3)]
    )
    assert [r.rank for r in ranks] == [2, 1]
    assert "as sharp as the sharpest frame" in ranks[0].reason


def test_tracks_follow_faces_by_position():
    left, right = [0.1, 0.1, 0.1, 0.1], [0.7, 0.1, 0.1, 0.1]
    tracks = burst.track_faces(
        [burst.FrameInput(1, [person(left), person(right)], None), burst.FrameInput(2, [person(right)], None)]
    )
    assert tracks == [[0, 1], [1]]


# --- the real thing ------------------------------------------------------------------------------------------------

needs_samples = pytest.mark.skipif(
    len(sample_nefs(29)) < 20 or not faces.models_available() or shutil.which("exiftool") is None,
    reason="needs the sample NEFs, the face models and exiftool",
)


@needs_samples
def test_faces_on_a_group_shot_have_expressions_and_no_identity():
    from rie.scan import exif

    nef = next(p for p in sample_nefs(29) if p.name == "DSC_2656.NEF")
    record = next(exif.read_folder([nef]))
    start, length = exif.preview_ranges(record)["full"]
    img = faces.decode_upright(nef.read_bytes()[start : start + length], exif.orientation(record))
    found = faces.FaceAnalyzer().analyze(img, 35.0)
    assert len(found) >= 3
    for face in found:
        data = face.to_json()
        assert set(data) <= {
            "box", "score", "source", "landmarks", "confirmed", "blink_l", "blink_r", "smile", "yaw", "pitch",
            "roll", "eyes", "cut_off",
        }  # fmt: skip
        assert data["confirmed"] and 0 <= data["smile"] <= 1


@needs_samples
def test_open_folder_analyses_and_groups_a_burst_reading_only(tmp_path):
    from rie.pipeline import open_folder
    from tests import guard

    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("DSC_2680.NEF", "DSC_2681.NEF"):
        shutil.copyfile(next(p for p in sample_nefs(29) if p.name == name), shoot / name)
    cfg = config.parse({"paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")}})
    conn = catalog.connect(cfg.catalog_path)
    before = {p.name: p.stat().st_mtime_ns for p in shoot.iterdir()}
    with guard.watch(shoot) as seen:
        result = open_folder(conn, cfg, shoot, workers=1)
    assert seen.violations() == []
    assert {p.name: p.stat().st_mtime_ns for p in shoot.iterdir()} == before
    assert result.analysis is not None and (result.analysis.done, result.analysis.failed) == (2, 0)
    assert result.groups is not None and (result.groups.bursts, result.groups.in_bursts) == (1, 2)
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] >= 4
    hashes = [r[0] for r in conn.execute("SELECT content_hash FROM images")]
    assert all(h and h.startswith("sha256:") for h in hashes)
