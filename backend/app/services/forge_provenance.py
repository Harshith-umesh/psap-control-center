"""Collect durable evidence of the Forge revision that actually executed."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import settings
from app.services.mlflow_failure_provider import _client_options, parse_run_reference


FORGE_VERSION_FILENAME = "forge.git_version"
MAX_ARTIFACT_ENTRIES = 1000
MAX_ARTIFACT_LIST_REQUESTS = 50
MAX_DIRECTORY_CONCURRENCY = 8
MAX_VERSION_FILES = 64
MAX_ARTIFACT_PATH_LENGTH = 1024
MAX_VERSION_BYTES = 256
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}$")


@dataclass
class ForgeVersionResult:
    state: str
    versions: list[dict] = field(default_factory=list)


async def _read_version(
    client: httpx.AsyncClient,
    run_id: str,
    artifact_path: str,
    file_size: Optional[int],
) -> Optional[dict]:
    if file_size is not None and file_size > MAX_VERSION_BYTES:
        return None
    response = await client.get(
        "/get-artifact",
        params={"run_uuid": run_id, "path": artifact_path},
    )
    response.raise_for_status()
    if len(response.content) > MAX_VERSION_BYTES:
        return None
    version = response.text.strip()
    if not _VERSION.fullmatch(version):
        return None
    return {"version": version, "artifactPath": artifact_path}


async def _list_directory(
    client: httpx.AsyncClient, run_id: str, path: str
) -> list[dict]:
    response = await client.get(
        "/api/2.0/mlflow/artifacts/list",
        params={"run_id": run_id, "path": path},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("MLflow artifact response must be an object")
    return [item for item in payload.get("files", []) if isinstance(item, dict)]


async def _discover_versions(
    client: httpx.AsyncClient, run_id: str
) -> ForgeVersionResult:
    queue = [""]
    visited: set[str] = set()
    versions: list[dict] = []
    version_files_seen = False
    version_files_read = 0
    entries_seen = 0
    requests = 0

    while (
        queue
        and entries_seen < MAX_ARTIFACT_ENTRIES
        and requests < MAX_ARTIFACT_LIST_REQUESTS
    ):
        batch = []
        while queue and len(batch) < MAX_DIRECTORY_CONCURRENCY:
            path = queue.pop(0)
            if path not in visited:
                visited.add(path)
                batch.append(path)
        if not batch:
            continue
        remaining = MAX_ARTIFACT_LIST_REQUESTS - requests
        batch = batch[:remaining]
        listings = await asyncio.gather(
            *(_list_directory(client, run_id, path) for path in batch)
        )
        requests += len(batch)

        version_candidates = []
        for files in listings:
            entries_seen += len(files)
            for item in files:
                path = item.get("path", "")
                if item.get("is_dir") and path:
                    queue.append(path)
                elif (
                    len(path) <= MAX_ARTIFACT_PATH_LENGTH
                    and path.rsplit("/", 1)[-1] == FORGE_VERSION_FILENAME
                ):
                    version_files_seen = True
                    version_candidates.append(item)

        if version_candidates:
            remaining_files = MAX_VERSION_FILES - version_files_read
            for start in range(0, min(len(version_candidates), remaining_files), 8):
                candidate_batch = version_candidates[start:start + 8]
                version_files_read += len(candidate_batch)
                read_results = await asyncio.gather(
                    *(
                        _read_version(
                            client,
                            run_id,
                            item["path"],
                            item.get("file_size"),
                        )
                        for item in candidate_batch
                    )
                )
                versions.extend(result for result in read_results if result)

    unique = {
        (item["version"], item["artifactPath"]): item for item in versions
    }
    ordered = [unique[key] for key in sorted(unique)]
    if queue:
        return ForgeVersionResult(state="exhausted", versions=ordered)
    if ordered:
        return ForgeVersionResult(state="complete", versions=ordered)
    return ForgeVersionResult(
        state="malformed" if version_files_seen else "pending"
    )


async def fetch_forge_versions(
    run_url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> ForgeVersionResult:
    """Read Forge's own git-version artifact from an authenticated MLflow run."""
    if not settings.MLFLOW_TRACKING_URI:
        return ForgeVersionResult(state="disabled")
    reference = parse_run_reference(run_url)
    if not reference:
        return ForgeVersionResult(state="invalid_reference")
    run_id, workspace, _artifact_hint = reference

    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(**_client_options(workspace))
        return await _discover_versions(client, run_id)
    except (httpx.HTTPError, ValueError):
        return ForgeVersionResult(state="pending")
    finally:
        if owns_client and client is not None:
            await client.aclose()
