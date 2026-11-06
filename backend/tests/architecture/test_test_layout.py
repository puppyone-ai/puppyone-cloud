"""Tests live at tests/<area>/<domain>/<kind>/test_<capability>.py (tests/README.md).

Files from before the layout are frozen in unstructured_tests.txt: the list
only shrinks. Structured tests never import another test module; shared
fixtures live in a conftest.py, shared helpers in a support module.
"""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
KINDS = ("unit", "contract", "native", "live", "acceptance")
BASELINE = Path(__file__).with_name("unstructured_tests.txt")
IMPORT_BASELINE = Path(__file__).with_name("test_module_imports.txt")
TEST_MODULE_IMPORT = re.compile(r"^\s*(?:from|import)\s+tests\.[\w.]*\btest_\w+", re.MULTILINE)


def _relative(path):
    return path.relative_to(TESTS)


def _structured(path):
    return any(part in KINDS for part in _relative(path).parts[:-1])


def _all_tests():
    return [path for path in TESTS.rglob("test_*.py") if "__pycache__" not in path.parts]


def _listed(path):
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def _baseline():
    return _listed(BASELINE)


def test_new_tests_live_in_their_domain_and_kind():
    frozen = _baseline()
    misplaced = sorted(str(_relative(path)) for path in _all_tests()
                       if _relative(path).parts[0] != "architecture" and not _structured(path)
                       and str(_relative(path)) not in frozen)
    assert misplaced == [], f"Put these under tests/<area>/<domain>/<kind>/ (kinds: {', '.join(KINDS)}): {misplaced}"


def test_the_frozen_list_only_names_files_that_still_need_moving():
    existing = {str(_relative(path)) for path in _all_tests()}
    stale = sorted(_baseline() - existing)
    assert stale == [], f"Moved or removed; delete these lines from {BASELINE.name}: {stale}"


def test_structured_tests_never_import_another_test_module():
    importing = {str(_relative(path)) for path in _all_tests()
                 if _structured(path) and TEST_MODULE_IMPORT.search(path.read_text())}
    allowed = _listed(IMPORT_BASELINE)
    new = sorted(importing - allowed)
    assert new == [], f"Share fixtures through conftest.py and helpers through a support module: {new}"
    fixed = sorted(allowed - importing)
    assert fixed == [], f"No longer import a test module; delete these lines from {IMPORT_BASELINE.name}: {fixed}"


def test_a_kind_directory_sits_inside_a_domain():
    for path in _all_tests():
        parts = _relative(path).parts[:-1]
        for index, part in enumerate(parts):
            if part in KINDS:
                assert index >= 1, f"{_relative(path)}: the kind directory belongs inside a domain directory"
