"""Phase 2 end to end: the render cache, `rie edit` on real NEFs (read only, under the audit hook), `rie eval`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rie import catalog, cli, config
from rie.edit import EditSettings, edit_folder, photos_to_edit
from rie.edit_report import write_report
from rie.engine.recipe import parse
from rie.render import cached_render
from rie.scan.folder import scan_folder
from tests import guard
from tests.conftest import sample_nefs

needs_rawtherapee = pytest.mark.skipif(
    shutil.which("rawtherapee-cli") is None or shutil.which("exiftool") is None or len(sample_nefs(2)) < 2,
    reason="needs rawtherapee-cli, exiftool and two sample NEFs",
)


def test_cached_render_renders_the_same_profiles_once(tmp_path, fake_rt, raw_file):
    cli_path, log = fake_rt
    cfg = config.parse(
        {
            "paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")},
            "rawtherapee": {"cli": str(cli_path)},
        }
    )
    recipe = parse({"schema": "rie.recipe/1", "params": {"exposure": {"ev": 0.5}}})
    first = cached_render(raw_file, recipe, cfg, image_key="q1", size=512, preview=True)
    second = cached_render(raw_file, recipe, cfg, image_key="q1", size=512, preview=True)
    other = cached_render(raw_file, recipe, cfg, image_key="q1", size=1024, preview=True)
    assert first.output == second.output != other.output
    assert second.seconds == 0.0
    renders = [json.loads(line)["args"] for line in log.read_text().splitlines()]
    renders = [args for args in renders if args != ["-v"]]
    assert len(renders) == 2
    assert first.output.is_relative_to(cfg.paths.cache / "renders" / "cache")


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


@pytest.mark.rawtherapee
@needs_rawtherapee
def test_edit_folder_edits_real_nefs_and_writes_nothing_in_the_folder(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for nef in sample_nefs(2):
        shutil.copyfile(nef, shoot / nef.name)
        (shoot / nef.name).chmod(0o444)
    cfg = config.parse({"paths": {"cache": str(tmp_path / "cache"), "data": str(tmp_path / "data")}})
    conn = catalog.connect(cfg.catalog_path)
    try:
        folder_id = scan_folder(conn, cfg, shoot).folder_id
        assert len(photos_to_edit(conn, folder_id)) == 2  # nothing culled: everything is kept
        before = snapshot(shoot)
        with guard.watch(shoot) as seen:
            run = edit_folder(conn, cfg, folder_id, settings=EditSettings(workers=2, size=512))
        assert seen.violations() == []
        assert {Path(cmd[0]).name for cmd in seen.processes} <= {"rawtherapee-cli"}
        assert snapshot(shoot) == before
        page = write_report(run, cfg)
    finally:
        conn.close()
    for photo in run.photos:
        assert photo.errors == []
        assert photo.before is not None and photo.before.is_file()
        assert photo.after is not None and photo.after.is_file()
        assert set(photo.proposals) == {"geometry", "white_balance", "exposure", "color", "baseline"}
        assert photo.params["tone"]["curve_mode"] == "perceptual"
        assert photo.checks  # QA ran on the edited render
        parse(photo.recipe())
    data = json.loads((page.parent / "edits.json").read_text())
    assert data["metrics"]["photos"] == 2 and page.read_text().count('class="photo"') == 2


@pytest.mark.rawtherapee
@needs_rawtherapee
def test_eval_compares_a_sidecar_white_balance_and_reads_the_folder_only(tmp_path, capsys):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    nef = sample_nefs(1)[0]
    shutil.copyfile(nef, shoot / nef.name)
    (shoot / nef.name).chmod(0o444)
    (shoot / f"{nef.name}.pp3").write_text(
        "[Version]\nAppVersion=5.13\nVersion=353\n\n[White Balance]\nEnabled=true\nSetting=Custom\n"
        "Temperature=5600\nGreen=1\nEqual=1\nStandardObserver=TWO_DEGREES\n"
    )
    settings = tmp_path / "config.toml"
    settings.write_text(f'[paths]\ncache = "{tmp_path / "cache"}"\ndata = "{tmp_path / "data"}"\n')
    before = snapshot(shoot)
    with guard.watch(shoot) as seen:
        assert cli.main(["--config", str(settings), "eval", str(shoot), "--agent", "wb", "--workers", "1"]) == 0
    assert seen.violations() == [] and snapshot(shoot) == before
    report = Path(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("report: "))
    row = json.loads(report.read_text())["results"][0]["rows"][0]
    assert abs(row["owner"]["temp"] - 5600) < 60  # the probe measures what RawTherapee applies
    assert row["agent_error"] >= 0 and row["camera_error"] >= 0
