import json

import pytest

from rie.engine.rawtherapee import RawTherapee, RenderError


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_command_order_and_options(fake_rt, raw_file, tmp_path):
    cli, _ = fake_rt
    rt = RawTherapee(cli=str(cli), settings_dir=tmp_path / "rt")
    a, b = tmp_path / "a.pp3", tmp_path / "b.pp3"
    cmd = rt.command(raw_file, [a, b], tmp_path / "out.jpg", quality=92, subsampling=2, fast=True)
    assert cmd[1:] == [
        "-q",
        "-Y",
        "-o",
        str(tmp_path / "out.jpg"),
        "-p",
        str(a),
        "-p",
        str(b),
        "-j92",
        "-js2",
        "-f",
        "-c",
        str(raw_file),
    ]


@pytest.mark.parametrize(("quality", "subsampling"), [(0, 3), (101, 3), (95, 4)])
def test_command_rejects_bad_jpeg_options(fake_rt, raw_file, tmp_path, quality, subsampling):
    rt = RawTherapee(cli=str(fake_rt[0]), settings_dir=tmp_path / "rt")
    with pytest.raises(ValueError):
        rt.command(raw_file, [], tmp_path / "o.jpg", quality=quality, subsampling=subsampling)


def test_render_uses_app_settings_and_renames_into_place(fake_rt, raw_file, tmp_path):
    cli, log = fake_rt
    profile = tmp_path / "base.pp3"
    profile.write_text("[Exposure]\nAuto=false\n")
    rt = RawTherapee(cli=str(cli), settings_dir=tmp_path / "rt")
    before = raw_file.read_bytes()
    result = rt.render(raw_file, [profile], tmp_path / "out" / "DSC_0001.jpg")
    assert result.output.read_bytes()[:2] == b"\xff\xd8"
    (call,) = calls(log)
    assert call["RT_SETTINGS"] == str(tmp_path / "rt")
    written = call["args"][call["args"].index("-o") + 1]
    assert written != str(result.output)  # a temporary name, renamed when complete
    assert not list((tmp_path / "out").glob(".*partial*"))
    assert raw_file.read_bytes() == before


def test_failed_render_leaves_no_output(fake_rt, raw_file, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_RT_EXIT", "253")
    rt = RawTherapee(cli=str(fake_rt[0]), settings_dir=tmp_path / "rt")
    with pytest.raises(RenderError, match="processing profile missing or unreadable"):
        rt.render(raw_file, [], tmp_path / "out.jpg")
    assert list(tmp_path.glob("*.jpg")) == []


def test_render_refuses_to_overwrite_the_raw_or_write_non_jpeg(fake_rt, raw_file, tmp_path):
    rt = RawTherapee(cli=str(fake_rt[0]), settings_dir=tmp_path / "rt")
    with pytest.raises(RenderError):
        rt.render(raw_file, [], raw_file)
    with pytest.raises(RenderError):
        rt.render(raw_file, [], tmp_path / "out.tif")


def test_missing_inputs_are_reported_before_running(fake_rt, raw_file, tmp_path):
    rt = RawTherapee(cli=str(fake_rt[0]), settings_dir=tmp_path / "rt")
    with pytest.raises(RenderError, match="raw file not found"):
        rt.render(tmp_path / "missing.NEF", [], tmp_path / "o.jpg")
    with pytest.raises(RenderError, match="profile not found"):
        rt.render(raw_file, [tmp_path / "missing.pp3"], tmp_path / "o.jpg")
    with pytest.raises(RenderError, match="not found on PATH"):
        RawTherapee(cli="no-such-rawtherapee-cli").render(raw_file, [], tmp_path / "o.jpg")


def test_version_reads_the_banner(fake_rt, tmp_path):
    assert RawTherapee(cli=str(fake_rt[0]), settings_dir=tmp_path / "rt").version() == "5.13"
