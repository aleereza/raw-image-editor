"""Recipes: the engine-neutral edit of one image (schema `rie.recipe/1`), and their compilation to a partial `.pp3`.

A recipe names a base profile (`engine.base_profile`, such as `d7100-base@1`) and holds parameters by group.
Compilation turns the parameters into the per-image partial profile that `rawtherapee-cli` stacks on the base.
Every parameter maps to `.pp3` keys confirmed against RawTherapee 5.13 in docs/adr/0001-rawtherapee-cli.md.
RawTherapee ignores unknown keys silently, so validation here is strict: an unknown or misspelt parameter is an error.

Conventions: `geometry.rotate_deg` is positive counter-clockwise, as RawTherapee's `[Rotation] Degree`;
`geometry.crop` is in pixels of the displayed (orientation-applied) frame after rotation; `white_balance.tint` is
RawTherapee's `Green` (above 1 renders greener).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pp3

SCHEMA = "rie.recipe/1"
DEFAULT_BASE_PROFILE = "d7100-base@1"
_PROFILE_ID = re.compile(r"([a-z0-9][a-z0-9-]*)@(\d+)")
_BASE_HEADER = re.compile(r"^#\s*rie base profile\s+(\S+)", re.M)


class RecipeError(ValueError):
    """Raised for a recipe that does not match the schema or names an unknown base profile."""


@dataclass(frozen=True)
class Number:
    low: float
    high: float
    integer: bool = False

    def check(self, where: str, value: Any) -> float | int:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise RecipeError(f"{where} must be a number, got {value!r}")
        if self.integer and value != int(value):
            raise RecipeError(f"{where} must be a whole number, got {value!r}")
        if not self.low <= value <= self.high:
            raise RecipeError(f"{where} must be within {self.low:g}..{self.high:g}, got {value!r}")
        return int(value) if self.integer else float(value)


@dataclass(frozen=True)
class Choice:
    options: tuple[str, ...]

    def check(self, where: str, value: Any) -> str:
        if value not in self.options:
            raise RecipeError(f"{where} must be one of {', '.join(self.options)}; got {value!r}")
        return value


@dataclass(frozen=True)
class Flag:
    def check(self, where: str, value: Any) -> bool:
        if not isinstance(value, bool):
            raise RecipeError(f"{where} must be true or false, got {value!r}")
        return value


@dataclass(frozen=True)
class NumberOrAuto:
    number: Number

    def check(self, where: str, value: Any) -> float | int | str:
        return "auto" if value == "auto" else self.number.check(where, value)


@dataclass(frozen=True)
class CropBox:
    def check(self, where: str, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RecipeError(f"{where} must be an object with x, y, w, h")
        unknown = set(value) - {"x", "y", "w", "h", "aspect"}
        if unknown:
            raise RecipeError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}")
        missing = {"x", "y", "w", "h"} - set(value)
        if missing:
            raise RecipeError(f"{where} is missing {', '.join(sorted(missing))}")
        box: dict[str, Any] = {
            k: int(Number(0, 100_000, integer=True).check(f"{where}.{k}", value[k])) for k in ("x", "y", "w", "h")
        }
        if box["w"] < 16 or box["h"] < 16:
            raise RecipeError(f"{where} must be at least 16 x 16 pixels")
        if "aspect" in value:
            aspect = value["aspect"]
            match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", str(aspect))
            sides = (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)
            if min(sides) <= 0:
                raise RecipeError(f"{where}.aspect must look like '3:2', got {aspect!r}")
            ratio = max(sides) / min(sides)
            long_side, short_side = max(box["w"], box["h"]), min(box["w"], box["h"])
            if abs(long_side - ratio * short_side) > ratio + 1:  # allow a pixel of rounding on the short side
                raise RecipeError(f"{where} is {box['w']} x {box['h']}, which is not {aspect}")
            box["aspect"] = str(aspect)
        return box


type Spec = Number | Choice | Flag | NumberOrAuto | CropBox

PARAMS: dict[str, dict[str, Spec]] = {
    "white_balance": {
        "as_shot": Flag(),
        "temperature_k": Number(1500, 60000),
        "tint": Number(0.02, 10.0),
    },
    "exposure": {
        "ev": Number(-5, 12),
        "highlight_compression": Number(0, 500, integer=True),
        "shadows": Number(0, 100, integer=True),
    },
    "color": {
        "vibrance": Number(-100, 100, integer=True),
        "saturation": Number(-100, 100, integer=True),
    },
    "geometry": {
        "rotate_deg": Number(-45, 45),
        "crop": CropBox(),
    },
    "lens": {
        "profile": Choice(("lensfun-auto", "none")),
        "distortion": Flag(),
        "vignetting": Flag(),
        "ca": Flag(),
    },
    "detail": {
        "demosaic": Choice(("amaze", "rcd")),
        "capture_sharpening": Flag(),
        "nr_luma": Number(0, 100),
        "nr_chroma": NumberOrAuto(Number(0, 100)),
    },
}


@dataclass(frozen=True)
class Engine:
    name: str = "rawtherapee"
    version: str | None = None
    base_profile: str = DEFAULT_BASE_PROFILE


@dataclass(frozen=True)
class Recipe:
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    engine: Engine = field(default_factory=Engine)
    image: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


def parse(data: Any) -> Recipe:
    """Validate a decoded recipe and return it with normalized values."""
    if not isinstance(data, dict):
        raise RecipeError("a recipe must be a JSON object")
    unknown = set(data) - {"schema", "image", "engine", "params", "provenance"}
    if unknown:
        raise RecipeError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    if data.get("schema") != SCHEMA:
        raise RecipeError(f"schema must be {SCHEMA!r}, got {data.get('schema')!r}")

    engine_data = data.get("engine", {})
    if not isinstance(engine_data, dict) or set(engine_data) - {"name", "version", "base_profile"}:
        raise RecipeError("engine must be an object with name, version and base_profile")
    engine = Engine(
        name=engine_data.get("name", "rawtherapee"),
        version=engine_data.get("version"),
        base_profile=engine_data.get("base_profile", DEFAULT_BASE_PROFILE),
    )
    if engine.name != "rawtherapee":
        raise RecipeError(f"engine.name must be 'rawtherapee', got {engine.name!r}")
    if engine.version is not None and not isinstance(engine.version, str):
        raise RecipeError("engine.version must be a string such as '5.13'")
    if not isinstance(engine.base_profile, str) or not _PROFILE_ID.fullmatch(engine.base_profile):
        raise RecipeError(f"engine.base_profile must look like 'd7100-base@1', got {engine.base_profile!r}")

    image = data.get("image")
    if image is not None and not isinstance(image, str):
        raise RecipeError("image must be a string such as 'blake3:<hex>'")
    provenance = data.get("provenance", {})
    if not isinstance(provenance, dict):
        raise RecipeError("provenance must be an object")

    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raise RecipeError("params must be an object")
    params: dict[str, dict[str, Any]] = {}
    for group, values in raw_params.items():
        specs = PARAMS.get(group)
        if specs is None:
            raise RecipeError(f"unknown parameter group {group!r}; known: {', '.join(PARAMS)}")
        if not isinstance(values, dict):
            raise RecipeError(f"params.{group} must be an object")
        checked = {}
        for name, value in values.items():
            spec = specs.get(name)
            if spec is None:
                raise RecipeError(f"unknown parameter {group}.{name}; known: {', '.join(specs)}")
            checked[name] = spec.check(f"params.{group}.{name}", value)
        params[group] = checked

    wb = params.get("white_balance", {})
    if wb.get("as_shot"):
        if set(wb) - {"as_shot"}:
            raise RecipeError("white_balance.as_shot cannot be combined with temperature_k or tint")
    elif wb and not {"temperature_k", "tint"} <= set(wb):
        raise RecipeError("a custom white_balance needs both temperature_k and tint (or as_shot: true)")
    return Recipe(params=params, engine=engine, image=image, provenance=provenance)


def load(path: Path) -> Recipe:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeError(f"{path}: not valid JSON: {exc}") from exc
    return parse(data)


def compile_profile(recipe: Recipe) -> pp3.Profile:
    """The partial profile for this recipe, to stack on its base profile."""
    out: dict[str, dict[str, pp3.Value]] = {}

    def put(section: str, key: str, value: pp3.Value) -> None:
        out.setdefault(section, {})[key] = value

    p = recipe.params
    wb = p.get("white_balance", {})
    if wb.get("as_shot"):
        put("White Balance", "Setting", "Camera")
    elif wb:
        put("White Balance", "Setting", "Custom")
        put("White Balance", "Temperature", round(wb["temperature_k"]))
        put("White Balance", "Green", round(wb["tint"], 4))

    exposure = p.get("exposure", {})
    if "ev" in exposure:
        put("Exposure", "Compensation", round(exposure["ev"], 3))
    if "highlight_compression" in exposure:
        put("Exposure", "HighlightCompr", exposure["highlight_compression"])
    if "shadows" in exposure:
        put("Shadows & Highlights", "Enabled", exposure["shadows"] > 0)
        put("Shadows & Highlights", "Shadows", exposure["shadows"])

    color = p.get("color", {})
    if "vibrance" in color:
        put("Vibrance", "Enabled", color["vibrance"] != 0)
        put("Vibrance", "Pastels", color["vibrance"])
        put("Vibrance", "Saturated", color["vibrance"])
        put("Vibrance", "PastSatTog", True)
    if "saturation" in color:
        put("Exposure", "Saturation", color["saturation"])

    geometry = p.get("geometry", {})
    if "rotate_deg" in geometry:
        put("Rotation", "Degree", round(geometry["rotate_deg"], 3))
    if "crop" in geometry:
        box = geometry["crop"]
        put("Crop", "Enabled", True)
        for key in ("x", "y", "w", "h"):
            put("Crop", key.upper(), box[key])
        put("Crop", "FixedRatio", False)

    lens = p.get("lens", {})
    if "profile" in lens:
        put("LensProfile", "LcMode", "lfauto" if lens["profile"] == "lensfun-auto" else "none")
    for name, key in (("distortion", "UseDistortion"), ("vignetting", "UseVignette"), ("ca", "UseCA")):
        if name in lens:
            put("LensProfile", key, lens[name])

    detail = p.get("detail", {})
    if "demosaic" in detail:
        put("RAW Bayer", "Method", detail["demosaic"])
    if "capture_sharpening" in detail:
        put("PostDemosaicSharpening", "Enabled", detail["capture_sharpening"])
    if "nr_luma" in detail or "nr_chroma" in detail:
        luma = detail.get("nr_luma", 0)
        chroma = detail.get("nr_chroma", 0)
        put("Directional Pyramid Denoising", "Enabled", luma > 0 or chroma == "auto" or chroma > 0)
        put("Directional Pyramid Denoising", "Luma", luma)
        if chroma == "auto":
            put("Directional Pyramid Denoising", "CMethod", "AUT")
            put("Directional Pyramid Denoising", "C2Method", "AUTO")
        else:
            put("Directional Pyramid Denoising", "CMethod", "MAN")
            put("Directional Pyramid Denoising", "C2Method", "MANU")
            put("Directional Pyramid Denoising", "Chroma", chroma)
    return pp3.stack(out)


def base_profile_path(recipe: Recipe, profiles_dir: Path) -> Path:
    """The base profile file the recipe names; its header must declare the same name and version."""
    name = recipe.engine.base_profile.split("@", 1)[0]
    path = profiles_dir / f"{name}.pp3"
    if not path.is_file():
        raise RecipeError(f"base profile {recipe.engine.base_profile} not found: {path}")
    header = _BASE_HEADER.search(path.read_text(encoding="utf-8"))
    declared = header.group(1) if header else None
    if declared != recipe.engine.base_profile:
        raise RecipeError(
            f"{path.name} declares {declared or 'no version'}, the recipe needs {recipe.engine.base_profile}"
        )
    return path


def describe(params: Mapping[str, Mapping[str, Any]]) -> str:
    """A one-line summary of the parameters, for logs."""
    return "; ".join(f"{g}: " + ", ".join(f"{k}={v}" for k, v in vals.items()) for g, vals in params.items()) or "none"
