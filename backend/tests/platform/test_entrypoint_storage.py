"""Canonical persistence still produces the existing public task/binding shapes."""
from unittest.mock import MagicMock


from src.platform.access.adapters.agent.config.repository import _row_to_tool
from src.platform.project.dashboard_router import _fetch_uploads
from src.platform.synchronize.github.repository import _to_api_row

NOW = '2026-09-27T00:00:00+00:00'


def test_canonical_access_identifier_keeps_agent_wire_contract():
    row = dict(id='link', access_surface_id='surface', tool_id='tool', created_at=NOW)
    result = _row_to_tool(row).model_dump()
    assert result['agent_id'] == 'surface'
    assert result['tool_id'] == 'tool'
    assert 'access_surface_id' not in result


def test_github_log_preserves_canonical_parent_without_alias_or_mutation():
    row = dict(id='log', synchronize_github_binding_id='binding', version_commit_id='commit', status='success')
    result = _to_api_row(row)
    assert result == row and result is not row
    assert result['synchronize_github_binding_id'] == 'binding'
    assert 'binding_id' not in result and 'integration_id' not in result
    assert result['version_commit_id'] == 'commit'


def test_dashboard_lists_upload_jobs_and_hides_retired_search_rows():
    uploads = MagicMock()
    # Model Supabase chain return values while asserting the consumer boundary.
    for method in ('select','neq','eq','in_','limit'):
        getattr(uploads, method).return_value = uploads
    uploads.execute.return_value.data = [dict(id='upload-1',status='running',type='file_ocr',progress=20)]
    client = MagicMock()
    client.table.side_effect = lambda name: {'uploads': uploads}[name]
    result = _fetch_uploads(client, 'project-1')
    assert [(row.id, row.type) for row in result] == [('upload-1','file_ocr')]
    uploads.neq.assert_called_once_with('type','search_index')
    uploads.eq.assert_called_once_with('project_id','project-1')
