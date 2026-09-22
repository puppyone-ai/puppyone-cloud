"""Stable HTTP mapping for Template Registry application errors."""

from fastapi import HTTPException

from .exceptions import (
    TemplateBundleInvalidError,
    TemplateNotFoundError,
    TemplateRegistryError,
    TemplateRegistryUnavailableError,
    TemplateRegistryUpstreamError,
    TemplateReleaseNotFoundError,
)


def registry_http_exception(error: TemplateRegistryError) -> HTTPException:
    if isinstance(error, (TemplateNotFoundError, TemplateReleaseNotFoundError)):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, TemplateRegistryUnavailableError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, (TemplateRegistryUpstreamError, TemplateBundleInvalidError)):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=500, detail="Template Registry operation failed")
