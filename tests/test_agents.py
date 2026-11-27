"""Phase 2: RawTherapee's white balance model, proposals and routing, the agents, harmonization and QA, on
synthetic data (no raw file, no RawTherapee)."""

from __future__ import annotations

import itertools
import math

import cv2
import numpy as np
import pytest

from rie.agents import baseline, c5, color, exposure, geometry, gravity, harmonize, qa, wb
from rie.agents import colorspace as cs
from rie.agents.context import Face, ImageContext
from rie.agents.linear import LinearRaw
from rie.agents.looks import NEUTRAL
from rie.agents.proposal import Proposal, blend
from rie.engine import pp3, wbmodel
from rie.engine.recipe import RecipeError, compile_profile, parse

D7100 = wbmodel.CameraModel.from_matrix([8322, -3112, -1047, -6367, 14342, 2179, -988, 1638, 6394])


# --- RawTherapee's white balance model -------------------------------------------------------------------------


@pytest.mark.parametrize(("temp", "green"), [(2800, 1.0), (3900, 1.05), (4300, 0.95), (5200, 1.0), (7500, 1.1)])
def test_temperature_and_multipliers_round_trip(temp, green):
    t, g = D7100.temperature(D7100.multipliers(temp, green))
    assert abs(t - temp) < 2
    assert abs(g - green) < 0.002


def test_d65_daylight_is_srgb_white_and_warmer_light_is_redder():
    assert np.allclose(wbmodel.temp_to_srgb(6504), 1.0, atol=0.03)
    warm, cool = D7100.illuminant(3000, 1.0), D7100.illuminant(7000, 1.0)
    assert warm[0] / warm[2] > cool[0] / cool[2]


def test_daylight_locus_formula_matches_d65():
    x, y = wbmodel.daylight_xy(6504)
    assert abs(x - 0.3127) < 0.001 and abs(y - 0.3291) < 0.001


def test_angular_error():
    assert wbmodel.angular_error(np.array([1, 1, 1]), np.array([2, 2, 2])) == pytest.approx(0, abs=1e-6)
    assert wbmodel.angular_error(np.array([1, 0, 0]), np.array([0, 1, 0])) == pytest.approx(90)


# --- recipes: the keys Phase 2 added ----------------------------------------------------------------------------


def test_tone_keys_compile_to_the_measured_pp3_keys():
    recipe = parse(
        {
            "schema": "rie.recipe/1",
            "params": {
                "tone": {"curve": [[0, 0], [0.46, 0.56], [1, 1]], "contrast": 5},
                "color": {"vibrance": 10, "protect_skin": True},
                "exposure": {"highlight_compression": 60, "highlight_threshold": 80},
            },
        }
    )
    profile = compile_profile(recipe)
    assert profile["Exposure"]["CurveMode"] == "Perceptual"
    assert profile["Exposure"]["Curve"] == "1;0;0;0.46;0.56;1;1;"
    assert profile["Exposure"]["Contrast"] == "5"
    assert profile["Exposure"]["HighlightComprThreshold"] == "80"
    assert profile["Vibrance"]["ProtectSkins"] == "true"
    assert pp3.parse(pp3.dump(profile)) == profile


@pytest.mark.parametrize(
    "tone",
    [
        {"curve": [[0, 0], [0.5, 0.4], [0.4, 0.6], [1, 1]]},
        {"curve": [[0, 0], [0.5, 0.6], [0.7, 0.5], [1, 1]]},
        {"curve": [[0.1, 0], [1, 1]]},
        {"curve": [[0, 0], [1, 1]], "match_camera": True},
        {"curve_mode": "neon"},
    ],
)
def test_bad_tone_parameters_are_refused(tone):
    with pytest.raises(RecipeError):
        parse({"schema": "rie.recipe/1", "params": {"tone": tone}})


def test_the_neutral_look_lifts_mid_grey_and_keeps_the_ends():
    tone = NEUTRAL.tone
    xs = np.linspace(0, 1, 201)
    ys = tone(xs)
    assert np.all(np.diff(ys) >= -1e-9)
    assert tone(np.array([0.46]))[0] == pytest.approx(0.56, abs=0.005)
    assert tone(np.array([0.0]))[0] == pytest.approx(0, abs=1e-6) and tone(np.array([1.0]))[0] == pytest.approx(1)
    parse({"schema": "rie.recipe/1", "params": NEUTRAL.params()})


# --- proposals and routing -------------------------------------------------------------------------------------


def test_halfway_is_half_in_mired_and_in_log_green():
    start = {"white_balance": {"temperature_k": 4000, "tint": 1.0}, "exposure": {"ev": 0.0}}
    end = {"white_balance": {"temperature_k": 6000, "tint": 1.2}, "exposure": {"ev": 1.0}}
    half = blend(start, end, 0.5)
    assert half["white_balance"]["temperature_k"] == pytest.approx(1e6 / ((250 + 1e6 / 6000) / 2))
    assert half["white_balance"]["tint"] == pytest.approx(math.sqrt(1.2))
    assert half["exposure"]["ev"] == pytest.approx(0.5)
    assert blend(start, end, 0.0) == start and blend(start, end, 1.0) == end


@pytest.mark.parametrize(
    ("confidence", "band", "review", "ev"),
    [(0.9, "apply", False, 1.0), (0.6, "conservative", True, 0.5), (0.3, "as_shot", True, None)],
)
def test_confidence_routes_a_proposal(confidence, band, review, ev):
    p = Proposal("exposure@1", {"exposure": {"ev": 1.0}}, confidence, "why", as_shot={"exposure": {"ev": 0.0}})
    routed = p.route()
    assert routed.band == band and routed.review is review
    assert routed.applied.get("exposure", {}).get("ev") == ev


def test_abstaining_and_unchanged_proposals_are_not_reviewed():
    abstain = Proposal("geometry@1", {"geometry": {"rotate_deg": 0.0}}, 0.0, "no lines", abstain=True)
    same = Proposal(
        "wb@1", {"white_balance": {"as_shot": True}}, 0.3, "camera", as_shot={"white_balance": {"as_shot": True}}
    )
    assert not abstain.route().review and not same.route().review
    flagged = Proposal("geometry@1", {"geometry": {"rotate_deg": 0.0}}, 0.9, "tilt", flags=["deliberate tilt?"])
    assert flagged.route().review


# --- colour helpers --------------------------------------------------------------------------------------------


def test_lab_of_white_and_srgb_round_trip():
    assert np.allclose(cs.lab(np.ones(3)), [100, 0, 0], atol=0.01)
    values = np.linspace(0, 1, 11, dtype=np.float32)
    assert np.allclose(cs.decode_srgb(cs.encode_srgb(values)), values, atol=1e-5)
    assert cs.luminance_of_lightness(float(cs.lightness(0.18))) == pytest.approx(0.18, rel=1e-6)


def test_cat16_degree_one_neutralizes_and_zero_keeps_the_cast():
    light = wbmodel.XYZ_FROM_SRGB @ wbmodel.temp_to_srgb(2800)
    full = cs.adapted_white(light, cs.D65_WHITE, 1.0)
    none = cs.adapted_white(light, cs.D65_WHITE, 0.0)
    assert np.allclose(full, light / light[1], atol=1e-9)
    assert np.allclose(none, cs.D65_WHITE / cs.D65_WHITE[1], atol=1e-9)


def test_warm_light_keeps_some_warmth():
    temp, _green = wb.keep_some_warmth(2800, 1.0, wb.warm_degree(2800))
    assert 2800 < temp < 3300
    assert wb.warm_degree(5000) == 1.0


# --- white balance on a synthetic scene ------------------------------------------------------------------------


def scene(
    temp: float, green: float, *, as_shot: tuple[float, float], colourful: float = 0.6, seed: int = 1
) -> LinearRaw:
    """A mosaic of grey-world surfaces under a known light, seen through the D7100's matrix."""
    rng = np.random.default_rng(seed)
    level = rng.uniform(0.1, 0.6, size=(24, 36, 1))
    tiles = level * rng.uniform(1 - colourful, 1 + colourful, size=(24, 36, 3))
    tiles /= tiles.reshape(-1, 3).mean(axis=0)  # the scene averages to grey
    srgb = cv2.resize(tiles.astype(np.float32) * 0.2, (720, 480), interpolation=cv2.INTER_NEAREST)
    camera = srgb @ D7100.cam_rgb.T.astype(np.float32)  # balanced camera RGB: white is (1, 1, 1)
    light = D7100.illuminant(temp, green)  # the camera's view of a white surface under the light
    rgb = (camera * light.astype(np.float32)).astype(np.float32)
    clipped = np.zeros(rgb.shape[:2], dtype=bool)
    return LinearRaw(rgb, clipped, D7100, D7100.multipliers(*as_shot), white=16383.0, black=0.0)


def test_the_white_balance_agent_finds_the_light_the_camera_missed():
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.linear = scene(4600, 1.0, as_shot=(6000, 1.0))
    p = wb.propose(ctx, keep_warm=False)
    estimate = p.evidence["estimate"]
    assert abs(wbmodel.mired(estimate["temp"]) - wbmodel.mired(4600)) < 12
    assert "temperature_k" in p.params["white_balance"]


def test_the_white_balance_agent_keeps_a_good_camera_white_balance():
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.linear = scene(5200, 1.0, as_shot=(5200, 1.0))
    p = wb.propose(ctx)
    assert p.params == {"white_balance": {"as_shot": True}}


# --- exposure ----------------------------------------------------------------------------------------------------


def test_exposure_brings_a_dark_face_into_range():
    before = np.full((400, 600, 3), 0.03, dtype=np.float32)
    before[120:280, 240:360] = 0.02  # the face, darker still
    ctx = ImageContext(name="x.NEF", path=None, meta={}, faces=[Face((0.4, 0.3, 0.2, 0.4), 0.95, True, False)])  # type: ignore[arg-type]
    ctx.before = before
    p = exposure.propose(ctx)
    assert p.params["exposure"]["ev"] > 1.0
    assert p.evidence["face_lightness_after"] >= exposure.FACE_RANGE[0] - 1


def test_exposure_leaves_a_face_in_range_and_compresses_new_highlights():
    before = np.full((400, 600, 3), 0.18, dtype=np.float32)
    before[:150] = 0.7  # a large bright sky that a push would clip
    ctx = ImageContext(name="x.NEF", path=None, meta={}, faces=[Face((0.4, 0.5, 0.15, 0.3), 0.9, True, False)])  # type: ignore[arg-type]
    ctx.before = before
    p = exposure.propose(ctx)
    assert abs(p.params["exposure"]["ev"]) <= 0.3
    predictor = exposure.Predictor(before, NEUTRAL)
    assert predictor.large_clipping(1.0) > exposure.CLIP_LIMIT


# --- colour ------------------------------------------------------------------------------------------------------


def test_muted_colours_get_vibrance_and_colourful_ones_none():
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.linear = scene(5200, 1.0, as_shot=(5200, 1.0), colourful=0.05)
    muted = color.propose(ctx, D7100.multipliers(5200, 1.0))
    assert muted.params["color"]["vibrance"] > 0 and muted.params["color"]["protect_skin"] is True
    lin = ctx.linear
    rgb = lin.rgb.copy()
    rgb[..., 0] *= np.where(np.arange(rgb.shape[1]) % 2 == 0, 3.0, 0.3)[None, :]
    ctx.linear = LinearRaw(rgb, lin.clipped, lin.camera, lin.as_shot, lin.white, lin.black)
    vivid = color.propose(ctx, D7100.multipliers(5200, 1.0))
    assert vivid.params["color"]["vibrance"] <= color.VIVID_CAP


# --- baseline ----------------------------------------------------------------------------------------------------


def test_noise_reduction_follows_the_effective_iso():
    assert baseline.noise_reduction(640) == (0, 0)
    assert baseline.noise_reduction(3200)[1] > baseline.noise_reduction(1600)[1] > 0
    ctx = ImageContext(name="x.NEF", path=None, meta={"iso": 800})  # type: ignore[arg-type]
    assert baseline.propose(ctx, 0.0).params == {}
    pushed = baseline.propose(ctx, 2.0)
    assert pushed.params["detail"]["nr_chroma"] > 0 and "effective 3200" in pushed.rationale


# --- geometry ----------------------------------------------------------------------------------------------------


def test_autofill_scale_and_visible_area():
    assert geometry.autofill_scale(0, 3, 2) == pytest.approx(1.0)
    assert geometry.autofill_scale(2, 3, 2) == pytest.approx(
        math.cos(math.radians(2)) + 1.5 * math.sin(math.radians(2))
    )
    assert geometry.visible((0.5, 0.5), 5, 3, 2)
    corner_face = Face((0.0, 0.0, 0.08, 0.1), 0.9, True, False)
    assert geometry.face_kept(corner_face, 0.0, 3, 2) is False or geometry.face_kept(corner_face, 3.0, 3, 2) is False


def test_geometry_measures_a_known_turn():
    grid = np.full((800, 1200), 160, dtype=np.uint8)
    for y in range(100, 800, 100):
        cv2.line(grid, (50, y), (1150, y), 30, 3)
    for x in range(150, 1200, 150):
        cv2.line(grid, (x, 50), (x, 750), 30, 3)
    turned = cv2.warpAffine(grid, cv2.getRotationMatrix2D((600, 400), -2.0, 1.0), (1200, 800), borderValue=160)
    roll, support, lines = geometry.estimate(turned)
    assert lines == "both" and support > geometry.MIN_SUPPORT
    assert roll == pytest.approx(2.0, abs=0.15)  # turned clockwise on screen: turn back counter-clockwise


def _turned_lines(horizontal: bool, vertical: bool) -> np.ndarray:
    """A linear render of dark lines on grey, turned 2 degrees clockwise."""
    grid = np.full((800, 1200), 160, dtype=np.uint8)
    for y in range(100, 800, 100) if horizontal else ():
        cv2.line(grid, (50, y), (1150, y), 30, 3)
    for x in range(150, 1200, 150) if vertical else ():
        cv2.line(grid, (x, 50), (x, 750), 30, 3)
    turned = cv2.warpAffine(grid, cv2.getRotationMatrix2D((600, 400), -2.0, 1.0), (1200, 800), borderValue=160)
    return cs.decode_srgb(np.repeat(turned[..., None], 3, axis=2) / 255.0).astype(np.float32)


def test_vertical_lines_alone_are_measured_but_not_acted_on():
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.before = _turned_lines(horizontal=False, vertical=True)
    p = geometry.propose(ctx)
    assert p.evidence["lines"] == "verticals" and p.evidence["roll"] == pytest.approx(2.0, abs=0.15)
    assert p.abstain and p.params == {"geometry": {"rotate_deg": 0.0}}
    assert not p.route().review  # nothing to review either
    ctx.before = _turned_lines(horizontal=True, vertical=False)
    p = geometry.propose(ctx)
    assert p.evidence["lines"] == "horizontals" and not p.abstain
    assert p.params["geometry"]["rotate_deg"] == pytest.approx(2.0, abs=0.15)


# --- learned estimators: C5 and GeoCalib (ADR 0017) ---------------------------------------------------------------


def _c5_reference_histogram(rgb: np.ndarray) -> np.ndarray:
    """C5's own code (src/ops.py: get_hist_colors and compute_histogram), literal and slow."""
    pixels = rgb.reshape(-1, 3).astype(np.float64)
    logs = np.log(pixels + 1e-9)
    chroma = np.stack([logs[:, 1] - logs[:, 0], logs[:, 1] - logs[:, 2]], axis=1)
    keep = pixels.sum(axis=1) > 1e-9
    chroma, colors = chroma[keep], pixels[keep]
    step = 5.7 / 63
    a_u = np.arange(-2.85, 2.85 + step / 2, step)
    a_v = np.flip(a_u)
    du = np.abs(chroma[:, :1] - a_u[None, :])
    dv = np.abs(chroma[:, 1:] - a_v[None, :])
    du[du > step] = 0
    du[du != 0] = 1
    dv[dv > step] = 0
    dv[dv != 0] = 1
    n = (np.sqrt((colors**2).sum(axis=1))[None, :] * dv.T) @ du
    return np.sqrt(n / (n.sum() + 1e-9))


def _c5_reference_edges(image: np.ndarray) -> np.ndarray:
    """C5's compute_edges, literal."""
    edges = np.zeros(image.shape)
    padded = cv2.copyMakeBorder(image, 1, 1, 1, 1, cv2.BORDER_REFLECT)
    for dx, dy in itertools.product([-1, 0, 1], repeat=2):
        if dx or dy:
            edges += np.abs(image - padded[1 + dx : image.shape[0] + 1 + dx, 1 + dy : image.shape[1] + 1 + dy, :])
    return edges / 8


def test_c5_histograms_match_c5s_own_code():
    image = np.random.default_rng(3).uniform(0, 1, size=(32, 48, 3)) ** 2
    image[:4, :4] = 0  # blocks left out (clipped or near black) are zero and do not count
    mine = c5.histogram(image)
    assert mine.shape == (64, 64, 2)
    assert np.allclose(mine[..., 0], _c5_reference_histogram(image), atol=1e-12)
    assert np.allclose(mine[..., 1], _c5_reference_histogram(_c5_reference_edges(image)), atol=1e-12)


def test_c5_reads_photos_spread_through_the_folder():
    assert c5.spread([1, 2, 3]) == [1, 2, 3]
    assert c5.spread(list(range(70))) == [5, 15, 25, 35, 45, 55, 65]


def test_white_balance_follows_c5_as_far_as_its_spread_allows(monkeypatch):
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.linear = scene(4600, 1.0, as_shot=(6000, 1.0))
    light = D7100.illuminant(3800, 1.0)
    monkeypatch.setattr(wb, "c5_estimate", lambda ctx: c5.Estimate(illuminant=light, spread=1.5, others=6))
    p = wb.propose(ctx, keep_warm=False)
    assert p.agent == "wb@3" and p.evidence["source"] == "c5" and p.band == "apply"
    assert abs(wbmodel.mired(p.evidence["estimate"]["temp"]) - wbmodel.mired(3800)) < 3
    assert abs(wbmodel.mired(p.evidence["classic"]["temp"]) - wbmodel.mired(4600)) < 12  # kept as evidence
    monkeypatch.setattr(wb, "c5_estimate", lambda ctx: c5.Estimate(illuminant=light, spread=5.0, others=6))
    unsure = wb.propose(ctx, keep_warm=False)
    assert unsure.band == "as_shot" and unsure.route().review  # left as shot and queued, with the reason
    assert "C5 unsure" in unsure.rationale
    monkeypatch.setattr(wb, "c5_estimate", lambda ctx: None)  # no weights: the classic estimate
    classic = wb.propose(ctx, keep_warm=False)
    assert classic.evidence["source"] == "classic"
    assert abs(wbmodel.mired(classic.evidence["estimate"]["temp"]) - wbmodel.mired(4600)) < 12


def test_geocalib_turns_a_photo_without_lines(monkeypatch):
    ctx = ImageContext(name="x.NEF", path=None, meta={"focal_length": 35.0})  # type: ignore[arg-type]
    ctx.before = np.full((800, 1200, 3), 0.2, dtype=np.float32)  # no lines at all
    monkeypatch.setattr(geometry, "gravity_estimate", lambda ctx: gravity.Gravity(1.4, 0.6, 2.0, 2340.0))
    p = geometry.propose(ctx)
    assert p.agent == "geometry@3" and p.evidence["lines"] == "none" and p.evidence["source"] == "geocalib"
    assert p.params == {"geometry": {"rotate_deg": 1.4}}
    assert p.band == "apply" and not p.route().review  # applied automatically (ADR 0017)
    ctx.faces = [Face(box=(0.35, 0.3, 0.3, 0.4), score=0.95, confirmed=True, cut_off=False)]
    assert geometry.propose(ctx).band == "conservative"  # a portrait's tilt may be deliberate
    ctx.faces = []
    monkeypatch.setattr(geometry, "gravity_estimate", lambda ctx: gravity.Gravity(1.4, 1.6, 2.0, None))
    unsure = geometry.propose(ctx)
    assert unsure.abstain and "GeoCalib is unsure" in unsure.rationale


def test_geocalib_confirms_a_cautious_roll_from_level_lines(monkeypatch):
    ctx = ImageContext(name="x.NEF", path=None, meta={})  # type: ignore[arg-type]
    ctx.before = _turned_lines(horizontal=True, vertical=False)
    monkeypatch.setattr(geometry, "gravity_estimate", lambda ctx: None)
    assert geometry.propose(ctx).band != "apply"  # horizontals alone are cautious
    monkeypatch.setattr(geometry, "gravity_estimate", lambda ctx: gravity.Gravity(2.1, 0.5, 0.0, None))
    confirmed = geometry.propose(ctx)
    assert confirmed.band == "apply" and confirmed.params["geometry"]["rotate_deg"] == pytest.approx(2.0, abs=0.15)
    monkeypatch.setattr(geometry, "gravity_estimate", lambda ctx: gravity.Gravity(-1.0, 0.5, 0.0, None))
    assert geometry.propose(ctx).band != "apply"  # disagreeing: still cautious


def test_geocalib_takes_the_gpu_only_with_room_to_spare(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert gravity.device() == "cpu"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (300 * 2**20, 4096 * 2**20))
    assert gravity.device() == "cpu"  # a busy GPU: the CPU
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (1600 * 2**20, 4096 * 2**20))
    assert gravity.device() == "cuda"


@pytest.mark.learned
@pytest.mark.skipif(not (c5.MODELS / c5.WEIGHTS).is_file(), reason="the C5 weights are not in models/")
def test_c5_estimates_the_light_of_a_synthetic_scene():
    lin = scene(4200, 1.0, as_shot=(5500, 1.0))
    e = c5.estimate(c5.histogram(c5.image_of(lin)), [])
    assert np.all(np.isfinite(e.illuminant)) and e.illuminant[1] == pytest.approx(1.0) and e.others == 0
    assert e.spread > 0


@pytest.mark.learned
@pytest.mark.skipif(not (gravity.MODELS / gravity.WEIGHTS).is_file(), reason="the GeoCalib weights are not in models/")
def test_geocalib_turns_the_same_way_as_the_lines():
    reading = gravity.estimate(_turned_lines(horizontal=True, vertical=True), 35.0)
    assert reading.turn == pytest.approx(2.0, abs=1.0)  # turned 2 degrees clockwise: turn back counter-clockwise
    assert reading.uncertainty < 2.0 and reading.focal_prior == pytest.approx(35.0 / 23.5 * 1200)
    assert reading.device in ("cpu", "cuda")
    gravity.release()
    assert gravity._model is None


# --- harmonization -----------------------------------------------------------------------------------------------


def _wb_proposal(temp: float, camera: float, confidence: float) -> Proposal:
    evidence = {"camera": {"temp": camera, "green": 1.0}, "final": {"temp": temp, "green": 1.0}}
    return Proposal(
        "wb@1",
        {"white_balance": {"temperature_k": temp, "tint": 1.0}},
        confidence,
        "x",
        as_shot={"white_balance": {"temperature_k": camera, "tint": 1.0}},
        evidence=evidence,
    )


def test_a_scene_gets_one_white_balance_unless_the_light_changed():
    frames = [
        harmonize.Frame("a", 1, _wb_proposal(4000, 4300, 0.7), None, {}),
        harmonize.Frame("b", 1, _wb_proposal(4100, 4300, 0.8), None, {}),
        harmonize.Frame("c", 1, _wb_proposal(5200, 4250, 0.95), None, {}),  # confident, but the camera saw no change
        harmonize.Frame("d", 2, _wb_proposal(6000, 6000, 0.9), None, {}),
    ]
    assert harmonize.harmonize(frames) == 1
    temps = {f.name: f.wb.params["white_balance"]["temperature_k"] for f in frames if f.wb}
    assert temps["a"] == temps["b"] == temps["c"] == 4100
    assert temps["d"] == 6000


def test_a_scene_gets_one_rendering_gain():
    def frame(name: str, shutter: float, ev: float) -> harmonize.Frame:
        p = Proposal("exposure@1", {"exposure": {"ev": ev}}, 0.7, "x", as_shot={"exposure": {"ev": 0.0}})
        return harmonize.Frame(name, 1, None, p, {"exposure_time": shutter, "iso": 800, "fnumber": 2.0})

    frames = [frame("a", 1 / 100, 1.0), frame("b", 1 / 100, 0.6), frame("c", 1 / 50, 0.2)]
    harmonize.harmonize(frames)
    evs = [f.exposure.params["exposure"]["ev"] for f in frames if f.exposure]
    assert evs[0] == evs[1]
    assert evs[2] == pytest.approx(evs[0] - 1.0)  # twice the light captured: one stop less push


# --- QA ----------------------------------------------------------------------------------------------------------


def test_qa_finds_a_clipped_face_crushed_shadows_and_gamut():
    render = np.full((300, 450, 3), 0.2, dtype=np.float32)
    face = Face((0.4, 0.3, 0.2, 0.4), 0.9, True, False)
    render[90:210, 180:270] = 1.0
    check = qa.face_check(render, [face], {"exposure": {"ev": 0.5}})
    assert not check.ok and check.fix["exposure"]["ev"] == pytest.approx(0.2)
    dark = render.copy()
    dark[:60] = 0.0
    assert not qa.shadow_check(dark, {}).ok
    vivid = render.copy()
    vivid[:30, :, :] = [0.9, 0.0, 0.0]
    gamut = qa.gamut_check(vivid, {"color": {"vibrance": 20}})
    assert not gamut.ok and gamut.fix == {"color": {"vibrance": 10}}
    assert qa.horizon_check(render, {}) is None
