"""Download the model files listed in models/manifest.toml and check their SHA-256.

Usage: uv run python scripts/fetch_models.py
"""

from __future__ import annotations

import hashlib
import sys
import tomllib
import urllib.request
from pathlib import Path

MODELS = Path(__file__).resolve().parents[1] / "models"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    manifest = tomllib.loads((MODELS / "manifest.toml").read_text(encoding="utf-8"))
    failures = 0
    for name, entry in manifest.items():
        path = MODELS / entry["file"]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + ".part")
            print(f"{name}: downloading {entry['source']}")
            urllib.request.urlretrieve(entry["source"], partial)
            partial.replace(path)
        ok = sha256(path) == entry["sha256"]
        print(f"{name}: {path.relative_to(MODELS)} {'ok' if ok else 'SHA-256 MISMATCH'} ({entry['license']})")
        failures += not ok
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
