"""Run the existing 59 SQL permission/credential assertions on the owned DB."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[5]


def test_existing_sql_authorization_contract(postgres):
    # CI installs the PostgreSQL extension. A locally built pgTAP SQL library
    # is also supported without installing anything in the workstation's PG.
    library = os.environ.get("PUPPYONE_TEST_PGTAP_SQL")
    postgres.sql(Path(library).read_text() if library else "CREATE EXTENSION IF NOT EXISTS pgtap")
    result = postgres.sql(
        (ROOT / "supabase/tests/unified_project_authorization_test.sql").read_text()
    )
    assert "not ok" not in result, result
    assert (
        "1..59" in result and sum(line.startswith("ok ") for line in result.splitlines()) == 59
    ), result
