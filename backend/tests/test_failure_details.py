import asyncio

import httpx

from app.services import failure_details
from app.services import fournos_k8s_client as k8s
from app.services import mlflow_failure_provider as mlflow
from app.schemas.fournos import FournosJobDetailResponse


RUN_ID = "5c904b85a9224f28a6ece7e7d8defc56"


def _terminal_pipeline(child_name: str = "test-run") -> dict:
    return {
        "status": {
            "conditions": [{"reason": "Succeeded", "status": "True"}],
            "childReferences": [
                {"name": child_name, "pipelineTaskName": "test"}
            ],
            "pipelineSpec": {"tasks": [{"name": "test"}]},
        }
    }


def test_failed_taskrun_preserves_first_failed_inner_step(monkeypatch):
    monkeypatch.setattr(
        k8s,
        "get_taskrun",
        lambda _name: {
            "status": {
                "conditions": [
                    {
                        "reason": "Failed",
                        "status": "False",
                        "message": "Tasks Completed: 0 (Failed: 1)",
                    }
                ],
                "steps": [
                    {
                        "name": "run-benchmark",
                        "terminated": {
                            "exitCode": 2,
                            "reason": "Error",
                            "message": "benchmark command failed",
                        },
                    }
                ],
            }
        },
    )

    stage = k8s.extract_pipeline_stages(_terminal_pipeline())[0]

    assert stage["status"] == "Failed"
    assert stage["outcome"] == "failed"
    assert stage["failedStep"] == "run-benchmark"
    assert stage["reason"] == "benchmark command failed"
    assert stage["reasonSource"] == "step_termination"
    assert stage["exitCode"] == 2


def test_known_platform_reason_is_classified_as_infrastructure(monkeypatch):
    monkeypatch.setattr(
        k8s,
        "get_taskrun",
        lambda _name: {
            "status": {
                "conditions": [
                    {
                        "reason": "TaskRunTimeout",
                        "status": "False",
                        "message": "task exceeded platform deadline",
                    }
                ]
            }
        },
    )

    stage = k8s.extract_pipeline_stages(_terminal_pipeline())[0]

    assert stage["status"] == "Failed"
    assert stage["outcome"] == "infrastructure_error"
    assert stage["reasonCode"] == "TaskRunTimeout"


def test_cancellation_reason_variants_are_not_generic_failures():
    for reason in (
        "TaskRunCancelled",
        "PipelineRunCancelled",
        "Cancelled",
        "StoppedRunFinally",
    ):
        assert k8s._phase_from_conditions(
            [{"reason": reason, "status": "False"}]
        ) == "Cancelled"


def test_deleted_taskrun_is_archived_as_unknown_not_pending(monkeypatch):
    monkeypatch.setattr(k8s, "get_taskrun", lambda _name: None)

    stage = k8s.extract_pipeline_stages(_terminal_pipeline())[0]

    assert stage["status"] == "Unknown"
    assert stage["outcome"] == "unknown"
    assert stage["reasonCode"] == "TASKRUN_UNAVAILABLE"


def test_taskrun_authorization_failure_is_explicit_for_live_detail(monkeypatch):
    monkeypatch.setattr(
        k8s,
        "get_taskrun",
        lambda _name: (_ for _ in ()).throw(
            k8s.TaskRunLookupError("test-run", 403, "Forbidden")
        ),
    )

    stage = k8s.extract_pipeline_stages(_terminal_pipeline())[0]

    assert stage["status"] == "Unknown"
    assert stage["outcome"] == "unknown"
    assert stage["reasonCode"] == "TASKRUN_ACCESS_DENIED"
    assert stage["reasonSource"] == "tekton_api"


def test_strict_taskrun_lookup_preserves_authorization_error(monkeypatch):
    monkeypatch.setattr(
        k8s,
        "get_taskrun",
        lambda _name: (_ for _ in ()).throw(
            k8s.TaskRunLookupError("test-run", 403, "Forbidden")
        ),
    )

    try:
        k8s.extract_pipeline_stages(
            _terminal_pipeline(), strict_lookup_errors=True
        )
    except k8s.TaskRunLookupError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("strict lookup must preserve authorization errors")


def test_first_actionable_failure_ignores_skipped_and_not_run_stages():
    summary = failure_details.first_actionable_failure(
        [
            {"name": "optional", "status": "Skipped", "outcome": "skipped"},
            {"name": "deploy", "status": "NotRun", "outcome": "not_run"},
            {
                "name": "test",
                "displayName": "Test",
                "status": "Failed",
                "outcome": "failed",
                "failedStep": "benchmark",
                "reason": "connection refused",
                "reasonCode": "Error",
                "reasonSource": "step_termination",
            },
        ],
        job_phase="Failed",
    )

    assert summary == {
        "outcome": "failed",
        "stage": "test",
        "stageDisplayName": "Test",
        "step": "benchmark",
        "reason": "connection refused",
        "reasonCode": "Error",
        "source": "step_termination",
        "artifactPath": "",
    }


def test_failure_reasons_redact_common_credentials():
    reason = failure_details.normalize_reason(
        "authorization Bearer abcdefghijklmnop token=top-secret "
        "https://user:password@example.com/path"
    )

    assert "abcdefghijklmnop" not in reason
    assert "top-secret" not in reason
    assert "user:password" not in reason
    assert reason.count("[REDACTED]") == 3


def test_job_detail_contract_serializes_normalized_failure_fields():
    response = FournosJobDetailResponse.model_validate(
        {
            "job": {
                "metadata": {"name": "job-1"},
                "spec": {},
                "status": {"phase": "Failed"},
                "source": "history",
            },
            "stages": [
                {
                    "name": "test",
                    "displayName": "Test",
                    "status": "Failed",
                    "finally": False,
                    "outcome": "failed",
                    "reason": "benchmark failed",
                    "reasonCode": "Error",
                    "reasonSource": "step_termination",
                    "failedStep": "benchmark",
                    "exitCode": 1,
                }
            ],
            "failure_summary": {
                "outcome": "failed",
                "stage": "test",
                "stageDisplayName": "Test",
                "step": "benchmark",
                "reason": "benchmark failed",
                "reasonCode": "Error",
                "source": "step_termination",
                "artifactPath": "",
            },
            "failure_enrichment_state": "complete",
            "forge_execution": {
                "images": [{
                    "image": "forge:latest",
                    "imageID": "forge@sha256:abc",
                    "container": "step-forge",
                }],
                "gitVersions": [{
                    "version": "5415caf",
                    "artifactPath": "01__test/000__ci_metadata/forge.git_version",
                }],
            },
            "forge_provenance_state": "complete",
        }
    ).model_dump(by_alias=True)

    assert response["stages"][0]["finally"] is False
    assert response["failure_summary"]["step"] == "benchmark"
    assert response["forge_execution"]["gitVersions"][0]["version"] == "5415caf"


def test_job_detail_contract_normalizes_empty_success_failure_summary():
    response = FournosJobDetailResponse.model_validate(
        {
            "job": {
                "metadata": {"name": "successful-job"},
                "spec": {},
                "status": {"phase": "Succeeded"},
                "source": "history",
            },
            "failure_summary": {},
        }
    )

    assert response.failure_summary is None


def test_caliper_failure_enriches_test_failure_but_not_infrastructure():
    execution = {
        "outcome": "failed",
        "stage": "test",
        "stageDisplayName": "Test",
        "step": "benchmark",
        "reason": "command returned 1",
        "reasonCode": "Error",
        "source": "step_termination",
        "artifactPath": "",
    }
    caliper = {
        "reason": "Test failed: Configuration requested a failure",
        "artifactPath": "01__test/smoke/__caliper_test_metadata__.yaml",
    }

    enriched = failure_details.merge_caliper_failure(execution, caliper)

    assert enriched["source"] == "mlflow_caliper"
    assert enriched["reasonCode"] == "CALIPER_TEST_FAILED"
    assert enriched["executionReason"] == "command returned 1"

    infrastructure = {**execution, "outcome": "infrastructure_error"}
    assert failure_details.merge_caliper_failure(
        infrastructure, caliper
    ) == infrastructure


def test_fresh_concrete_failure_replaces_stale_archived_unknown():
    live = {
        "outcome": "failed",
        "stage": "test",
        "step": "benchmark",
        "reason": "command exited with code 1",
        "reasonCode": "Error",
        "source": "step_termination",
    }
    archived = {
        "outcome": "unknown",
        "stage": "",
        "step": "",
        "reason": "details unavailable",
        "source": "unknown",
    }

    assert failure_details.select_failure_summary(live, archived) == live


def test_archived_mlflow_detail_remains_stronger_than_live_execution():
    live = {
        "outcome": "failed",
        "stage": "test",
        "reason": "command exited with code 1",
        "source": "step_termination",
    }
    archived = {
        "outcome": "failed",
        "stage": "test",
        "reason": "Configuration requested a failure",
        "source": "mlflow_caliper",
    }

    assert failure_details.select_failure_summary(live, archived) == archived


def test_parse_mlflow_fragment_url_uses_recorded_workspace(monkeypatch):
    monkeypatch.setattr(mlflow.settings, "MLFLOW_WORKSPACE", "fallback")

    reference = mlflow.parse_run_reference(
        "https://mlflow.example/#/experiments/240/runs/"
        f"{RUN_ID}/artifacts/file.yaml?workspace=forge-sandbox"
    )

    assert reference == (
        RUN_ID,
        "forge-sandbox",
        "file.yaml",
    )


def test_fetch_caliper_completion_discovers_metadata_recursively(monkeypatch):
    monkeypatch.setattr(
        mlflow.settings, "MLFLOW_TRACKING_URI", "https://mlflow.example"
    )
    monkeypatch.setattr(
        mlflow.settings, "MLFLOW_FAILURE_ENRICHMENT_ENABLED", True
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/2.0/mlflow/artifacts/list":
            path = request.url.params.get("path")
            if path == "":
                return httpx.Response(
                    200,
                    json={"files": [{"path": "01__test", "is_dir": True}]},
                )
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "path": f"{path}/{mlflow.METADATA_FILENAME}",
                            "is_dir": False,
                            "file_size": 120,
                        }
                    ]
                },
            )
        if request.url.path == "/get-artifact":
            return httpx.Response(
                200,
                text=(
                    "version: '1'\nlabels: {}\ncompletion:\n"
                    "  success: false\n  message: benchmark failed\n"
                ),
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def exercise():
        async with httpx.AsyncClient(
            base_url="https://mlflow.example",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await mlflow.fetch_caliper_completion(
                "https://mlflow.example/#/experiments/1/runs/"
                f"{RUN_ID}?workspace=forge-sandbox",
                client=client,
            )

    result = asyncio.run(exercise())

    assert result.state == "complete"
    assert result.first_failure == {
        "success": False,
        "reason": "benchmark failed",
        "artifactPath": f"01__test/{mlflow.METADATA_FILENAME}",
    }
