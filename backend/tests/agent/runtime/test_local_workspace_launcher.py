import signal
import subprocess
from unittest.mock import Mock

import pytest

from scripts import run_local_workspace as launcher


def environment(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("version = 1\n")
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "run_local_workspace.py"
    script.write_text("# launcher\n")
    env = tmp_path / ".env"
    env.write_text("TEST_RUNTIME=old\n")
    monkeypatch.setattr(launcher, "__file__", str(script))
    monkeypatch.setattr(launcher.sys, "argv", [str(script), "--env-file", str(env), "--reload"])
    handlers = {}
    monkeypatch.setattr(launcher.signal, "signal", lambda sig, fn: handlers.update({sig: fn}))
    return env, handlers


def test_reload_stops_both_children_then_rechecks_and_starts_one_config_snapshot(
    monkeypatch, tmp_path
):
    env, handlers = environment(monkeypatch, tmp_path)
    events, children = [], []
    clock = [0.0]
    monkeypatch.setattr(launcher.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] == 1:
            env.write_text("TEST_RUNTIME=new\n")
        if clock[0] >= 2:
            handlers[signal.SIGTERM](None, None)

    def start(_command, **kwargs):
        value = kwargs["env"]["TEST_RUNTIME"]
        events.append(("start", value))
        child = Mock(
            poll=Mock(return_value=None),
            terminate=Mock(side_effect=lambda: events.append(("stop", value))),
        )
        children.append(child)
        return child

    monkeypatch.setattr(launcher.time, "sleep", sleep)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda _command, **kwargs: events.append(("preflight", kwargs["env"]["TEST_RUNTIME"])),
    )
    launcher.main()
    assert events == [
        ("preflight", "old"),
        ("start", "old"),
        ("start", "old"),
        ("stop", "old"),
        ("stop", "old"),
        ("preflight", "new"),
        ("start", "new"),
        ("start", "new"),
        ("stop", "new"),
        ("stop", "new"),
    ]
    assert len(children) == 4
    for child in children:
        child.wait.assert_called_once()


def test_failed_preflight_never_starts_api_or_worker(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    start = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(
        launcher.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "preflight"))
    )
    with pytest.raises(subprocess.CalledProcessError):
        launcher.main()
    start.assert_not_called()


def test_agent_death_restarts_children_without_a_source_change(monkeypatch, tmp_path):
    _, handlers = environment(monkeypatch, tmp_path)
    agent = Mock(healthy=Mock(side_effect=[False, True]))
    monkeypatch.setattr(launcher, "LocalFlyAgent", Mock(return_value=agent))
    clock, children = [0.0], []
    monkeypatch.setattr(launcher.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 2:
            handlers[signal.SIGTERM](None, None)

    def start(*args, **kwargs):
        child = Mock(poll=Mock(return_value=None))
        children.append(child)
        return child

    monkeypatch.setattr(launcher.time, "sleep", sleep)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(launcher.subprocess, "run", Mock())
    launcher.main()
    assert len(children) == 4
    assert agent.configure.call_count == 2
    agent.close.assert_called_once()
    for child in children:
        child.terminate.assert_called_once()
        child.wait.assert_called_once()


def test_source_identity_includes_added_removed_and_environment_files(monkeypatch, tmp_path):
    env, _ = environment(monkeypatch, tmp_path)
    original = launcher.runtime_inputs(tmp_path, env)
    source = tmp_path / "src" / "added.py"
    source.write_text("x = 1\n")
    assert launcher.runtime_inputs(tmp_path, env) != original
    source.unlink()
    assert launcher.runtime_inputs(tmp_path, env) == original
    env.write_text("TEST_RUNTIME=updated\n")
    assert launcher.runtime_inputs(tmp_path, env) != original
