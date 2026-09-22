from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.platform.project.router import _legacy_template_detail, _legacy_template_summary
from src.platform.project.schemas import ProjectCreate
from src.platform.template_registry.config import TemplateRegistrySettings
from src.platform.template_registry.providers.builtin import BuiltinTemplateRegistryProvider


@pytest.mark.asyncio
async def test_legacy_template_shapes_preserve_cover_version_and_preview_fields() -> None:
    provider = BuiltinTemplateRegistryProvider(TemplateRegistrySettings(_env_file=None))
    page = await provider.list_templates(
        query=None,
        category=None,
        cursor=None,
        limit=1,
    )
    detail = await provider.get_template(page.templates[0].id)

    summary_payload = _legacy_template_summary(page.templates[0])
    detail_payload = _legacy_template_detail(detail)

    assert "cover" in summary_payload
    assert detail_payload["version"] == detail.current_release.version
    assert "preview_doc" in detail_payload


def test_project_create_is_a_strict_empty_project_contract() -> None:
    with pytest.raises(ValidationError, match="org_id"):
        ProjectCreate(name="Copy")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectCreate(
            name="Copy",
            org_id="org-1",
            template="get-started",  # type: ignore[call-arg]
        )
