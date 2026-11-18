# ADR 0005: Telling whether the photo drive is connected

- **Status:** accepted
- **Date:** 2026-11-17 (Phase 0, spike e)
- **Code:** `src/rie/drive.py`, `tests/test_drive.py`, `rie check`

## Context

Photos live on an external NTFS drive. When it is unplugged, the app must say so instead of showing empty folders, and jobs that need raw files must wait. The mount point can exist while empty. Trashing on the drive is already known to stay on the drive (its `.Trash-1000` folder) and was not tested again.

## Findings

How the drive is mounted on this installation:

- `/etc/fstab` mounts it by filesystem UUID with `ntfs3` and `nofail,x-systemd.automount,x-systemd.device-timeout=10s,x-systemd.mount-timeout=30s`. Files belong to a dedicated account and group, and the owner's account is in that group.
- `/proc/self/mountinfo` shows two entries at the mount point: the `autofs` trigger (`systemd-1`) and the `ntfs3` filesystem mounted on top of it, with the block device's numbers (`8:1`). The mount unit `Requires=` the device unit.
- With an automount, the mount point always exists. Any access to it while the device is absent waits for the device timeout (10 s here) and then fails. This is systemd's documented behaviour; it was not tested by unplugging the drive.
- `/dev/disk/by-uuid/<UUID>` exists exactly while the device is present. An NTFS UUID is the 16-hex-digit volume serial (no dashes).

## Decision

`rie.drive.drive_status(uuid, mountpoint)` decides from `/dev/disk/by-uuid` and `/proc/self/mountinfo` alone, never from the photo folders:

| State | Condition |
|---|---|
| connected | the UUID's device exists, and a non-autofs filesystem with that device's numbers is mounted (at the expected mount point, or anywhere else, which is reported) |
| not mounted | the device exists but nothing from it is mounted; with an automount, the first access mounts it, which `ensure_mounted()` triggers only in this state |
| unplugged | no device has the UUID; the mount point is not touched, so nothing waits for the timeout. A stale mount whose device has gone counts as unplugged. |
| wrong device | another filesystem is mounted at the expected mount point |
| unconfigured | `drive.uuid` is not set |

- A check takes 0.25 ms.
- `drive.uuid`, and optionally `drive.mountpoint`, go in `config.toml`.
- `rie check` prints the state.
- Unit tests cover every state with mount-table fixtures, including paths with spaces and a stale mount.

## Consequences for the plan

- Matches §3.3: the catalog keeps paths relative to the drive plus its UUID, so a different mount point still works.
- Jobs that read raw files check the state before each batch and pause while the drive is not connected (§3.4). Arrival can be noticed by polling the by-uuid link every few seconds; nothing mounts until something accesses the mount point.
- Not tested: physically unplugging and replugging the drive, and the device coming back under another name (such as `sdb1`). Both are covered by the logic (device numbers are read fresh on each check) but not by a real run.
