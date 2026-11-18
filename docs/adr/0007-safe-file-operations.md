# ADR 0007: Safe file operations without send2trash

- **Status:** accepted
- **Date:** 2026-11-18 (Phase 1)
- **Code:** `src/rie/fileops.py`, `src/rie/safety.py`, `tests/test_fileops.py`, `tests/test_no_hard_deletes.py`, `tests/guard.py`

## Context

Plan §3.5 says the app can never lose a photo: one module does every file operation on the drive, its only removal is moving a file to the drive's own trash, it refuses when that trash is not on the same drive, the catalog records where each file went so it can be restored, and a test fails the build if any other code deletes, overwrites or renames files in the photo folders. §7 named send2trash as the trash library behind that module.

## Findings

- send2trash follows the freedesktop.org trash specification, but it does not say where a file went: it picks a free name in `files/` and returns nothing. Restoring a single photo from the catalog needs that name.
- Its same-drive behaviour is implicit: it tries `os.rename` and relies on the error across devices. The plan wants an explicit check and a refusal.
- On Linux, `os.rename` replaces an existing target silently. `renameat2` with `RENAME_NOREPLACE` refuses instead; Linux supports it on ext4, btrfs, tmpfs and ntfs3 (the drive's driver, ADR 0005). Python does not expose it, but `ctypes` can call it.
- Python's audit hooks report `open` with its mode and flags, `os.remove`, `os.rename` (also raised by `os.replace`), `os.rmdir`, `shutil.rmtree` and `subprocess.Popen`, with the calling stack available. That is enough to check, while real code runs, that only one module changes a folder. Calls made through `ctypes` are not reported, so the module raises its own `rie.fileops` audit event for each operation.
- On this machine nothing empties the trash automatically: GNOME's `remove-old-trash-files` is off and its housekeeping daemon is not installed, there is no crontab, and no systemd unit, cron file or tmpfiles rule mentions the trash.

## Decision

- `rie.fileops` implements the trash specification itself instead of using send2trash:
  - The trash is `<topdir>/.Trash/<uid>` when `.Trash` is a real directory with the sticky bit, else `<topdir>/.Trash-<uid>`; the home trash for a file on the home partition. On the photo drive, `topdir` is the drive's mount point.
  - It refuses when `files/` or `info/` is a symlink or on another device than the file, and it never copies.
  - It writes the `.trashinfo` first (created exclusively, with the path relative to `topdir`, percent-encoded, and the local deletion time), then renames the file with `RENAME_NOREPLACE`. A name already used in the trash gets a number (`DSC_0001.2.NEF`).
  - It returns where the file went; the culling code records that in `trash_entries`.
- Restore renames the file back, never over an existing file, and then removes its `.trashinfo`, as the specification asks. That metadata file is the only thing the module ever removes.
- Sidecars and exports are written under a temporary dot-name, synced, and renamed into place: a new file with `RENAME_NOREPLACE`, a sidecar update only for `.pp3` files whose content is still what the caller read, and only when the app created the sidecar or backed it up to the internal disk first. A temporary file that cannot be renamed into place goes to the trash too.
- Two tests enforce the rule:
  - a static one parses every module in `src/rie` and fails on any call that can remove, rename or write a file outside `rie.fileops`, unless an allowlist entry says why it only touches the app's own cache or data folder;
  - an audit hook (`tests/guard.py`) watches a photo folder while real code runs and fails on any change that does not come from `rie.fileops`. The scan and culling tests use it.
- `rie check` reports the drive's trash size and free space, warns below `safety.min_free_gb`, and lists any setting or job that would empty the trash on its own (GNOME, KDE, cron, systemd units, tmpfiles rules).
- `rie render -o` refuses outputs inside the photos folder; exports there will go through `rie.fileops`.

## Consequences for the plan

- §7's trash row: send2trash is not used. The module follows the same specification, so file managers see and restore the app's trashed files as their own.
- The trash check of §3.5 ("Phase 1 also checks that nothing on the system empties the trash automatically") is part of `rie check`, not a one-off.
