"""Trusted Registry adapter for starter templates installed with the app."""

from __future__ import annotations

from src.platform.project.templates import TEMPLATES, get_template_detail

from ..bundle import BuiltTemplateBundle, build_template_bundle
from ..config import RegistryMode, TemplateRegistrySettings
from ..exceptions import TemplateNotFoundError, TemplateReleaseNotFoundError
from ..provider import TemplatePage
from ..schemas import (
    TemplateDetail,
    TemplatePreviewDocument,
    TemplatePreviewNode,
    TemplateRelease,
    TemplateSummary,
)


class BuiltinTemplateRegistryProvider:
    mode: RegistryMode = "builtin"
    source: RegistryMode = "builtin"

    def __init__(self, settings: TemplateRegistrySettings) -> None:
        self._settings = settings
        self._artifacts: dict[str, BuiltTemplateBundle] = {}

    async def list_templates(
        self,
        *,
        query: str | None,
        category: str | None,
        cursor: str | None,
        limit: int,
    ) -> TemplatePage:
        summaries = [self._summary(template_id) for template_id in TEMPLATES]
        summaries.sort(key=lambda item: (TEMPLATES[item.id].order, item.id))

        normalized_query = (query or "").strip().casefold()
        normalized_category = (category or "").strip().casefold()
        if normalized_query:
            summaries = [
                item
                for item in summaries
                if normalized_query
                in " ".join([item.name, item.description, item.author or "", *item.tags]).casefold()
            ]
        if normalized_category:
            summaries = [
                item
                for item in summaries
                if (item.category or "").casefold() == normalized_category
            ]

        try:
            offset = int(cursor or "0")
        except ValueError:
            offset = 0
        offset = max(0, offset)
        page = summaries[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = str(next_offset) if next_offset < len(summaries) else None
        return TemplatePage(templates=page, next_cursor=next_cursor)

    async def get_template(self, template_id: str) -> TemplateDetail:
        detail = get_template_detail(template_id)
        if detail is None:
            raise TemplateNotFoundError(f"template not found: {template_id}")
        summary = self._summary(template_id)
        preview_document = detail.get("preview_doc")
        return TemplateDetail(
            **summary.model_dump(),
            screenshots=detail.get("screenshots") or [],
            long_description=detail.get("long_description"),
            file_tree=detail.get("file_tree") or [],
            preview_document=(
                TemplatePreviewDocument(**preview_document) if preview_document else None
            ),
            releases=[summary.current_release],
        )

    async def download_bundle(self, template_id: str, release_id: str) -> bytes:
        template = TEMPLATES.get(template_id)
        if template is None:
            raise TemplateNotFoundError(f"template not found: {template_id}")
        if release_id != template.version:
            raise TemplateReleaseNotFoundError(
                f"release {release_id!r} not found for template {template_id!r}"
            )
        return self._artifact(template_id).payload

    def _summary(self, template_id: str) -> TemplateSummary:
        template = TEMPLATES[template_id]
        artifact = self._artifact(template_id)
        release = TemplateRelease(
            id=template.version,
            version=template.version,
            bundle_sha256=artifact.bundle_sha256,
            file_count=len(template.files),
            total_bytes=sum(len(content) for content in template.files.values()),
        )
        preview = _build_preview(template.files)
        return TemplateSummary(
            id=template.id,
            name=template.name,
            description=template.description,
            icon=template.icon,
            category=template.category,
            cover_url=template.cover,
            author=template.author,
            tags=list(template.tags),
            preview=preview,
            current_release=release,
        )

    def _artifact(self, template_id: str) -> BuiltTemplateBundle:
        artifact = self._artifacts.get(template_id)
        if artifact is not None:
            return artifact
        template = TEMPLATES[template_id]
        artifact = build_template_bundle(
            template_id=template.id,
            release_id=template.version,
            files=template.files,
            settings=self._settings,
        )
        self._artifacts[template_id] = artifact
        return artifact


def _build_preview(files: dict[str, bytes], limit: int = 6) -> list[TemplatePreviewNode]:
    preview: list[TemplatePreviewNode] = []
    seen: set[str] = set()
    for path in files:
        head, separator, _tail = path.partition("/")
        display = f"{head}/" if separator else head
        if display in seen:
            continue
        seen.add(display)
        if separator:
            node_type = "folder"
        elif path.endswith(".md"):
            node_type = "markdown"
        elif path.endswith(".json"):
            node_type = "json"
        else:
            node_type = "file"
        preview.append(TemplatePreviewNode(name=display, type=node_type))
        if len(preview) >= limit:
            break
    return preview
