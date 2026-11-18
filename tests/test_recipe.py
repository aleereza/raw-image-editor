import copy
import json

import pytest

from rie import config
from rie.engine import pp3
from rie.engine.recipe import RecipeError, base_profile_path, compile_profile, load, parse

# The example recipe from IMPLEMENTATION_PLAN.md §3.3, with the base profile this repository ships.
PLAN_EXAMPLE = {
    "schema": "rie.recipe/1",
    "image": "blake3:9f2c",
    "engine": {"name": "rawtherapee", "version": "5.13", "base_profile": "d7100-base@1"},
    "params": {
        "geometry": {"rotate_deg": -1.35, "crop": {"x": 112, "y": 80, "w": 5776, "h": 3851, "aspect": "3:2"}},
        "white_balance": {"temperature_k": 5450, "tint": 1.03},
        "exposure": {"ev": 0.45, "highlight_compression": 35, "shadows": 20},
        "color": {"vibrance": 12, "saturation": 0},
        "detail": {"nr_luma": 0, "nr_chroma": "auto", "capture_sharpening": True},
        "lens": {"profile": "lensfun-auto", "distortion": True, "vignetting": True, "ca": True},
    },
    "provenance": {
        "white_balance.temperature_k": {"source": "agent:wb@0.3.0", "confidence": 0.82, "why": "gray-edge"},
        "geometry.rotate_deg": {"source": "human", "locked": True},
    },
}

EXPECTED_PP3 = """\
[Rotation]
Degree=-1.35

[Crop]
Enabled=true
X=112
Y=80
W=5776
H=3851
FixedRatio=false

[White Balance]
Setting=Custom
Temperature=5450
Green=1.03

[Exposure]
Compensation=0.45
HighlightCompr=35
Saturation=0

[Shadows & Highlights]
Enabled=true
Shadows=20

[Vibrance]
Enabled=true
Pastels=12
Saturated=12
PastSatTog=true
"""


def with_params(**groups):
    data = copy.deepcopy(PLAN_EXAMPLE)
    data["params"] = groups
    return data


def test_plan_example_compiles_to_the_confirmed_keys():
    profile = compile_profile(parse(PLAN_EXAMPLE))
    expected = pp3.parse(EXPECTED_PP3)
    for section, keys in expected.items():
        assert {k: profile[section][k] for k in keys} == keys, section
    assert profile["LensProfile"] == {
        "LcMode": "lfauto",
        "UseDistortion": "true",
        "UseVignette": "true",
        "UseCA": "true",
    }
    assert profile["Directional Pyramid Denoising"] == {
        "Enabled": "true",
        "Luma": "0",
        "CMethod": "AUT",
        "C2Method": "AUTO",
    }
    assert profile["PostDemosaicSharpening"] == {"Enabled": "true"}


def test_load_reads_json(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(PLAN_EXAMPLE), encoding="utf-8")
    assert load(path).engine.base_profile == "d7100-base@1"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RecipeError, match="not valid JSON"):
        load(path)


def test_empty_params_compile_to_an_empty_profile():
    assert compile_profile(parse({"schema": "rie.recipe/1"})) == {}


def test_as_shot_white_balance():
    profile = compile_profile(parse(with_params(white_balance={"as_shot": True})))
    assert profile == {"White Balance": {"Setting": "Camera"}}


def test_values_are_rounded_for_the_profile():
    recipe = parse(with_params(white_balance={"temperature_k": 5450.6, "tint": 1.034567}, exposure={"ev": 0.33333}))
    profile = compile_profile(recipe)
    assert profile["White Balance"]["Temperature"] == "5451"
    assert profile["White Balance"]["Green"] == "1.0346"
    assert profile["Exposure"]["Compensation"] == "0.333"


def test_zero_shadows_and_vibrance_disable_their_tools():
    profile = compile_profile(parse(with_params(exposure={"shadows": 0}, color={"vibrance": 0})))
    assert profile["Shadows & Highlights"]["Enabled"] == "false"
    assert profile["Vibrance"]["Enabled"] == "false"


def test_manual_chroma_noise_reduction_and_rcd():
    profile = compile_profile(parse(with_params(detail={"nr_chroma": 20, "demosaic": "rcd"})))
    assert profile["Directional Pyramid Denoising"]["CMethod"] == "MAN"
    assert profile["Directional Pyramid Denoising"]["Chroma"] == "20"
    assert profile["RAW Bayer"] == {"Method": "rcd"}


def test_lens_profile_none():
    assert compile_profile(parse(with_params(lens={"profile": "none"}))) == {"LensProfile": {"LcMode": "none"}}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"schema": "rie.recipe/2"}, "schema"),
        ({"schema": "rie.recipe/1", "extra": 1}, "unknown top-level"),
        (with_params(exposure={"evv": 1}), "unknown parameter exposure.evv"),
        (with_params(sharpness={"amount": 1}), "unknown parameter group"),
        (with_params(exposure={"ev": 13}), "within"),
        (with_params(exposure={"ev": "1"}), "must be a number"),
        (with_params(exposure={"ev": True}), "must be a number"),
        (with_params(exposure={"highlight_compression": 1.5}), "whole number"),
        (with_params(white_balance={"temperature_k": 5000}), "needs both"),
        (with_params(white_balance={"as_shot": True, "tint": 1.0}), "cannot be combined"),
        (with_params(lens={"profile": "lensfun"}), "must be one of"),
        (with_params(lens={"ca": "yes"}), "true or false"),
        (with_params(geometry={"crop": {"x": 0, "y": 0, "w": 10}}), "missing h"),
        (with_params(geometry={"crop": {"x": 0, "y": 0, "w": 8, "h": 8}}), "at least 16"),
        (with_params(geometry={"crop": {"x": 0, "y": 0, "w": 4000, "h": 4000, "aspect": "3:2"}}), "not 3:2"),
        (with_params(geometry={"crop": {"x": 0, "y": 0, "w": 300, "h": 200, "aspect": "wide"}}), "look like"),
        (with_params(geometry={"crop": {"x": 0, "y": 0, "w": 300, "h": 200, "aspect": "0:2"}}), "look like"),
        ({**PLAN_EXAMPLE, "engine": {"name": "darktable"}}, "engine.name"),
        ({**PLAN_EXAMPLE, "engine": {"base_profile": "d7100-base"}}, "base_profile"),
    ],
)
def test_invalid_recipes_are_rejected(data, message):
    with pytest.raises(RecipeError, match=message):
        parse(data)


def test_portrait_crop_matches_aspect_by_long_side():
    parse(with_params(geometry={"crop": {"x": 0, "y": 0, "w": 2000, "h": 3000, "aspect": "3:2"}}))


def test_base_profile_must_exist_and_declare_its_version(tmp_path):
    recipe = parse(PLAN_EXAMPLE)
    assert base_profile_path(recipe, config.PROFILES_DIR).name == "d7100-base.pp3"
    with pytest.raises(RecipeError, match="not found"):
        base_profile_path(recipe, tmp_path)
    (tmp_path / "d7100-base.pp3").write_text("# rie base profile d7100-base@2\n[Exposure]\nAuto=false\n")
    with pytest.raises(RecipeError, match="declares d7100-base@2"):
        base_profile_path(recipe, tmp_path)


def test_shipped_base_profile_targets_rawtherapee_5_13():
    profile = pp3.load(config.PROFILES_DIR / "d7100-base.pp3")
    assert profile["Version"] == {"AppVersion": "5.13", "Version": "353"}
    assert profile["Color Management"]["InputProfile"] == "(cameraICC)"
    assert profile["Color Management"]["WorkingProfile"] == "Rec2020"
    assert profile["LensProfile"]["LcMode"] == "lfauto"
