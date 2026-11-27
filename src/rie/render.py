"""Render one raw file from a recipe with rawtherapee-cli: base profile, compiled per-image profile, optional size.

`cached_render` keeps renders in `<cache>/renders/cache/`, named by a hash of everything that decides the pixels:
the raw file's quick hash, every stacked profile's text, the RawTherapee version and the JPEG options. The same
recipe on the same file is rendered once. Edited previews use the fast preview overlay (plan §6): no capture
sharpening, no raw chromatic aberration correction and no noise reduction, which matter only at close zoom.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from dataclasses import dataclass
from functools import cache
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


# The fast preview profile (plan §6): ADR 0001 measured what each step costs; exports never use it.
PREVIEW_OVERLAY: pp3.Profile = pp3.stack(
    {
        "PostDemosaicSharpening": {"Enabled": False},
        "RAW": {"CA": False},
        "Directional Pyramid Denoising": {"Enabled": False},
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


@cache
def _engine_version(cli: str, settings_dir: Path) -> str:
    return RawTherapee(cli=cli, settings_dir=settings_dir).version()


def render_key(image_key: str, profiles: list[str], version: str, quality: int, subsampling: int) -> str:
    """The cache name of a render: what decides its pixels, hashed."""
    digest = hashlib.sha256()
    for part in (image_key, version, f"q{quality}s{subsampling}", *profiles):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def cached_render(
    raw: Path,
    recipe: Recipe,
    cfg: Config,
    *,
    image_key: str,
    size: int | None = None,
    preview: bool = False,
) -> RenderResult:
    """Render `raw` with `recipe` into the render cache, or return the cached render of the same profiles.

    `image_key` names the raw file's content (its quick hash). `preview` stacks the fast preview overlay.
    """
    base = base_profile_path(recipe, cfg.rawtherapee.profiles_dir)
    texts = [
        base.read_text(encoding="utf-8"),
        pp3.dump(compile_profile(recipe), header=[f"rie per-image profile for {raw.name}"]),
    ]
    if preview:
        texts.append(pp3.dump(PREVIEW_OVERLAY, header=["rie fast preview overlay"]))
    if size is not None:
        texts.append(pp3.dump(size_overlay(size), header=[f"rie output size: long edge {size} px"]))
    rt = engine(cfg)
    quality, subsampling = cfg.render.jpeg_quality, cfg.render.jpeg_subsampling
    key = render_key(image_key, texts, _engine_version(rt.cli, rt.settings_dir), quality, subsampling)
    output = cfg.paths.cache / "renders" / "cache" / key[:2] / f"{key}.jpg"
    if output.is_file():
        return RenderResult(output=output, seconds=0.0, command=())
    tmp_root = cfg.paths.cache / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root, prefix="render-") as tmp:
        profiles = []
        for index, text in enumerate(texts[1:], start=1):
            path = Path(tmp) / f"{index}.pp3"
            path.write_text(text, encoding="utf-8")
            profiles.append(path)
        return rt.render(raw, [base, *profiles], output, quality=quality, subsampling=subsampling)
