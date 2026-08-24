from types import SimpleNamespace

from gui import updater


def test_check_for_update_detects_remote_main_ahead(monkeypatch):
    outputs = iter(["local-commit\n", "remote-commit\trefs/heads/main\n"])

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=next(outputs), returncode=0)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    assert updater.check_for_update() == (True, "main @ remote-", None)


def test_check_for_update_reports_current_main(monkeypatch):
    outputs = iter(["same-commit\n", "same-commit\trefs/heads/main\n"])

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=next(outputs), returncode=0)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    assert updater.check_for_update() == (False, "main @ same-co", None)