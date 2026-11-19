import json

from rie import cli
from tests.test_recipe import PLAN_EXAMPLE


def write_config(tmp_path, fake_cli):
    path = tmp_path / "config.toml"
    path.write_text(f'[paths]\ncache = "{tmp_path / "cache"}"\n\n[rawtherapee]\ncli = "{fake_cli}"\n', encoding="utf-8")
    return path


def write_recipe(tmp_path, data=PLAN_EXAMPLE):
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_render_writes_to_the_cache_by_default(fake_rt, raw_file, tmp_path, capsys):
    cli_path, log = fake_rt
    cfg = write_config(tmp_path, cli_path)
    code = cli.main(["--config", str(cfg), "render", str(raw_file), "--recipe", str(write_recipe(tmp_path))])
    assert code == 0
    out = tmp_path / "cache" / "renders" / "DSC_0001.jpg"
    assert out.is_file()
    assert str(out) in capsys.readouterr().out
    args = json.loads(log.read_text().splitlines()[-1])["args"]
    profiles = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
    assert profiles[0].endswith("profiles/rawtherapee/d7100-base.pp3")
    assert len(profiles) == 2
    assert "-j95" in args and "-js3" in args
    assert list(raw_file.parent.iterdir()) == [raw_file]  # nothing written beside the original


def test_render_with_size_adds_a_resize_profile(fake_rt, raw_file, tmp_path):
    cli_path, log = fake_rt
    cfg = write_config(tmp_path, cli_path)
    out = tmp_path / "o.jpg"
    assert (
        cli.main(
            [
                "--config",
                str(cfg),
                "render",
                str(raw_file),
                "--recipe",
                str(write_recipe(tmp_path)),
                "--preview",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    args = json.loads(log.read_text().splitlines()[-1])["args"]
    assert len([a for a in args if a == "-p"]) == 3
    assert out.is_file()


def test_dry_run_prints_profiles_and_command(fake_rt, raw_file, tmp_path, capsys):
    cli_path, log = fake_rt
    cfg = write_config(tmp_path, cli_path)
    profile_out = tmp_path / "saved.pp3"
    code = cli.main(
        [
            "--config",
            str(cfg),
            "render",
            str(raw_file),
            "--recipe",
            str(write_recipe(tmp_path)),
            "--size",
            "1024",
            "--dry-run",
            "--profile-out",
            str(profile_out),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "[White Balance]\nSetting=Custom\nTemperature=5450\nGreen=1.03" in printed
    assert "LongEdge=1024" in printed
    assert "-c " + str(raw_file) in printed
    assert "Temperature=5450" in profile_out.read_text()
    assert not log.exists()  # rawtherapee-cli did not run


def test_invalid_recipe_is_a_clean_error(fake_rt, raw_file, tmp_path, capsys):
    cfg = write_config(tmp_path, fake_rt[0])
    bad = write_recipe(tmp_path, {"schema": "rie.recipe/1", "params": {"exposure": {"ev": 99}}})
    assert cli.main(["--config", str(cfg), "render", str(raw_file), "--recipe", str(bad)]) == 2
    assert "params.exposure.ev must be within" in capsys.readouterr().err


def test_render_failure_is_reported(fake_rt, raw_file, tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, fake_rt[0])
    monkeypatch.setenv("FAKE_RT_EXIT", "254")
    assert cli.main(["--config", str(cfg), "render", str(raw_file), "--recipe", str(write_recipe(tmp_path))]) == 2
    assert "could not save output" in capsys.readouterr().err


def test_check_reports_tools_and_unconfigured_drive(fake_rt, tmp_path, capsys):
    cfg = write_config(tmp_path, fake_rt[0])
    cli.main(["--config", str(cfg), "check"])
    printed = capsys.readouterr().out
    assert "rawtherapee-cli: 5.13" in printed
    assert "photo drive: unconfigured" in printed


def test_wait_for_port_sees_a_listener_and_gives_up_on_none():
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert cli.wait_for_port("127.0.0.1", port, timeout=2)
    assert not cli.wait_for_port("127.0.0.1", port, timeout=0.3)
