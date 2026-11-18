"""Run `rawtherapee-cli` with stacked profiles and an app-owned settings folder.

RawTherapee reads its preferences (`options`) from the folder in `RT_SETTINGS`. The fast-export pipeline (`-f`)
and the default profile (`-d`) come from those preferences, so the app points `RT_SETTINGS` at its own folder and
never passes `-d`: a render depends only on the profiles given with `-p`. See docs/adr/0001-rawtherapee-cli.md.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# rawtherapee-cli exit codes seen with 5.13: 2 when the input is missing, 253 when a profile is missing or has a
# value it cannot read, 254 when the output cannot be saved.
EXIT_CODES = {2: "input file not found", 253: "processing profile missing or unreadable", 254: "could not save output"}


class RenderError(RuntimeError):
    """Raised when rawtherapee-cli fails or produces no output."""


@dataclass(frozen=True)
class RenderResult:
    output: Path
    seconds: float
    command: tuple[str, ...]


@dataclass(frozen=True)
class RawTherapee:
    cli: str = "rawtherapee-cli"
    settings_dir: Path = Path.home() / ".cache" / "rie" / "rawtherapee"
    timeout: float = 300.0

    def executable(self) -> str:
        path = shutil.which(self.cli)
        if path is None:
            raise RenderError(f"{self.cli} not found on PATH")
        return path

    def environment(self) -> dict[str, str]:
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["RT_SETTINGS"] = str(self.settings_dir)
        env["RT_CACHE"] = str(self.settings_dir / "cache")
        return env

    def version(self) -> str:
        """The version rawtherapee-cli reports, such as "5.13"."""
        # `-v` prints the banner and exits with a non-zero code; the banner is what matters.
        proc = subprocess.run(
            [self.executable(), "-v"], capture_output=True, text=True, env=self.environment(), timeout=60, check=False
        )
        match = re.search(r"version\s+([0-9][\w.\-]*)", proc.stdout + proc.stderr)
        if match is None:
            raise RenderError(f"could not read the version from {self.cli}: {proc.stdout!r}")
        return match.group(1).rstrip(",")

    def command(
        self,
        raw: Path,
        profiles: Sequence[Path],
        output: Path,
        *,
        quality: int = 95,
        subsampling: int = 3,
        fast: bool = False,
    ) -> list[str]:
        if not 1 <= quality <= 100:
            raise ValueError(f"JPEG quality must be 1-100, got {quality}")
        if subsampling not in (1, 2, 3):
            raise ValueError(f"JPEG subsampling must be 1, 2 or 3, got {subsampling}")
        cmd = [self.executable(), "-q", "-Y", "-o", str(output)]
        for profile in profiles:
            cmd += ["-p", str(profile)]
        cmd += [f"-j{quality}", f"-js{subsampling}"]
        if fast:
            cmd.append("-f")
        cmd += ["-c", str(raw)]  # -c must come last
        return cmd

    def render(
        self,
        raw: Path,
        profiles: Sequence[Path],
        output: Path,
        *,
        quality: int = 95,
        subsampling: int = 3,
        fast: bool = False,
    ) -> RenderResult:
        """Render `raw` to a JPEG at `output`, applying `profiles` in order on top of neutral values.

        The JPEG is written to a temporary name beside `output` and renamed when complete, so a failed or
        interrupted render never leaves a partial file. The raw file is only ever read.
        """
        raw = raw.resolve()
        output = output.resolve()
        if not raw.is_file():
            raise RenderError(f"raw file not found: {raw}")
        if output == raw or output.suffix.lower() not in (".jpg", ".jpeg"):
            raise RenderError(f"output must be a .jpg path other than the raw file: {output}")
        for profile in profiles:
            if not profile.is_file():
                raise RenderError(f"processing profile not found: {profile}")
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}.rie-partial.jpg")
        cmd = self.command(raw, profiles, partial, quality=quality, subsampling=subsampling, fast=fast)
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, env=self.environment(), timeout=self.timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            partial.unlink(missing_ok=True)
            raise RenderError(f"{self.cli} timed out after {self.timeout:.0f} s on {raw.name}") from exc
        seconds = time.perf_counter() - start
        if proc.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            reason = EXIT_CODES.get(proc.returncode, f"exit code {proc.returncode}")
            detail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
            raise RenderError(f"{self.cli} failed on {raw.name}: {reason}: {' | '.join(detail)}")
        partial.replace(output)
        return RenderResult(output=output, seconds=seconds, command=tuple(cmd))
