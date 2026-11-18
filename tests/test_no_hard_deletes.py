"""Plan §3.5: nothing but `rie.fileops` may delete, overwrite or rename files.

The static check lists every call in `src/rie` that can remove, rename or write a file, and fails on any that is
not in ALLOWED with a reason. All such calls outside `rie.fileops` write only the app's own cache, data folder or
temporary folders; a new one needs a reviewed entry here. The dynamic checks (in the pipeline tests) watch a photo
folder with an audit hook while the real code runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests import guard

SRC = guard.SRC

MODULE_CALLS = {
    "os": {"remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace", "truncate", "open", "link"},
    "shutil": {"rmtree", "move", "copy", "copy2", "copyfile", "copytree"},
    "send2trash": {"send2trash"},
    "tempfile": {"TemporaryDirectory", "NamedTemporaryFile", "mkstemp", "mkdtemp"},
    "cv2": {"imwrite"},
}
METHODS = {
    "unlink",
    "rmdir",
    "rename",
    "touch",
    "write_text",
    "write_bytes",
    "symlink_to",
    "hardlink_to",
    "write_to_file",
    "jpegsave",
    "pngsave",
    "save",
}
WRITE_MODES = set("wax+")

# (file relative to src/rie, enclosing function, operation) -> why it cannot touch a photo folder
ALLOWED: dict[tuple[str, str, str], str] = {
    ("catalog/__init__.py", "backup", ".unlink"): "prunes the app's own catalog backups in the data folder",
    ("render.py", "render", ".write_text"): "--profile-out and the per-image profiles in a temporary cache folder",
    ("render.py", "render", "tempfile.TemporaryDirectory"): "temporary profiles under <cache>/tmp",
    ("engine/rawtherapee.py", "RawTherapee.render", ".unlink"): "a failed render's partial file next to its output",
    ("scan/previews.py", "write_cached", ".write_bytes"): "cached previews, checked to be under <cache>",
    ("cull/model.py", "train", "open"): "saves a new culling model version under <data>/models",
    ("scan/previews.py", "write_cached", ".replace"): "renames a cached preview into place under <cache>",
    ("engine/rawtherapee.py", "RawTherapee.render", ".replace"): "renames a finished render into place; `rie render`"
    " refuses outputs inside the photos folder",
}


class Finder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}  # local name -> "module" or "module.function"
        self.found: list[tuple[str, str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if node.module:
                self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped  # type: ignore[assignment]

    def _add(self, op: str, node: ast.AST) -> None:
        self.found.append((".".join(self.scope) or "<module>", op, getattr(node, "lineno", 0)))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = self.aliases.get(func.value.id, func.value.id)
            if func.attr in MODULE_CALLS.get(module, set()):
                self._add(f"{module}.{func.attr}", node)
                self.generic_visit(node)
                return
        if isinstance(func, ast.Name) and func.id in self.aliases and "." in self.aliases[func.id]:
            module, _, name = self.aliases[func.id].rpartition(".")
            if name in MODULE_CALLS.get(module, set()):
                self._add(f"{module}.{name}", node)
        if isinstance(func, ast.Attribute):
            if func.attr in METHODS:
                self._add(f".{func.attr}", node)
            elif func.attr == "replace" and len(node.args) + len(node.keywords) == 1:
                self._add(".replace", node)  # Path.replace(target); str.replace takes two arguments
            elif func.attr == "open" and _writing(node, 0):
                self._add(".open", node)
        if isinstance(func, ast.Name) and func.id == "open" and _writing(node, 1):
            self._add("open", node)
        self.generic_visit(node)


def _writing(node: ast.Call, mode_index: int) -> bool:
    mode = node.args[mode_index] if len(node.args) > mode_index else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(WRITE_MODES & set(mode.value))
    return True  # a computed mode might write


def destructive_calls() -> dict[tuple[str, str, str], list[int]]:
    found: dict[tuple[str, str, str], list[int]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "fileops.py":
            continue
        finder = Finder()
        finder.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for scope, op, line in finder.found:
            found.setdefault((rel, scope, op), []).append(line)
    return found


def test_only_fileops_and_reviewed_cache_writers_can_delete_rename_or_write():
    found = destructive_calls()
    unexpected = {key: lines for key, lines in found.items() if key not in ALLOWED}
    assert not unexpected, (
        "calls that can delete, rename or overwrite files outside rie.fileops; move them into rie.fileops, or, "
        "if they only touch the app's own cache or data folder, add them to ALLOWED with the reason:\n"
        + "\n".join(f"  {rel}:{lines} in {scope}: {op}" for (rel, scope, op), lines in sorted(unexpected.items()))
    )


def test_the_allowlist_has_no_stale_entries():
    stale = set(ALLOWED) - set(destructive_calls())
    assert not stale, f"ALLOWED lists calls that no longer exist: {sorted(stale)}"


def test_the_finder_sees_aliases_and_writing_modes():
    source = (
        "import os as o\nfrom shutil import rmtree\nfrom pathlib import Path\n"
        "def f(p: Path):\n    o.remove(p)\n    rmtree(p)\n    p.replace(p)\n    'a'.replace('a', 'b')\n"
        "    open(p, 'rb')\n    open(p, 'w')\n    p.open(mode='ab')\n"
    )
    finder = Finder()
    finder.visit(ast.parse(source))
    assert [op for _, op, _ in finder.found] == ["os.remove", "shutil.rmtree", ".replace", "open", ".open"]


def test_fileops_is_the_module_that_trashes(tmp_path):
    """The audit hook sees fileops' rename, and reports a change made elsewhere."""
    from rie import fileops

    top = tmp_path / "drive"
    shoot = top / "shoot"
    shoot.mkdir(parents=True)
    photo = shoot / "DSC_0001.NEF"
    photo.write_bytes(b"raw")
    with guard.watch(shoot) as seen:
        fileops.trash(photo, topdir=top)
        (shoot / "stray.txt").write_text("not allowed")  # this test file is not rie.fileops
    assert any(e.name == "rie.fileops" and e.detail == "trash" for e in seen.events)
    assert [Path(e.paths[0]).name for e in seen.violations()] == ["stray.txt"]
