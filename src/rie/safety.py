"""Read-only safety checks: the trash's size, the drive's free space, and anything that empties the trash on its own.

Rejects stay in the photo drive's trash until the owner empties it (plan §3.5), so they keep using space there.
These checks report how much, warn before the drive runs low, and look for system settings or jobs that would
empty the trash behind the owner's back. Nothing here changes anything.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import fileops


@dataclass(frozen=True)
class SpaceReport:
    trash_path: Path
    trash_files: int
    trash_bytes: int
    free_bytes: int
    total_bytes: int
    min_free_bytes: int

    @property
    def low(self) -> bool:
        return self.free_bytes < self.min_free_bytes

    def describe(self) -> str:
        text = (
            f"trash {format_bytes(self.trash_bytes)} in {self.trash_files} files; "
            f"{format_bytes(self.free_bytes)} free of {format_bytes(self.total_bytes)}"
        )
        if self.low:
            text += f" (LOW: below {format_bytes(self.min_free_bytes)}; empty the trash when you are ready)"
        return text


def format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.2f} TB"


def space_report(topdir: Path, min_free_gb: float) -> SpaceReport:
    usage = fileops.trash_usage(topdir)
    return SpaceReport(
        trash_path=usage.path,
        trash_files=usage.files,
        trash_bytes=usage.bytes,
        free_bytes=usage.free_bytes,
        total_bytes=usage.total_bytes,
        min_free_bytes=int(min_free_gb * 1e9),
    )


def _run(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _grep(paths: list[Path], words: tuple[str, ...]) -> list[str]:
    hits = []
    for root in paths:
        if not root.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                path = Path(dirpath) / name
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if any(w in text for w in words):
                    hits.append(str(path))
    return hits


TRASH_WORDS = ("trash-empty", "gio trash --empty", "/.Trash", "Trash/files", ".local/share/Trash")


def trash_autoempty_findings(home: Path | None = None) -> list[str]:
    """Settings and scheduled jobs that may empty the trash automatically; empty when none were found."""
    home = home or Path.home()
    findings = []
    gnome = _run(["gsettings", "get", "org.gnome.desktop.privacy", "remove-old-trash-files"])
    if gnome is not None and gnome.strip() == "true":
        age = (_run(["gsettings", "get", "org.gnome.desktop.privacy", "old-files-age"]) or "").split()
        days = age[-1] if age else "?"
        findings.append(
            f"GNOME deletes trashed files older than {days} days (Settings > Privacy > File History & Trash)"
        )
    ktrash = home / ".config" / "ktrashrc"
    if ktrash.is_file():
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            parser.read(ktrash, encoding="utf-8")
        except configparser.Error:
            parser = configparser.ConfigParser()
        for section in parser.sections():
            if parser[section].get("UseTimeLimit", "false") == "true":
                findings.append(f"KDE empties trash {section} after a time limit ({ktrash})")
            if parser[section].get("UseSizeLimit", "false") == "true":
                findings.append(f"KDE trims trash {section} at a size limit ({ktrash})")
    crontab = _run(["crontab", "-l"])
    if crontab and any(w in crontab for w in TRASH_WORDS):
        findings.append("a cron job of yours mentions the trash (crontab -l)")
    for path in _grep(
        [
            home / ".config" / "systemd" / "user",
            Path("/etc/systemd/system"),
            Path("/etc/systemd/user"),
            Path("/etc/tmpfiles.d"),
            Path("/usr/lib/tmpfiles.d"),
            home / ".config" / "user-tmpfiles.d",
            Path("/etc/cron.d"),
            Path("/etc/cron.daily"),
            Path("/etc/cron.weekly"),
        ],
        TRASH_WORDS,
    ):
        findings.append(f"{path} mentions the trash")
    return findings
