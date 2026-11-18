from pathlib import Path

import pytest

from rie import config


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(tmp_path):
    cfg = config.load(tmp_path / "missing.toml")
    assert cfg == config.Config()
    assert cfg.rawtherapee_settings_dir == cfg.paths.cache / "rawtherapee"
    assert cfg.render.preview_size == 2048


def test_example_config_parses():
    cfg = config.load(config.REPO_ROOT / "config.example.toml")
    assert cfg.drive.uuid is not None
    assert cfg.paths.photos is not None


def test_paths_expand_home_and_resolve_relative_to_the_file(tmp_path):
    body = '[paths]\nphotos = "~/Photos"\ncache = "cache"\n[rawtherapee]\nsettings_dir = "/opt/rt"\n'
    cfg = config.load(write(tmp_path, body))
    assert cfg.paths.photos == Path.home() / "Photos"
    assert cfg.paths.cache == tmp_path / "cache"
    assert cfg.rawtherapee_settings_dir == Path("/opt/rt")


@pytest.mark.parametrize(
    "body",
    [
        "[pathz]\n",
        '[paths]\nphoto = "/x"\n',
        '[drive]\nuuid = "not a uuid!"\n',
        "[render]\njpeg_quality = 0\n",
        "[render]\njpeg_subsampling = 4\n",
        '[render]\npreview_size = "big"\n',
        '[claude]\nenabled = "yes"\n',
        "[paths\n",
    ],
)
def test_invalid_settings_are_errors(tmp_path, body):
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, body))


def test_ntfs_and_ext4_uuids_are_accepted(tmp_path):
    for uuid in ("0123456789ABCDEF", "123e4567-e89b-12d3-a456-426614174000", "ABCD-1234"):
        assert config.load(write(tmp_path, f'[drive]\nuuid = "{uuid}"\n')).drive.uuid == uuid
