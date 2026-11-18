import os
import shutil
from pathlib import Path

import pytest

from rie import catalog, config, drive, locations
from rie.scan import exif, files
from rie.scan.folder import ScanError, scan_folder
from tests.conftest import make_jpeg, sample_nefs

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


def touch(path: Path, data: bytes = b"x" * 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_list_folder_pairs_raw_and_jpeg_and_skips_dot_files(tmp_path):
    for name in ("DSC_0001.NEF", "DSC_0001.JPG", "DSC_0002.nef", "DSC_0003.JPG", "._DSC_0001.NEF", ".hidden.NEF"):
        touch(tmp_path / name)
    touch(tmp_path / "DSC_0001.NEF.pp3")
    touch(tmp_path / "DSC_0004.MOV")
    touch(tmp_path / "jpg" / "DSC_0001.jpg")
    listing = files.list_folder(tmp_path)
    assert [(s.name, s.kind, s.jpeg_name) for s in listing.shots] == [
        ("DSC_0001.NEF", "raw", "DSC_0001.JPG"),
        ("DSC_0002.nef", "raw", None),
        ("DSC_0003.JPG", "jpeg", None),
    ]
    assert listing.sidecars == ["DSC_0001.NEF.pp3"]
    assert listing.other == ["DSC_0004.MOV"]
    assert listing.subfolders == ["jpg"]


def test_quick_hash_depends_on_size_and_head_only(tmp_path):
    a = touch(tmp_path / "a", b"A" * 70_000 + b"tail-1")
    b = touch(tmp_path / "b", b"A" * 70_000 + b"tail-2")
    c = touch(tmp_path / "c", b"A" * 70_000 + b"tail-22")
    assert files.quick_hash(a) == files.quick_hash(b)  # same size, same first 64 KiB
    assert files.quick_hash(a) != files.quick_hash(c)  # different size
    assert files.quick_hash(a).startswith("q1:")
    assert files.content_hash(b"abc").startswith("sha256:ba7816bf")


def test_af_points_from_the_51_point_mask():
    assert exif.af_points_from_mask("01 00 00 00 00 00 00") == ["C6"]
    assert exif.af_points_from_mask("20 00 00 00 00 00 00") == ["C7"]
    assert exif.af_points_from_mask("03 00 00 00 00 00 04") == ["C6", "B6", "D1"]
    assert exif.af_points_from_mask(0) == []
    assert sorted(exif.AF_POINTS_51) == list(range(1, 52))
    assert len(set(exif.AF_POINTS_51.values())) == 51


def test_capture_time_keeps_tenths_and_falls_back_to_subsec():
    assert exif.capture_time({"Composite:SubSecDateTimeOriginal": "2026:07:05 18:08:52.50"}) == "2026-07-05T18:08:52.50"
    record = {"ExifIFD:DateTimeOriginal": "2026:07:05 18:08:52", "ExifIFD:SubSecTimeOriginal": "7"}
    assert exif.capture_time(record) == "2026-07-05T18:08:52.7"
    assert exif.capture_time({"ExifIFD:DateTimeOriginal": "0000:00:00 00:00:00"}) is None
    later, earlier = exif.timestamp("2026-07-05T18:08:52.70"), exif.timestamp("2026-07-05T18:08:52.50")
    assert later is not None and earlier is not None and later - earlier == pytest.approx(0.2)


def test_normalize_reads_the_d7100_fields():
    record = {
        "IFD0:Make": "NIKON CORPORATION",
        "IFD0:Model": "NIKON D7100",
        "IFD0:Orientation": 8,
        "ExifIFD:ExposureTime": 0.005,
        "ExifIFD:FNumber": 1.8,
        "ExifIFD:ISO": 640,
        "ExifIFD:FocalLength": 35,
        "ExifIFD:Flash": 16,
        "ExifIFD:ExposureCompensation": -0.3333,
        "Nikon:ShootingMode": 1,
        "Nikon:PrimaryAFPoint": 6,
        "Nikon:AFPointsUsed": "20 00 00 00 00 00 00",
        "Nikon:FocusDistance": 1.41,
        "Nikon:WB_RBLevels": "1.69 2.05 1 1",
        "Nikon:CropHiSpeed": "0 6036 4020 6036 4020 0 0",
        "Nikon:ShutterCount": 3264,
        "Composite:LensID": "9F 58 44 44 14 14 A1 06",
        "Composite:LightValue": 6.66,
        "Composite:ImageSize": "6036 4020",
        "SubIFD:JpgFromRawStart": 1152000,
        "SubIFD:JpgFromRawLength": 2514658,
        "SubIFD2:OtherImageStart": 186368,
        "SubIFD2:OtherImageLength": 965539,
        "rie:Printed": {
            "LensID": "AF-S DX Nikkor 35mm f/1.8G",
            "ShootingMode": "Continuous",
            "AFAreaMode": "Single Area",
        },
    }
    meta = exif.normalize(record)
    assert meta["lens"] == "AF-S DX Nikkor 35mm f/1.8G"
    assert meta["af_primary"] == "C7" and meta["af_points"] == ["C7"]
    assert meta["continuous"] is True and meta["flash_fired"] is False
    assert meta["wb_rb"] == [1.69, 2.05] and meta["crop_mode"] == "DX" and meta["raw_size"] == [6036, 4020]
    assert exif.orientation(record) == 8 and exif.shutter_count(record) == 3264
    assert exif.preview_ranges(record) == {"full": [1152000, 2514658], "other": [186368, 965539]}


@pytest.mark.parametrize(
    "arg", ["-o", "-overwrite_original", "-Rating=5", "-XMP:Rating+=1", "-tagsFromFile", "-w", "-execute", "-@"]
)
def test_write_options_never_reach_exiftool(arg):
    with pytest.raises(exif.ExifError, match="refusing"):
        exif.check_read_only(["-json", arg, "/photos/DSC_0001.NEF"])


def test_read_options_are_allowed():
    exif.check_read_only([*exif.NUMERIC_ARGS, *exif.PRINTED_ARGS, "/photos/-odd name.NEF"])


@needs_exiftool
def test_exiftool_reads_jpegs_in_one_process(tmp_path):
    a = make_jpeg(tmp_path / "a b.JPG", when="2026:05:01 10:00:00", subsec="3")
    b = make_jpeg(tmp_path / "ü.JPG", when="2026:05:01 10:00:01", subsec="5")
    records = list(exif.read_folder([a, b], chunk=1))
    assert [os.path.basename(r["SourceFile"]) for r in records] == ["a b.JPG", "ü.JPG"]
    assert [exif.capture_time(r) for r in records] == ["2026-05-01T10:00:00.3", "2026-05-01T10:00:01.5"]
    assert all("rie:Printed" in r for r in records)


# --- scanning a folder into the catalog -----------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return config.parse({"paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")}})


@pytest.fixture
def conn(cfg):
    connection = catalog.connect(cfg.catalog_path)
    yield connection
    connection.close()


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


@needs_exiftool
def test_scan_reads_metadata_and_writes_nothing(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    make_jpeg(shoot / "DSC_0001.JPG", when="2026:05:01 10:00:00", subsec="1")
    make_jpeg(shoot / "DSC_0002.JPG", when="2026:05:01 10:00:00", subsec="3")
    before = snapshot(shoot)
    summary = scan_folder(conn, cfg, shoot)
    assert (summary.shots, summary.new) == (2, 2)
    assert snapshot(shoot) == before
    rows = conn.execute("SELECT name, kind, state, capture_time, orientation FROM images ORDER BY name").fetchall()
    assert [tuple(r) for r in rows] == [
        ("DSC_0001.JPG", "jpeg", "present", "2026-05-01T10:00:00.1", 1),
        ("DSC_0002.JPG", "jpeg", "present", "2026-05-01T10:00:00.3", 1),
    ]
    folder = conn.execute("SELECT drive_uuid, path, name FROM folders").fetchone()
    assert tuple(folder) == ("", str(shoot), "shoot")


@needs_exiftool
def test_rescanning_an_unchanged_folder_reads_no_metadata(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    make_jpeg(shoot / "DSC_0001.JPG")
    scan_folder(conn, cfg, shoot)
    again = scan_folder(conn, cfg, shoot, exiftool="false")  # would fail if exiftool were started
    assert (again.unchanged, again.new) == (1, 0)


@needs_exiftool
def test_renamed_and_moved_files_keep_their_catalog_rows(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    make_jpeg(shoot / "DSC_0001.JPG", color=(10, 20, 30))
    make_jpeg(shoot / "DSC_0002.JPG", color=(200, 20, 30))
    scan_folder(conn, cfg, shoot)
    ids = dict(conn.execute("SELECT name, id FROM images").fetchall())

    os.rename(shoot / "DSC_0001.JPG", shoot / "renamed.JPG")
    summary = scan_folder(conn, cfg, shoot, exiftool="false")
    assert summary.relinked == 1 and summary.missing == 0
    assert dict(conn.execute("SELECT name, id FROM images").fetchall())["renamed.JPG"] == ids["DSC_0001.JPG"]

    moved = tmp_path / "moved shoot"
    os.rename(shoot, moved)
    summary = scan_folder(conn, cfg, moved, exiftool="false")
    assert summary.moved_from == str(shoot)
    assert conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 1
    assert conn.execute("SELECT path FROM folders").fetchone()[0] == str(moved)


@needs_exiftool
def test_a_deleted_file_is_marked_missing_and_comes_back(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    make_jpeg(shoot / "DSC_0001.JPG")
    make_jpeg(shoot / "DSC_0002.JPG", color=(1, 2, 3))
    scan_folder(conn, cfg, shoot)
    aside = tmp_path / "aside.JPG"
    os.rename(shoot / "DSC_0002.JPG", aside)
    assert scan_folder(conn, cfg, shoot).missing == 1
    assert conn.execute("SELECT state FROM images WHERE name = 'DSC_0002.JPG'").fetchone()[0] == "missing"
    os.rename(aside, shoot / "DSC_0002.JPG")
    scan_folder(conn, cfg, shoot)
    assert conn.execute("SELECT state FROM images WHERE name = 'DSC_0002.JPG'").fetchone()[0] == "present"


def test_scanning_a_file_is_an_error(tmp_path, cfg, conn):
    with pytest.raises(ScanError, match="not a folder"):
        scan_folder(conn, cfg, touch(tmp_path / "file.NEF"))


@needs_exiftool
@pytest.mark.skipif(not sample_nefs(2), reason="no sample NEFs")
def test_scan_real_nefs(tmp_path, cfg, conn):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for nef in sample_nefs(2):
        shutil.copyfile(nef, shoot / nef.name)
    scan_folder(conn, cfg, shoot)
    for row in conn.execute("SELECT meta, previews, shutter_count, capture_time FROM images"):
        meta, previews = catalog.loads(row["meta"]), catalog.loads(row["previews"])
        assert meta["model"] == "NIKON D7100"
        assert {"full", "other"} <= set(previews)
        assert row["shutter_count"] and row["capture_time"]


# --- the photo drive -------------------------------------------------------------------------------------------------

UUID = "0123456789ABCDEF"


def drive_cfg(tmp_path):
    mount = tmp_path / "drive"
    return config.parse(
        {
            "paths": {"photos": str(mount / "Photos"), "data": str(tmp_path / "data")},
            "drive": {"uuid": UUID, "mountpoint": str(mount)},
        }
    )


def test_a_folder_on_an_unplugged_drive_is_refused_without_touching_it(tmp_path, monkeypatch):
    cfg = drive_cfg(tmp_path)
    unplugged = drive.DriveStatus(drive.DriveState.UNPLUGGED, UUID, mountpoint=cfg.drive.mountpoint)
    monkeypatch.setattr(locations, "status", lambda _cfg: unplugged)
    monkeypatch.setattr(drive, "ensure_mounted", lambda _s: pytest.fail("must not touch the mount point"))
    with pytest.raises(locations.DriveUnavailable, match="unplugged"):
        locations.locate(cfg.drive.mountpoint / "Photos" / "shoot", cfg)  # type: ignore[operator]


def test_folders_on_the_drive_are_stored_relative_to_its_mount_point(tmp_path, monkeypatch):
    cfg = drive_cfg(tmp_path)
    elsewhere = tmp_path / "remounted"
    (elsewhere / "Photos" / "shoot").mkdir(parents=True)
    connected = drive.DriveStatus(drive.DriveState.CONNECTED, UUID, mountpoint=elsewhere)
    monkeypatch.setattr(locations, "status", lambda _cfg: connected)
    location = locations.locate(cfg.drive.mountpoint / "Photos" / "shoot", cfg)  # type: ignore[operator]
    assert location == locations.Location(UUID, "Photos/shoot")
    assert locations.resolve(location, cfg) == elsewhere / "Photos" / "shoot"
    assert locations.locate(tmp_path / "other", cfg) == locations.Location("", str(tmp_path / "other"))
