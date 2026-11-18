import pytest

from rie.engine import pp3


def test_parse_keeps_order_and_drops_comments():
    text = "# a comment\n[White Balance]\nSetting=Custom\nTemperature=5450\n\n[Rotation]\nDegree=-1.35\n"
    profile = pp3.parse(text)
    assert list(profile) == ["White Balance", "Rotation"]
    assert profile["White Balance"] == {"Setting": "Custom", "Temperature": "5450"}
    assert profile["Rotation"]["Degree"] == "-1.35"


def test_values_may_contain_equals_and_semicolons():
    profile = pp3.parse("[Exposure]\nCurve=1;0;0;0.5=x;\n")
    assert profile["Exposure"]["Curve"] == "1;0;0;0.5=x;"


@pytest.mark.parametrize("text", ["Key=1\n", "[Section]\nnot a key\n"])
def test_parse_rejects_lines_outside_a_section_or_without_equals(text):
    with pytest.raises(pp3.ProfileError):
        pp3.parse(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (5450, "5450"),
        (5450.0, "5450"),
        (1.03, "1.03"),
        (-1.35, "-1.35"),
        (0.1 + 0.2, "0.3"),
        (-0.0000001, "0"),
        ("(cameraICC)", "(cameraICC)"),
    ],
)
def test_format_value(value, expected):
    assert pp3.format_value(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "two\nlines"])
def test_format_value_rejects_unwritable_values(value):
    with pytest.raises(pp3.ProfileError):
        pp3.format_value(value)


def test_dump_parse_round_trip_with_header():
    profile = {"White Balance": {"Setting": "Custom", "Temperature": 5450, "Green": 1.03}, "Crop": {"Enabled": True}}
    text = pp3.dump(profile, header=["made by a test"])
    assert text.startswith("# made by a test\n\n[White Balance]\n")
    assert pp3.parse(text) == pp3.stack(profile)


def test_stack_applies_later_profiles_on_top():
    base = {"Exposure": {"Compensation": 0, "HighlightCompr": 0}, "RAW Bayer": {"Method": "amaze"}}
    image = {"Exposure": {"Compensation": 0.45}, "Rotation": {"Degree": -1.35}}
    assert pp3.stack(base, image) == {
        "Exposure": {"Compensation": "0.45", "HighlightCompr": "0"},
        "RAW Bayer": {"Method": "amaze"},
        "Rotation": {"Degree": "-1.35"},
    }
