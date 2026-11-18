import os
import stat
import sys
from pathlib import Path

import pytest

SAMPLES = Path(os.environ.get("RIE_SAMPLES", Path.home() / ".cache" / "rie" / "samples"))

# A stand-in for rawtherapee-cli: records its arguments and environment, then writes a tiny JPEG to the -o path,
# or exits with FAKE_RT_EXIT. `-v` prints a version banner, as the real CLI does. A shell wrapper starts it, because
# a shebang line cannot hold an interpreter path with spaces.
FAKE_RT = """\
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_RT_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"args": args, "RT_SETTINGS": os.environ.get("RT_SETTINGS")}) + "\\n")
if args == ["-v"]:
    print("RawTherapee, version 5.13, command line.")
    sys.exit(255)
code = int(os.environ.get("FAKE_RT_EXIT", "0"))
if code:
    print("Error: something went wrong")
    sys.exit(code)
out = args[args.index("-o") + 1]
with open(out, "wb") as fh:
    fh.write(bytes([0xFF, 0xD8, 0xFF, 0xD9]))
"""


@pytest.fixture
def fake_rt(tmp_path, monkeypatch):
    """Path of a fake rawtherapee-cli; calls are logged as JSON lines to the returned log file."""
    script = tmp_path / "fake_rawtherapee.py"
    script.write_text(FAKE_RT, encoding="utf-8")
    cli = tmp_path / "fake-rawtherapee-cli"
    cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "fake-rt.log"
    monkeypatch.setenv("FAKE_RT_LOG", str(log))
    monkeypatch.delenv("FAKE_RT_EXIT", raising=False)
    return cli, log


@pytest.fixture
def raw_file(tmp_path):
    """A read-only stand-in for a NEF (the fake CLI never reads it)."""
    raw = tmp_path / "photos" / "DSC_0001.NEF"
    raw.parent.mkdir()
    raw.write_bytes(b"not really a raw file")
    raw.chmod(0o444)
    return raw


def sample_nef() -> Path | None:
    """A real D7100 NEF for integration tests: RIE_SAMPLE_NEF, or the first NEF in the samples folder."""
    explicit = os.environ.get("RIE_SAMPLE_NEF")
    if explicit:
        return Path(explicit)
    found = sorted(SAMPLES.glob("*.NEF")) if SAMPLES.is_dir() else []
    return found[0] if found else None
