"""Sandbox file handling utility functions."""

import asyncio
import os
from typing import Any, Optional
from src.config import settings


# Large file memory warning threshold (100MB)
LARGE_FILE_MEMORY_WARNING_THRESHOLD = 100 * 1024 * 1024


async def download_files_parallel(
    files: list,
    s3_service: Optional[Any],
    max_concurrent: Optional[int] = None
) -> list[tuple[str, Any, Optional[str]]]:
    """
    Download file contents in parallel

    Args:
        files: List of SandboxFile, each containing path, content, s3_key
        s3_service: S3 service instance
        max_concurrent: Maximum concurrency, defaults to config value

    Returns:
        List of [(path, content, error), ...]
        - content: File content (str/bytes) or None (if download failed)
        - error: Error message or None
    """
    if max_concurrent is None:
        max_concurrent = settings.SANDBOX_DOWNLOAD_CONCURRENCY

    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_one(f) -> tuple[str, Any, Optional[str]]:
        """Download a single file"""
        async with semaphore:
            # Support both dict and object forms
            path = f.get("path") if isinstance(f, dict) else getattr(f, "path", None)
            content = f.get("content") if isinstance(f, dict) else getattr(f, "content", None)
            s3_key = f.get("s3_key") if isinstance(f, dict) else getattr(f, "s3_key", None)

            if not path:
                return ("", None, "No path specified")

            # If content already exists, return directly
            if content is not None:
                return (path, content, None)

            # Download from S3
            if s3_key and s3_service:
                try:
                    # Check file size
                    file_size = await _get_file_size(s3_service, s3_key)

                    if file_size and file_size > LARGE_FILE_MEMORY_WARNING_THRESHOLD:
                        print(f"[file_utils] Warning: downloading large file ({file_size / 1024 / 1024:.1f}MB) to memory: {s3_key}")

                    if file_size and file_size > settings.SANDBOX_LARGE_FILE_THRESHOLD:
                        # Large file: use chunked download (reduce peak memory)
                        content = await _download_file_chunked(s3_service, s3_key)
                    else:
                        # Small file: download directly
                        content = await s3_service.download_file(s3_key)

                    return (path, content, None)
                except Exception as e:
                    return (path, None, f"Failed to download from S3: {e}")

            # No content and no S3 key
            return (path, "", None)  # Return empty string to represent an empty file

    # Download all files in parallel
    results = await asyncio.gather(*[download_one(f) for f in files])
    return list(results)


async def _get_file_size(s3_service: Any, s3_key: str) -> Optional[int]:
    """Get S3 file size"""
    try:
        metadata = await s3_service.get_file_metadata(s3_key)
        return metadata.size
    except Exception:
        return None


async def _download_file_chunked(s3_service: Any, s3_key: str) -> bytes:
    """
    Download a file in chunks

    Note: This method still loads the complete file into memory, but chunked downloading:
    1. Reduces peak memory allocation per chunk
    2. Allows releasing processed data during download
    3. Provides better progress visibility

    For truly large file handling, use download_file_to_disk to write directly to disk
    """
    chunks = []
    total_size = 0
    async for chunk in s3_service.download_file_stream(s3_key):
        chunks.append(chunk)
        total_size += len(chunk)
        # Print progress every 10MB
        if total_size % (10 * 1024 * 1024) < len(chunk):
            print(f"[file_utils] Downloaded {total_size / 1024 / 1024:.1f}MB of {s3_key}")

    return b"".join(chunks)


async def download_file_to_disk(
    s3_service: Any,
    s3_key: str,
    local_path: str
) -> int:
    """
    Stream download a file to local disk (without consuming large amounts of memory)

    Args:
        s3_service: S3 service instance
        s3_key: S3 file key
        local_path: Local file path

    Returns:
        Number of bytes written
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    total_size = 0
    with open(local_path, "wb") as f:
        async for chunk in s3_service.download_file_stream(s3_key):
            f.write(chunk)
            total_size += len(chunk)

    return total_size


async def prepare_files_for_sandbox(
    files: list,
    s3_service: Optional[Any] = None
) -> tuple[list[dict], list[dict]]:
    """
    Prepare sandbox files

    Download all files in parallel, return prepared file list and failure list

    Args:
        files: List of SandboxFile
        s3_service: S3 service instance

    Returns:
        (prepared_files, failed_files)
        - prepared_files: [{"path": str, "content": str/bytes}, ...]
        - failed_files: [{"path": str, "error": str}, ...]
    """
    results = await download_files_parallel(files, s3_service)

    prepared_files = []
    failed_files = []

    for path, content, error in results:
        if not path:
            continue

        if error:
            failed_files.append({"path": path, "error": error})
        else:
            prepared_files.append({"path": path, "content": content})

    return prepared_files, failed_files


async def prepare_files_for_docker_sandbox(
    files: list,
    workspace_dir: str,
    s3_service: Optional[Any] = None
) -> tuple[list[str], list[dict]]:
    """
    Prepare files for Docker sandbox, large files are written directly to disk

    Args:
        files: List of SandboxFile
        workspace_dir: Local workspace directory
        s3_service: S3 service instance

    Returns:
        (written_paths, failed_files)
        - written_paths: List of successfully written file paths
        - failed_files: [{"path": str, "error": str}, ...]
    """
    semaphore = asyncio.Semaphore(settings.SANDBOX_DOWNLOAD_CONCURRENCY)

    async def process_one(f) -> tuple[Optional[str], Optional[dict]]:
        """Process a single file"""
        async with semaphore:
            # Support both dict and object forms
            path = f.get("path") if isinstance(f, dict) else getattr(f, "path", None)
            content = f.get("content") if isinstance(f, dict) else getattr(f, "content", None)
            s3_key = f.get("s3_key") if isinstance(f, dict) else getattr(f, "s3_key", None)

            if not path:
                return (None, {"path": "", "error": "No path specified"})

            # Calculate local path
            relative_path = path
            if path.startswith("/workspace/"):
                relative_path = path[len("/workspace/"):]
            elif path.startswith("/"):
                relative_path = path[1:]

            # Security check
            normalized_path = os.path.normpath(relative_path)
            if normalized_path.startswith(("..", "/")):
                return (None, {"path": path, "error": "Path traversal detected"})

            local_path = os.path.join(workspace_dir, normalized_path)

            # Secondary validation
            real_local_path = os.path.realpath(local_path)
            real_workspace_dir = os.path.realpath(workspace_dir)
            if not real_local_path.startswith(real_workspace_dir + os.sep) and real_local_path != real_workspace_dir:
                return (None, {"path": path, "error": "Path traversal detected (resolved)"})

            # Ensure directory exists
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
            except Exception as e:
                return (None, {"path": path, "error": f"Failed to create directory: {e}"})

            try:
                # If content already exists, write directly
                if content is not None:
                    if isinstance(content, bytes):
                        with open(local_path, "wb") as file:
                            file.write(content)
                    elif isinstance(content, (dict, list)):
                        import json
                        with open(local_path, "w", encoding="utf-8") as file:
                            json.dump(content, file, ensure_ascii=False, indent=2)
                    else:
                        with open(local_path, "w", encoding="utf-8") as file:
                            file.write(str(content))
                    return (local_path, None)

                # Download from S3
                if s3_key and s3_service:
                    file_size = await _get_file_size(s3_service, s3_key)

                    if file_size and file_size > settings.SANDBOX_LARGE_FILE_THRESHOLD:
                        # Large file: stream directly to disk
                        await download_file_to_disk(s3_service, s3_key, local_path)
                    else:
                        # Small file: download to memory then write
                        file_content = await s3_service.download_file(s3_key)
                        with open(local_path, "wb") as file:
                            file.write(file_content)
                    return (local_path, None)

                # No content and no S3 key, create empty file
                with open(local_path, "w", encoding="utf-8") as file:
                    pass
                return (local_path, None)

            except Exception as e:
                return (None, {"path": path, "error": str(e)})

    # Process all files in parallel
    results = await asyncio.gather(*[process_one(f) for f in files])

    written_paths = []
    failed_files = []

    for written_path, failure in results:
        if failure:
            failed_files.append(failure)
        elif written_path:
            written_paths.append(written_path)

    return written_paths, failed_files
