"""S3 storage module dependency injection"""

from src.infra.s3.service import S3Service, get_s3_service_instance


def get_s3_service() -> S3Service:
    """Return the process-wide S3 service."""
    return get_s3_service_instance()
