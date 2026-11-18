"""Render one raw file from a recipe with rawtherapee-cli: base profile, compiled per-image profile, optional size."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .engine import pp3
from .engine.rawtherapee import RawTherapee, RenderResult
from .engine.recipe import Recipe, base_profile_path, compile_profile


@dataclass(frozen=True)
class RenderPlan:
    """What a render will run: the profiles in stacking order and their text, for `--dry-run` and logs."""

    base: Path
    image_profile: str
    size_profile: str | None
    command: list[str]


def size_overlay(long_edge: int) -> pp3.Profile:
    """Resize so the long edge is `long_edge` pixels, never upscaling (confirmed keys: ADR 0001)."""
    return pp3.stack(
        {
            "Resize": {
                "Enabled": True,
                "AppliesTo": "Cropped area",
                "Method": "Lanczos",
                "DataSpecified": 4,
                "LongEdge": long_edge,
                "AllowUpscaling": False,
            }
        }
    )


def engine(cfg: Config) -> RawTherapee:
    return RawTherapee(
        cli=cfg.rawtherapee.cli, settings_dir=cfg.rawtherapee_settings_dir, timeout=cfg.rawtherapee.timeout
    )


def default_output(raw: Path, cfg: Config) -> Path:
    """Renders go to the cache by default, never beside the original."""
    return cfg.paths.cache / "renders" / f"{raw.stem}.jpg"


def render(
    raw: Path,
    recipe: Recipe,
    output: Path,
    cfg: Config,
    *,
    size: int | None = None,
    profile_out: Path | None = None,
    dry_run: bool = False,
) -> RenderResult | RenderPlan:
    """Render `raw` with `recipe` to the JPEG `output`, or with `dry_run` only say what would run."""
    base = base_profile_path(recipe, cfg.rawtherapee.profiles_dir)
    rt = engine(cfg)
    header = [f"rie per-image profile for {raw.name}, on top of {recipe.engine.base_profile}"]
    image_text = pp3.dump(compile_profile(recipe), header=header)
    size_text = pp3.dump(size_overlay(size), header=[f"rie output size: long edge {size} px"]) if size else None
    if profile_out is not None:
        profile_out.parent.mkdir(parents=True, exist_ok=True)
        profile_out.write_text(image_text, encoding="utf-8")

    tmp_root = cfg.paths.cache / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root, prefix="render-") as tmp:
        image_profile = Path(tmp) / f"{raw.name}.pp3"
        image_profile.write_text(image_text, encoding="utf-8")
        profiles = [base, image_profile]
        if size_text is not None:
            size_profile = Path(tmp) / "size.pp3"
            size_profile.write_text(size_text, encoding="utf-8")
            profiles.append(size_profile)
        quality, subsampling = cfg.render.jpeg_quality, cfg.render.jpeg_subsampling
        if dry_run:
            command = rt.command(raw, profiles, output, quality=quality, subsampling=subsampling)
            return RenderPlan(base=base, image_profile=image_text, size_profile=size_text, command=command)
        if recipe.engine.version is not None:
            installed = rt.version()
            if installed != recipe.engine.version:
                print(
                    f"warning: the recipe was made for RawTherapee {recipe.engine.version}, this is {installed}",
                    file=sys.stderr,
                )
        return rt.render(raw, profiles, output, quality=quality, subsampling=subsampling)
