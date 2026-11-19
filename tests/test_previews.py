import io
import shutil

import pytest
from PIL import Image

from rie import catalog, config
from rie.pipeline import open_folder, run_previews
from rie.scan import previews
from tests.conftest import make_jpeg, sample_nefs

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


def jpeg_bytes(size=(60, 40), color=(200, 100, 50)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def fake_nef(path, other: bytes, full: bytes) -> dict[str, list[int]]:
    """A file laid out like a NEF: a header, the small JPEG, the full-size JPEG, then 'raw data'."""
    header = b"II*\x00" + b"\x00" * 1020
    path.write_bytes(header + other + full + b"\x00" * 4096)
    return {"other": [len(header), len(other)], "full": [len(header) + len(other), len(full)]}


@pytest.fixture
def cfg(tmp_path):
    return config.parse(
        {
            "paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")},
            "previews": {"thumb_size": 128, "screen_size": 512, "workers": 2},
        }
    )


def test_jpeg_size_reads_the_frame_header():
    assert previews.jpeg_size(jpeg_bytes((60, 40))) == (60, 40)
    with pytest.raises(previews.PreviewError):
        previews.jpeg_size(b"not a jpeg")


@pytest.mark.parametrize("orientation", [3, 6, 8])
def test_with_orientation_adds_an_exif_tag_without_reencoding(orientation):
    data = jpeg_bytes((60, 40))
    tagged = previews.with_orientation(data, orientation)
    assert tagged.endswith(data[2:])  # the image data is untouched
    with Image.open(io.BytesIO(tagged)) as im:
        assert im.getexif()[0x0112] == orientation
        assert im.size == (60, 40)
    assert previews.with_orientation(data, 1) == data


def test_read_ranges_uses_one_span(tmp_path):
    other, full = jpeg_bytes((30, 20)), jpeg_bytes((60, 40))
    ranges = fake_nef(tmp_path / "x.NEF", other, full)
    data = previews.read_ranges(tmp_path / "x.NEF", ranges, ("other", "full"))
    assert data == {"other": other, "full": full}


@pytest.mark.parametrize(("orientation", "upright"), [(1, (600, 400)), (8, (400, 600)), (6, (400, 600))])
def test_make_previews_turns_raw_previews_upright(tmp_path, cfg, orientation, upright):
    other, full = jpeg_bytes((300, 200)), jpeg_bytes((600, 400))
    ranges = fake_nef(tmp_path / "DSC_0001.NEF", other, full)
    result = previews.make_previews(7, tmp_path / "DSC_0001.NEF", "raw", ranges, orientation, "q1:abcd", cfg)
    assert result.error is None
    assert (result.width, result.height) == upright
    with Image.open(previews.thumb_path(cfg, "q1:abcd")) as thumb:
        assert max(thumb.size) == 128 and (thumb.size[0] > thumb.size[1]) == (upright[0] > upright[1])
    with Image.open(previews.screen_path(cfg, "q1:abcd")) as screen:
        assert max(screen.size) == 512 and (screen.size[0] > screen.size[1]) == (upright[0] > upright[1])
    assert set(result.look) == {"layout", "dhash", "luma", "clip_high", "clip_low"}
    assert len(result.look["layout"]) == 8 * 8 * 3


def test_make_previews_reports_a_missing_embedded_jpeg(tmp_path, cfg):
    (tmp_path / "x.NEF").write_bytes(b"\x00" * 100)
    result = previews.make_previews(1, tmp_path / "x.NEF", "raw", {}, 1, "q1:00", cfg)
    assert result.error == "no full-size embedded JPEG"


def test_full_size_serves_the_embedded_jpeg_with_its_orientation(tmp_path):
    other, full = jpeg_bytes((30, 20)), jpeg_bytes((60, 40))
    ranges = fake_nef(tmp_path / "x.NEF", other, full)
    data = previews.full_size(tmp_path / "x.NEF", "raw", ranges, 8)
    with Image.open(io.BytesIO(data)) as im:
        assert im.getexif()[0x0112] == 8 and im.size == (60, 40)


@needs_exiftool
def test_open_folder_makes_previews_once(tmp_path, cfg):
    shoot = tmp_path / "shoot"
    make_jpeg(shoot / "DSC_0001.JPG", size=(900, 600))
    make_jpeg(shoot / "DSC_0002.JPG", size=(600, 900), color=(10, 200, 10))
    conn = catalog.connect(cfg.catalog_path)
    result = open_folder(conn, cfg, shoot)
    assert (result.previews.done, result.previews.failed) == (2, 0)
    sizes = dict(conn.execute("SELECT name, width || 'x' || height FROM images").fetchall())
    assert sizes == {"DSC_0001.JPG": "900x600", "DSC_0002.JPG": "600x900"}
    again = run_previews(conn, cfg, result.scan.folder_id)
    assert (again.done, again.skipped) == (0, 2)


@needs_exiftool
@pytest.mark.skipif(not sample_nefs(1), reason="no sample NEFs")
def test_real_nef_previews(tmp_path, cfg):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    nef = sample_nefs(1)[0]
    shutil.copyfile(nef, shoot / nef.name)
    conn = catalog.connect(cfg.catalog_path)
    result = open_folder(conn, cfg, shoot)
    assert result.previews.done == 1, result.previews.errors
    row = conn.execute("SELECT quick_hash, width, height, orientation, previews FROM images").fetchone()
    assert {row["width"], row["height"]} == {6000, 4000}
    data = previews.full_size(shoot / nef.name, "raw", catalog.loads(row["previews"]), row["orientation"])
    assert previews.jpeg_size(data) == (6000, 4000)
