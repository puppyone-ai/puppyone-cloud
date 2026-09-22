"""Template Registry domain errors.

The domain never raises ``HTTPException``. Routers translate these typed
failures at the application boundary so providers and bundle validation remain
usable from tests, workers, and future publisher tooling.
"""


class TemplateRegistryError(RuntimeError):
    """Base class for Registry and release failures."""


class TemplateRegistryUnavailableError(TemplateRegistryError):
    """The configured Registry cannot currently serve the request."""


class TemplateRegistryUpstreamError(TemplateRegistryError):
    """The remote Registry returned an invalid or unsuccessful response."""


class TemplateNotFoundError(TemplateRegistryError):
    """No visible template exists for the requested ID."""


class TemplateReleaseNotFoundError(TemplateRegistryError):
    """The requested immutable release does not exist."""


class TemplateBundleInvalidError(TemplateRegistryError):
    """A release bundle failed structural, integrity, or trust validation."""


class TemplateBundleTooLargeError(TemplateBundleInvalidError):
    """A release exceeded a configured compressed or expanded resource limit."""
