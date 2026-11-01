"""Project counts entry points only through the owners installed at startup."""

import subprocess
import sys
from pathlib import Path

import pytest

from src.composition.project import install_project_entry_points
from src.platform.project import entry_points

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_counters(monkeypatch):
    monkeypatch.setattr(entry_points, "_counters", {})


def test_a_process_that_installed_nothing_refuses_to_report_zero(no_counters):
    with pytest.raises(RuntimeError, match="not installed"):
        entry_points.count_entry_points(["project"])


def test_no_projects_ask_no_owner(no_counters):
    assert entry_points.count_entry_points([]) == {}


def test_every_owner_adds_to_the_total(no_counters):
    entry_points.register_entry_point_counter("first", lambda _ids: {"p1": 2})
    entry_points.register_entry_point_counter("second", lambda _ids: {"p1": 1, "p2": 4})
    assert entry_points.count_entry_points(["p1", "p2"]) == {"p1": 3, "p2": 4}


def test_owners_receive_the_requested_projects(no_counters):
    seen = []
    entry_points.register_entry_point_counter("owner", lambda ids: seen.append(ids) or {})
    entry_points.count_entry_points(["p1", "p2"])
    assert seen == [["p1", "p2"]]


def test_installing_twice_does_not_count_twice(no_counters):
    entry_points.register_entry_point_counter("owner", lambda _ids: {"p1": 1})
    entry_points.register_entry_point_counter("owner", lambda _ids: {"p1": 1})
    assert entry_points.count_entry_points(["p1"]) == {"p1": 1}


def test_install_connects_every_owner(no_counters):
    install_project_entry_points()
    assert set(entry_points._counters) == {"synchronize", "access"}


def test_a_failing_owner_fails_the_count_rather_than_lowering_it(no_counters):
    def unavailable(_ids):
        raise ConnectionError("storage unavailable")

    entry_points.register_entry_point_counter("healthy", lambda _ids: {"p1": 1})
    entry_points.register_entry_point_counter("broken", unavailable)
    with pytest.raises(ConnectionError):
        entry_points.count_entry_points(["p1"])


def test_the_api_process_installs_every_owner_when_it_loads():
    probe = (
        "import src.main\n"
        "from src.platform.project import entry_points\n"
        "print(sorted(entry_points._counters))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "['access', 'synchronize']"
