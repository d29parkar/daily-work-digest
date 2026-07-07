from __future__ import annotations

import pytest

from conftest import write_config
from digest.cli import main


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "generate" in capsys.readouterr().out


def test_generate_and_dry_run_send(tmp_path, capsys):
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path), "generate", "--mode", "morning"]) == 0
    out = capsys.readouterr().out
    assert "Wrote digest" in out

    assert (
        main(["--config", str(config_path), "send", "--mode", "morning", "--dry-run"]) == 0
    )
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "no email sent" in out


def test_send_without_smtp_env_saves_report_and_degrades(tmp_path, capsys, monkeypatch):
    for env in ("DIGEST_EMAIL_FROM", "DIGEST_SMTP_USERNAME", "DIGEST_SMTP_PASSWORD"):
        monkeypatch.delenv(env, raising=False)
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path), "send", "--mode", "night"]) == 0
    out = capsys.readouterr().out
    assert "EMAIL NOT SENT" in out
    assert "Digest saved" in out
    assert (tmp_path / "outputs").exists()


def test_doctor_runs(tmp_path, capsys):
    config_path = write_config(tmp_path)
    code = main(["--config", str(config_path), "doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Config validation" in out
    assert "Windows scheduled tasks" in out


def test_ingest_command(tmp_path, capsys):
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path), "ingest"]) == 0
    assert "Ingest complete" in capsys.readouterr().out


def test_invalid_config_returns_error(tmp_path, capsys):
    config_path = write_config(tmp_path, provider="nonsense")
    assert main(["--config", str(config_path), "generate", "--mode", "morning"]) == 1
    assert "Config error" in capsys.readouterr().err
