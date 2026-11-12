"""Read generic Caliper completion metadata from an authenticated MLflow run."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
import yaml

from app.core.config import settings
from app.services.failure_details import normalize_reason


METADATA_FILENAME = "__caliper_test_metadata__.yaml"
MAX_ARTIFACT_ENTRIES = 1000
MAX_ARTIFACT_LIST_REQUESTS = 50
MAX_METADATA_BYTES = 64 * 1024
_RUN_ID = re.compile(
    r"^(?:[0-9a-fA-F]{32}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_WORKSPACE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass
class MlflowCompletionResult:
    state: str
    first_failure: Optional[dict] = None
    test_results: list[dict] = field(default_factory=list)


def parse_run_reference(run_url: str) -> Optional[tuple[str, str, str]]:
    parsed = urlparse(run_url or "")
    fragment_path, _, fragment_query = parsed.fragment.partition("?")
    route = parsed.path if "/runs/" in parsed.path else fragment_path
    match = re.search(r"/runs/([^/?#]+)", route)
    if not match or not _RUN_ID.fullmatch(match.group(1)):
        return None
    query = parse_qs(parsed.query)
    if fragment_query:
        query.update(parse_qs(fragment_query))
    workspace = (query.get("workspace") or [""])[0]
    workspace = workspace or (settings.MLFLOW_WORKSPACE or "")
    if workspace and not _WORKSPACE.fullmatch(workspace):
        return None
    artifact_hint = ""
    artifact_match = re.search(r"/artifacts/(.+)$", route)
    if artifact_match:
        artifact_hint = artifact_match.group(1).strip("/")
    return match.group(1), workspace, artifact_hint


def _client_options(workspace: str) -> dict:
    headers = {"X-MLFLOW-WORKSPACE": workspace} if workspace else {}
    auth = None
    if settings.MLFLOW_TRACKING_USERNAME and settings.MLFLOW_TRACKING_PASSWORD:
        auth = httpx.BasicAuth(
            settings.MLFLOW_TRACKING_USERNAME,
            settings.MLFLOW_TRACKING_PASSWORD,
        )
    return {
        "base_url": str(settings.MLFLOW_TRACKING_URI).rstrip("/"),
        "headers": headers,
        "auth": auth,
        "verify": not settings.MLFLOW_TRACKING_INSECURE_TLS,
        "timeout": settings.MLFLOW_REQUEST_TIMEOUT_SECONDS,
        "follow_redirects": False,
    }


async def _read_metadata(
    client: httpx.AsyncClient,
    run_id: str,
    artifact_path: str,
    file_size: Optional[int] = None,
) -> Optional[dict]:
    if file_size is not None and file_size > MAX_METADATA_BYTES:
        return None
    response = await client.get(
        "/get-artifact",
        params={"run_uuid": run_id, "path": artifact_path},
    )
    response.raise_for_status()
    if len(response.content) > MAX_METADATA_BYTES:
        return None
    try:
        document = yaml.safe_load(response.text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    completion = document.get("completion")
    if not isinstance(completion, dict) or not isinstance(
        completion.get("success"), bool
    ):
        return None
    return {
        "success": completion["success"],
        "reason": normalize_reason(completion.get("message")),
        "artifactPath": artifact_path,
    }


def _directory_priority(path: str) -> tuple[int, str]:
    lowered = path.lower()
    return (0 if "test" in lowered else 1, path)


async def _discover_completions(
    client: httpx.AsyncClient, run_id: str
) -> MlflowCompletionResult:
    # Depth-first traversal reaches concrete test leaves quickly. Test-named
    # directories are preferred, but the provider remains schema-based and
    # does not assume one project's artifact layout.
    stack = [""]
    visited_directories = set()
    visited_entries = 0
    list_requests = 0
    results: list[dict] = []
    metadata_seen = False

    while (
        stack
        and visited_entries < MAX_ARTIFACT_ENTRIES
        and list_requests < MAX_ARTIFACT_LIST_REQUESTS
    ):
        path = stack.pop()
        if path in visited_directories:
            continue
        visited_directories.add(path)
        response = await client.get(
            "/api/2.0/mlflow/artifacts/list",
            params={"run_id": run_id, "path": path},
        )
        response.raise_for_status()
        list_requests += 1
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MLflow artifact response must be an object")
        files = [item for item in payload.get("files", []) if isinstance(item, dict)]
        visited_entries += len(files)

        metadata_files = sorted(
            (
                item for item in files
                if not item.get("is_dir")
                and item.get("path", "").rsplit("/", 1)[-1]
                == METADATA_FILENAME
            ),
            key=lambda item: item.get("path", ""),
        )
        metadata_seen = metadata_seen or bool(metadata_files)
        for item in metadata_files:
            result = await _read_metadata(
                client,
                run_id,
                item["path"],
                item.get("file_size"),
            )
            if result is None:
                continue
            results.append(result)
            if result["success"] is False:
                return MlflowCompletionResult(
                    state="complete",
                    first_failure=result,
                    test_results=results,
                )

        directories = sorted(
            (
                item.get("path", "") for item in files
                if item.get("is_dir") and item.get("path")
            ),
            key=_directory_priority,
        )
        stack.extend(reversed(directories))

    if stack:
        return MlflowCompletionResult(state="exhausted", test_results=results)
    if results:
        return MlflowCompletionResult(state="complete", test_results=results)
    return MlflowCompletionResult(
        state="malformed" if metadata_seen else "pending"
    )


async def fetch_caliper_completion(
    run_url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> MlflowCompletionResult:
    """Return Caliper test results; failures remain non-fatal to archival."""
    if not settings.MLFLOW_FAILURE_ENRICHMENT_ENABLED:
        return MlflowCompletionResult(state="disabled")
    if not settings.MLFLOW_TRACKING_URI:
        return MlflowCompletionResult(state="disabled")
    reference = parse_run_reference(run_url)
    if not reference:
        return MlflowCompletionResult(state="invalid_reference")
    run_id, workspace, artifact_hint = reference

    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(**_client_options(workspace))
        if artifact_hint.rsplit("/", 1)[-1] == METADATA_FILENAME:
            hinted = await _read_metadata(client, run_id, artifact_hint)
            if hinted is not None:
                return MlflowCompletionResult(
                    state="complete",
                    first_failure=(hinted if hinted["success"] is False else None),
                    test_results=[hinted],
                )
            return MlflowCompletionResult(state="malformed")
        return await _discover_completions(client, run_id)
    except (httpx.HTTPError, ValueError):
        return MlflowCompletionResult(state="pending")
    finally:
        if owns_client and client is not None:
            await client.aclose()
