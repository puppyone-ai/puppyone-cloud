"""Typed failures surfaced by the data migration CLI."""


class DataMigrationError(RuntimeError):
    """Base class for an operator-actionable migration failure."""


class ManifestError(DataMigrationError):
    """A repository migration artifact is malformed or unsafe."""


class PrerequisiteError(DataMigrationError):
    """The target database or runtime is not ready for the migration."""


class ImmutableArtifactError(DataMigrationError):
    """A completed migration ID now resolves to different content."""


class MigrationBusyError(DataMigrationError):
    """Another process already owns the target migration lock."""


class ExecutionError(DataMigrationError):
    """Migration execution or verification failed."""
