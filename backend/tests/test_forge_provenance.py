import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from app.core.config import settings
from app.services import forge_provenance
from app.services import fournos_k8s_client as k8s
from app.services import fournos_watcher as watcher


RUN_ID = "5c904b85a9224f28a6ece7e7d8defc56"
RUN_URL = f"https://mlflow.example/#/experiments/1/runs/{RUN_ID}"


def _response(request: httpx.Request, payload: dict) -> httpx.Response:
    return httpx.Response(200, request=request, content=json.dumps(payload))


def test_fetch_forge_versions_discovers_each_pipeline_artifact(monkeypatch):
    monkeypatch.setattr(settings, "MLFLOW_FAILURE_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(settings, "MLFLOW_TRACKING_URI", "https://mlflow.example")

    async def exercise():
        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            params = dict(request.url.params)
            if path.endswith("/artifacts/list"):
                directory = params.get("path", "")
                if directory == "":
                    return _response(request, {"files": [
                        {"path": "01__test", "is_dir": True},
                        {"path": "02__report", "is_dir": True},
                    ]})
                return _response(request, {"files": [{
                    "path": f"{directory}/000__ci_metadata/forge.git_version",
                    "is_dir": False,
                    "file_size": 8,
                }]})
            artifact = params["path"]
            version = "5415caf" if artifact.startswith("01__") else "8b63e10"
            return httpx.Response(200, request=request, text=version)

        async with httpx.AsyncClient(
            base_url="https://mlflow.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await forge_provenance.fetch_forge_versions(
                RUN_URL, client=client
            )

    result = asyncio.run(exercise())

    assert result.state == "complete"
    assert result.versions == [
        {
            "version": "5415caf",
            "artifactPath": "01__test/000__ci_metadata/forge.git_version",
        },
        {
            "version": "8b63e10",
            "artifactPath": "02__report/000__ci_metadata/forge.git_version",
        },
    ]


def test_fetch_forge_versions_rejects_untrusted_artifact_content(monkeypatch):
    monkeypatch.setattr(settings, "MLFLOW_FAILURE_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(settings, "MLFLOW_TRACKING_URI", "https://mlflow.example")

    async def exercise():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/artifacts/list"):
                return _response(request, {"files": [{
                    "path": "forge.git_version",
                    "is_dir": False,
                    "file_size": 20,
                }]})
            return httpx.Response(200, request=request, text="bad version value")

        async with httpx.AsyncClient(
            base_url="https://mlflow.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await forge_provenance.fetch_forge_versions(
                RUN_URL, client=client
            )

    result = asyncio.run(exercise())
    assert result.state == "malformed"
    assert result.versions == []


def test_fetch_forge_versions_bounds_malformed_file_reads(monkeypatch):
    monkeypatch.setattr(settings, "MLFLOW_TRACKING_URI", "https://mlflow.example")
    artifact_reads = 0

    async def exercise():
        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal artifact_reads
            if request.url.path.endswith("/artifacts/list"):
                return _response(request, {"files": [
                    {
                        "path": f"stage-{index}/forge.git_version",
                        "is_dir": False,
                        "file_size": 20,
                    }
                    for index in range(forge_provenance.MAX_VERSION_FILES + 20)
                ]})
            artifact_reads += 1
            return httpx.Response(200, request=request, text="bad version value")

        async with httpx.AsyncClient(
            base_url="https://mlflow.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await forge_provenance.fetch_forge_versions(
                RUN_URL, client=client
            )
            return result

    result = asyncio.run(exercise())
    assert result.state == "malformed"
    assert artifact_reads == forge_provenance.MAX_VERSION_FILES


def test_forge_image_evidence_uses_only_the_tekton_forge_step(monkeypatch):
    statuses = [
        SimpleNamespace(
            name="step-forge",
            image="quay.io/example/forge-core:latest",
            image_id="quay.io/example/forge-core@sha256:abc",
        ),
        SimpleNamespace(
            name="step-export",
            image="quay.io/example/exporter:latest",
            image_id="quay.io/example/exporter@sha256:def",
        ),
        SimpleNamespace(
            name="step-forge",
            image="quay.io/example/forge-core:next",
            image_id="",
        ),
    ]
    core_api = SimpleNamespace(
        list_namespaced_pod=lambda **_kwargs: SimpleNamespace(items=[
            SimpleNamespace(status=SimpleNamespace(container_statuses=statuses))
        ])
    )
    monkeypatch.setattr(k8s, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(k8s, "_core_api", core_api)

    assert k8s.get_forge_execution_images("job-1") == [{
        "image": "quay.io/example/forge-core:latest",
        "imageID": "quay.io/example/forge-core@sha256:abc",
        "container": "step-forge",
    }]


def test_forge_provenance_merge_preserves_distinct_observations():
    observed_at = datetime.now(timezone.utc)
    merged = watcher._merge_forge_execution(
        {
            "images": [{
                "image": "forge:latest",
                "imageID": "forge@sha256:old",
                "container": "step-forge",
            }],
            "gitVersions": [],
        },
        images=[{
            "image": "forge:latest",
            "imageID": "forge@sha256:new",
            "container": "step-forge",
        }],
        git_versions=[{
            "version": "5415caf",
            "artifactPath": "01__test/000__ci_metadata/forge.git_version",
        }],
        observed_at=observed_at,
    )

    assert len(merged["images"]) == 2
    assert merged["gitVersions"][0]["version"] == "5415caf"
    assert merged["observedAt"] == observed_at.isoformat()
    assert watcher._forge_provenance_state(
        merged,
        terminal=True,
        mlflow_url=RUN_URL,
        fetch_state="complete",
        attempts=1,
    ) == "complete"


def test_forge_provenance_retries_are_bounded_and_skip_migrated_history():
    now = datetime.now(timezone.utc)
    assert watcher._forge_provenance_retry_due(0, None, now) is True
    assert watcher._forge_provenance_retry_due(1, now, now) is False
    assert watcher._forge_provenance_retry_due(
        1,
        now - timedelta(seconds=watcher.FORGE_PROVENANCE_RETRY_SECONDS + 1),
        now,
    ) is True
    assert watcher._forge_provenance_retry_due(
        watcher.FORGE_PROVENANCE_MAX_ATTEMPTS, None, now
    ) is False
    assert watcher._should_fetch_forge_provenance(
        mlflow_url=RUN_URL,
        state="pending",
        retry_due=True,
        transitioning_into_terminal=False,
        attempts=0,
    ) is False
    assert watcher._should_fetch_forge_provenance(
        mlflow_url=RUN_URL,
        state="pending",
        retry_due=True,
        transitioning_into_terminal=True,
        attempts=0,
    ) is True
    assert watcher._should_fetch_forge_provenance(
        mlflow_url=RUN_URL,
        state="partial",
        retry_due=True,
        transitioning_into_terminal=False,
        attempts=0,
    ) is True
