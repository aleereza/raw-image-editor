"""RawTherapee processing profiles (`.pp3`): parse, write and stack.

A `.pp3` file is a GLib key file: `[Section]` headers, `Key=Value` lines and `#` comments. RawTherapee builds the
final parameters from neutral values and then applies each `-p` profile in order, so a partial profile holds only
the keys it changes. `Profile` keeps sections and keys in insertion order, which keeps written files diffable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path

type Value = str | int | float | bool
type Profile = dict[str, dict[str, str]]


class ProfileError(ValueError):
    """Raised for text that is not a valid key file."""


def format_value(value: Value) -> str:
    """Format a value the way RawTherapee writes it: lowercase booleans, plain decimals, no exponent."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProfileError(f"not a finite number: {value!r}")
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return "0" if text == "-0" else text
    if "\n" in value or "\r" in value:
        raise ProfileError(f"value spans lines: {value!r}")
    return value


def parse(text: str) -> Profile:
    """Parse key-file text into sections of string values; comments and blank lines are dropped."""
    profile: Profile = {}
    section: dict[str, str] | None = None
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = profile.setdefault(line[1:-1], {})
            continue
        key, sep, value = raw_line.partition("=")
        if not sep or section is None:
            raise ProfileError(f"line {number}: expected [Section] or Key=Value, got {raw_line!r}")
        section[key.strip()] = value.strip()
    return profile


def load(path: Path) -> Profile:
    return parse(path.read_text(encoding="utf-8"))


def dump(profile: Mapping[str, Mapping[str, Value]], header: Iterable[str] = ()) -> str:
    """Write sections as key-file text, with optional `#` comment lines first."""
    lines = [f"# {line}" if line else "#" for line in header]
    for name, keys in profile.items():
        if lines:
            lines.append("")
        lines.append(f"[{name}]")
        lines.extend(f"{key}={format_value(value)}" for key, value in keys.items())
    return "\n".join(lines) + "\n"


def stack(*profiles: Mapping[str, Mapping[str, Value]]) -> Profile:
    """Apply profiles in order, later keys winning, the way `rawtherapee-cli -p a -p b` combines them."""
    result: Profile = {}
    for profile in profiles:
        for name, keys in profile.items():
            section = result.setdefault(name, {})
            for key, value in keys.items():
                section[key] = format_value(value)
    return result
