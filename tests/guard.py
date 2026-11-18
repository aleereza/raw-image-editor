"""Watch a photo folder while code runs: only `rie.fileops` may create, change, rename or remove anything in it.

`watch(folder)` installs (once per process) a Python audit hook that records file events touching the folder or
its volume's trash, with the innermost `rie` source file on the stack. Opening for reading is fine; anything that
writes, renames or removes must come from `src/rie/fileops.py`. Subprocesses are recorded too, so a test can check
that only read-only tools ran. Events from worker processes are not seen, so pipelines run in-process under watch.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rie"
FILEOPS = SRC / "fileops.py"

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_PATH_EVENTS = {
    "os.remove": 1,
    "os.rmdir": 1,
    "os.rename": 2,
    "os.truncate": 1,
    "os.link": 2,
    "os.symlink": 2,
    "os.mkdir": 1,
    "os.chmod": 1,
    "os.chown": 1,
    "os.utime": 1,
    "shutil.rmtree": 1,
    "shutil.move": 2,
    "shutil.copyfile": 2,
    "shutil.copymode": 2,
    "shutil.copystat": 2,
}


@dataclass
class Event:
    name: str
    paths: list[str]
    origin: str | None  # the innermost src/rie file on the stack, if any
    detail: str = ""


@dataclass
class Watch:
    roots: list[str]
    events: list[Event] = field(default_factory=list)
    processes: list[list[str]] = field(default_factory=list)

    def violations(self) -> list[Event]:
        """Changes in the watched folders that did not come from rie.fileops."""
        return [e for e in self.events if e.origin != str(FILEOPS)]


_active: list[Watch] = []
_lock = threading.Lock()
_installed = False


def _as_path(value: object) -> str | None:
    if isinstance(value, int) or value is None:
        return None
    try:
        return os.path.abspath(os.fsdecode(value))  # type: ignore[arg-type]
    except TypeError:
        return None


def _origin() -> str | None:
    frame = sys._getframe(2)
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith(str(SRC)):
            return filename
        frame = frame.f_back
    return None


def _hook(event: str, args: tuple) -> None:
    if not _active:
        return
    paths: list[str] = []
    detail = ""
    if event == "open":
        path, mode, flags = [*args, None, None, None][:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        if not writing:
            return
        p = _as_path(path)
        paths = [p] if p else []
        detail = f"mode={mode} flags={flags}"
    elif event in _PATH_EVENTS:
        paths = [p for p in (_as_path(a) for a in args[: _PATH_EVENTS[event]]) if p]
    elif event == "rie.fileops":
        paths = [p for p in (_as_path(a) for a in args[1:]) if p]
        detail = str(args[0])
    elif event == "subprocess.Popen":
        with _lock:
            for watch in _active:
                watch.processes.append([os.fsdecode(a) for a in (args[1] or [])])
        return
    else:
        return
    if not paths:
        return
    with _lock:
        for watch in _active:
            if any(p == r or p.startswith(r + os.sep) for p in paths for r in watch.roots):
                watch.events.append(Event(event, paths, _origin(), detail))


@contextmanager
def watch(*folders: Path) -> Iterator[Watch]:
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
    current = Watch(roots=[os.path.abspath(f) for f in folders])
    _active.append(current)
    try:
        yield current
    finally:
        _active.remove(current)
