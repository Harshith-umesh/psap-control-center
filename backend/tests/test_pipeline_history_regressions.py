import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services import fournos_k8s_client as k8s
from app.services import fournos_watcher as watcher
from app.services.source_provenance import extract_source_request_fields
from app.api import fournos as fournos_api
from app.core import database as database_core
from app.services import fournos_db_service as db_service
from app.services import project_ui_schema
from app.schemas.fournos import (
    PullRequestSelection,
    SubmitJobRequest,
    SubmitMatrixModelInput,
    SubmitMatrixRequest,
)


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    def begin(self):
        return _AsyncContext()


def test_log_stream_terminal_sentinel_displaces_oldest_item_when_full():
    async def exercise_queue():
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait("old-line")

        fournos_api._enqueue_stream_item(queue, "dropped-line")
        assert queue.get_nowait() == "old-line"

        queue.put_nowait("last-line")
        fournos_api._enqueue_stream_item(queue, None)
        assert queue.get_nowait() is None

    asyncio.run(exercise_queue())


def test_pod_logs_are_processed_and_downloaded_in_bounded_chunks(monkeypatch):
    class FakeResponse:
        def __init__(self, chunks):
            self.chunks = chunks
            self.released = False
            self.requested_chunk_sizes = []

        def stream(self, amt):
            self.requested_chunk_sizes.append(amt)
            yield from self.chunks

        def release_conn(self):
            self.released = True

    line_response = FakeResponse([b"first\nsec", b"ond\nthird"])
    byte_response = FakeResponse([b"first\n", b"second\n"])
    core_api = MagicMock()
    core_api.read_namespaced_pod_log.side_effect = [line_response, byte_response]
    monkeypatch.setattr(k8s, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(k8s, "_core_api", core_api)

    assert list(k8s.read_pod_log("pod-a", follow=False)) == [
        "first", "second", "third"
    ]
    assert list(k8s.stream_pod_log_bytes("pod-a")) == [
        b"first\n", b"second\n"
    ]
    assert line_response.released is True
    assert byte_response.released is True
    assert all(
        call.kwargs["_preload_content"] is False
        for call in core_api.read_namespaced_pod_log.call_args_list
    )


def test_taskrun_condition_specific_terminal_reasons_win_over_false_status():
    assert k8s._phase_from_conditions(
        [{"reason": "TaskRunCancelled", "status": "False"}]
    ) == "Cancelled"
    assert k8s._phase_from_conditions(
        [{"reason": "SkippingNoMatch", "status": "False"}]
    ) == "Skipped"


def test_extract_pipeline_stages_includes_tekton_skipped_tasks(monkeypatch):
    monkeypatch.setattr(
        k8s,
        "get_taskrun",
        lambda _name: {
            "status": {
                "conditions": [{"reason": "Succeeded", "status": "True"}],
                "startTime": "2026-09-03T12:00:00Z",
                "completionTime": "2026-09-03T12:01:00Z",
            }
        },
    )
    pipelinerun = {
        "status": {
            "childReferences": [
                {"name": "prepare-run", "pipelineTaskName": "prepare"}
            ],
            "skippedTasks": [{"name": "optional-check"}],
            "pipelineSpec": {
                "tasks": [{"name": "prepare"}, {"name": "optional-check"}]
            },
        }
    }

    stages = k8s.extract_pipeline_stages(pipelinerun)

    assert {stage["name"]: stage["status"] for stage in stages} == {
        "prepare": "Succeeded",
        "optional-check": "Skipped",
    }


def test_terminal_snapshot_requires_a_complete_pipelinerun(monkeypatch):
    monkeypatch.setattr(k8s, "get_pipelinerun", lambda _name: None)
    monkeypatch.setattr(k8s, "list_pipelineruns_for_job", lambda _name: [])

    assert watcher._compute_terminal_stages(
        "job-1", {"pipeline": "forge-test-only"}, {"pipelineRun": "missing"}
    ) is None


def test_terminal_snapshot_marks_unstarted_definition_tasks_not_run(monkeypatch):
    monkeypatch.setattr(k8s, "get_pipelinerun", lambda _name: {"status": {}})
    monkeypatch.setattr(
        k8s,
        "extract_pipeline_stages",
        lambda _pr, **_kwargs: [
            {
                "name": "prepare",
                "displayName": "Prepare",
                "status": "Failed",
                "startTime": "2026-09-03T12:00:00Z",
                "completionTime": "2026-09-03T12:01:00Z",
                "finally": False,
            }
        ],
    )
    monkeypatch.setattr(
        watcher.pipeline_definitions,
        "get_definition_sync",
        lambda _name: {
            "name": "forge-test-only",
            "tasks": ["prepare", "test"],
            "finally": [],
        },
    )

    stages = watcher._compute_terminal_stages(
        "job-1", {"pipeline": "forge-test-only"}, {"pipelineRun": "run-1"}
    )

    assert {stage["name"]: stage["status"] for stage in stages} == {
        "prepare": "Failed",
        "test": "NotRun",
    }


def test_terminal_snapshot_propagates_taskrun_authorization_errors(monkeypatch):
    monkeypatch.setattr(k8s, "get_pipelinerun", lambda _name: {"status": {}})

    def fail_lookup(_pr, *, strict_lookup_errors=False):
        assert strict_lookup_errors is True
        raise k8s.TaskRunLookupError("test-run", 403, "Forbidden")

    monkeypatch.setattr(k8s, "extract_pipeline_stages", fail_lookup)

    try:
        watcher._compute_terminal_stages(
            "job-1", {}, {"pipelineRun": "run-1"}
        )
    except k8s.TaskRunLookupError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("authorization failures must remain retryable")


def test_stage_snapshot_retries_are_timed_and_bounded():
    now = datetime.now(timezone.utc)

    assert watcher._stage_snapshot_retry_due(0, None, now) is True
    assert watcher._stage_snapshot_retry_due(1, now, now) is False
    assert watcher._stage_snapshot_retry_due(
        1, now - timedelta(seconds=watcher.STAGE_SNAPSHOT_RETRY_SECONDS + 1), now
    ) is True
    assert watcher._stage_snapshot_retry_due(
        watcher.STAGE_SNAPSHOT_MAX_ATTEMPTS, None, now
    ) is False

    assert watcher._has_usable_stage_snapshot([]) is False
    assert watcher._has_usable_stage_snapshot(
        [{"name": "test", "status": "Pending"}]
    ) is False
    assert watcher._has_usable_stage_snapshot(
        [{"name": "test", "status": "Succeeded"}]
    ) is True


def test_failure_enrichment_retries_are_timed_and_bounded():
    now = datetime.now(timezone.utc)

    assert watcher._failure_enrichment_retry_due(0, None, now) is True
    assert watcher._failure_enrichment_retry_due(1, now, now) is False
    assert watcher._failure_enrichment_retry_due(
        1,
        now - timedelta(seconds=watcher.FAILURE_ENRICHMENT_RETRY_SECONDS + 1),
        now,
    ) is True
    assert watcher._failure_enrichment_retry_due(
        watcher.FAILURE_ENRICHMENT_MAX_ATTEMPTS, None, now
    ) is False


def test_failure_enrichment_does_not_backfill_migrated_history():
    assert watcher._should_enrich_failure(
        mlflow_url="https://mlflow.example/#/runs/abc",
        enrichment_state="pending",
        enrichment_due=True,
        transitioning_into_terminal=False,
        enrichment_attempts=0,
    ) is False
    assert watcher._should_enrich_failure(
        mlflow_url="https://mlflow.example/#/runs/abc",
        enrichment_state="pending",
        enrichment_due=True,
        transitioning_into_terminal=True,
        enrichment_attempts=0,
    ) is True
    assert watcher._should_enrich_failure(
        mlflow_url="https://mlflow.example/#/runs/abc",
        enrichment_state="unavailable",
        enrichment_due=True,
        transitioning_into_terminal=False,
        enrichment_attempts=0,
    ) is True


def test_testing_list_sort_uses_latest_available_date():
    rows = [
        {
            "name": "never-run",
            "last_scheduled_time": None,
            "created_at": "2026-09-05T09:00:00Z",
        },
        {
            "name": "older-run",
            "last_scheduled_time": "2026-09-05T10:00:00Z",
            "created_at": "",
        },
        {
            "name": "newer-run",
            "last_scheduled_time": "2026-09-05T12:00:00Z",
            "created_at": "",
        },
    ]

    sorted_rows = fournos_api._sort_latest(
        rows, "last_scheduled_time", "created_at"
    )

    assert [row["name"] for row in sorted_rows] == [
        "newer-run",
        "older-run",
        "never-run",
    ]


def test_unknown_live_sort_key_falls_back_to_latest_age():
    rows = [
        {"name": "older", "created_at": "2026-09-05T09:00:00Z"},
        {"name": "newer", "created_at": "2026-09-05T12:00:00Z"},
    ]

    rows.sort(key=fournos_api._live_sort_key("unsupported"), reverse=True)

    assert [row["name"] for row in rows] == ["newer", "older"]


def test_history_date_sort_falls_back_to_created_at():
    assert "coalesce" in str(db_service._SORT_COLUMNS["date"]).lower()


def test_history_requester_filter_uses_immutable_subject(monkeypatch):
    monkeypatch.setattr(db_service.settings, "DATABASE_URL", "sqlite://")
    jobs_result = MagicMock()
    jobs_result.scalars.return_value.all.return_value = []
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[jobs_result, count_result])

    asyncio.run(
        db_service.list_jobs(
            session,
            requester_subject="google:12345",
        )
    )

    statement = session.execute.await_args_list[0].args[0]
    compiled = statement.compile()
    assert "requester_subject" in str(compiled)
    assert "google:12345" in compiled.params.values()


def test_recurring_child_inherits_archived_parent_requester():
    fields = {
        "requester_subject": "",
        "requester_email": "",
        "requester_name": "",
        "auth_provider": "",
    }
    parent = SimpleNamespace(
        requester_subject="google:12345",
        requester_email="person@example.com",
        requester_name="Example Person",
        auth_provider="google",
    )

    watcher._inherit_requester_fields(fields, parent)

    assert fields == {
        "requester_subject": "google:12345",
        "requester_email": "person@example.com",
        "requester_name": "Example Person",
        "auth_provider": "google",
    }


def test_archived_parent_requester_lookup_omits_unattributed_jobs():
    result = MagicMock()
    result.all.return_value = [
        ("owned-parent", "google:12345"),
        ("legacy-parent", ""),
    ]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    subjects = asyncio.run(
        db_service.get_requester_subjects_by_names(
            session, ["owned-parent", "legacy-parent"]
        )
    )

    assert subjects == {"owned-parent": "google:12345"}


def test_history_effective_date_index_is_created_for_existing_databases():
    indexes = {name: columns for name, table, columns in database_core._INDEXES}

    assert indexes["ix_fournos_jobs_effective_date"] == (
        "COALESCE(completed_at, created_at)"
    )


def test_structured_pr_selection_is_validated_against_server_snapshot(monkeypatch):
    sha = "a" * 40
    repository = "openshift-psap/forge"
    monkeypatch.setattr(
        fournos_api.settings, "FORGE_GITHUB_REPO", repository
    )
    monkeypatch.setattr(
        fournos_api,
        "_open_prs_cache",
        [{
            "number": 123,
            "title": "Exercise a selected revision",
            "author": "engineer",
            "head_sha": sha,
            "branch": "feature/request-provenance",
            "repository": repository,
            "url": "https://github.com/openshift-psap/forge/pull/123",
            "draft": False,
        }],
    )
    selection = PullRequestSelection(
        repository=repository,
        number=123,
        url="https://github.com/openshift-psap/forge/pull/123",
        head_branch="feature/request-provenance",
        requested_sha=sha,
    )

    env, fields = asyncio.run(
        fournos_api._resolve_source_request(selection, "")
    )

    assert env == {
        "REPO_OWNER": "openshift-psap",
        "REPO_NAME": "forge",
        "PULL_NUMBER": "123",
        "PULL_TITLE": "Exercise a selected revision",
        "PULL_HEAD_REF": "feature/request-provenance",
        "PULL_PULL_SHA": sha,
        "CONTROL_CENTER_REQUESTED_SHA": sha,
        "CONTROL_CENTER_PR_URL": (
            "https://github.com/openshift-psap/forge/pull/123"
        ),
    }
    assert fields["source_repository"] == repository
    assert fields["source_pr_number"] == 123
    assert fields["source_requested_sha"] == sha
    assert fields["source_resolved_sha"] == sha


def test_watcher_archives_pr_request_provenance_from_job_spec():
    sha = "b" * 40
    fields = extract_source_request_fields({
        "env": {
            "REPO_OWNER": "openshift-psap",
            "REPO_NAME": "forge",
            "PULL_NUMBER": "456",
            "PULL_HEAD_REF": "feature/test",
            "PULL_PULL_SHA": sha,
            "CONTROL_CENTER_REQUESTED_SHA": sha,
            "CONTROL_CENTER_PR_URL": (
                "https://github.com/openshift-psap/forge/pull/456"
            ),
        }
    })

    assert fields == {
        "source_repository": "openshift-psap/forge",
        "source_pr_number": 456,
        "source_pr_url": (
            "https://github.com/openshift-psap/forge/pull/456"
        ),
        "source_head_branch": "feature/test",
        "source_requested_sha": sha,
        "source_resolved_sha": sha,
    }


def test_legacy_sha_only_submission_remains_supported():
    sha = "d" * 40

    env, fields = asyncio.run(
        fournos_api._resolve_source_request(None, sha)
    )

    assert env == {"PULL_PULL_SHA": sha}
    assert fields["source_repository"] == ""
    assert fields["source_pr_number"] is None
    assert fields["source_requested_sha"] == sha
    assert fields["source_resolved_sha"] == sha


def test_submission_persists_pr_provenance_before_kubernetes_create(monkeypatch):
    sha = "c" * 40
    repository = "openshift-psap/forge"
    events = []
    persisted = {}
    created_body = {}

    monkeypatch.setattr(
        fournos_api.settings, "FORGE_GITHUB_REPO", repository
    )
    monkeypatch.setattr(
        fournos_api,
        "_open_prs_cache",
        [{
            "number": 789,
            "title": "Record the selected request",
            "author": "engineer",
            "head_sha": sha,
            "branch": "feature/record-request",
            "repository": repository,
            "url": "https://github.com/openshift-psap/forge/pull/789",
            "draft": False,
        }],
    )
    monkeypatch.setattr(
        fournos_api, "AsyncSessionLocal", lambda: _FakeSession()
    )

    async def upsert_job(_session, **kwargs):
        events.append("persist")
        persisted.update(kwargs)

    def create_fournos_job(body):
        events.append("create")
        created_body.update(body)
        return body

    monkeypatch.setattr(fournos_api.db_svc, "upsert_job", upsert_job)
    monkeypatch.setattr(
        fournos_api.k8s, "create_fournos_job", create_fournos_job
    )

    request = SubmitJobRequest(
        project="example",
        cluster="cluster-a",
        pull_request=PullRequestSelection(
            repository=repository,
            number=789,
            url="https://github.com/openshift-psap/forge/pull/789",
            head_branch="feature/record-request",
            requested_sha=sha,
        ),
    )
    user = {
        "subject": "google:12345",
        "username": "person@example.com",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
        "role": "user",
    }

    asyncio.run(fournos_api.submit_job(request, user=user))

    assert events == ["persist", "create"]
    assert persisted["source_repository"] == repository
    assert persisted["source_pr_number"] == 789
    assert persisted["source_requested_sha"] == sha
    assert persisted["source_resolved_sha"] == sha
    assert created_body["spec"]["env"]["PULL_PULL_SHA"] == sha
    assert persisted["fjob_spec"]["env"] == created_body["spec"]["env"]


def test_matrix_recurring_submission_preserves_pr_provenance(monkeypatch):
    sha = "e" * 40
    repository = "openshift-psap/forge"
    persisted = []
    created = []

    monkeypatch.setattr(
        fournos_api.settings, "FORGE_GITHUB_REPO", repository
    )
    monkeypatch.setattr(
        fournos_api,
        "_open_prs_cache",
        [{
            "number": 321,
            "title": "Recurring matrix source",
            "author": "engineer",
            "head_sha": sha,
            "branch": "feature/matrix-source",
            "repository": repository,
            "url": "https://github.com/openshift-psap/forge/pull/321",
            "draft": False,
        }],
    )
    monkeypatch.setattr(
        fournos_api, "AsyncSessionLocal", lambda: _FakeSession()
    )

    async def upsert_job(_session, **kwargs):
        persisted.append(kwargs)

    def create_fournos_job(body):
        created.append(body)
        return body

    monkeypatch.setattr(fournos_api.db_svc, "upsert_job", upsert_job)
    monkeypatch.setattr(
        fournos_api.k8s, "create_fournos_job", create_fournos_job
    )

    request = SubmitMatrixRequest(
        project="example",
        cluster="cluster-a",
        models=[SubmitMatrixModelInput(key="model-a")],
        workloads=["workload-a"],
        schedule="0 2 * * *",
        pull_request=PullRequestSelection(
            repository=repository,
            number=321,
            url="https://github.com/openshift-psap/forge/pull/321",
            head_branch="feature/matrix-source",
            requested_sha=sha,
        ),
    )
    user = {
        "subject": "google:12345",
        "username": "person@example.com",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
        "role": "user",
    }

    response = asyncio.run(fournos_api.submit_matrix(request, user=user))

    assert response["status"] == "ok"
    assert len(created) == len(persisted) == 1
    assert created[0]["spec"]["schedule"] == "0 2 * * *"
    assert created[0]["spec"]["env"]["PULL_PULL_SHA"] == sha
    assert persisted[0]["trigger_type"] == "recurring-parent"
    assert persisted[0]["source_pr_number"] == 321
    assert persisted[0]["fjob_spec"]["env"] == created[0]["spec"]["env"]


def test_job_detail_exposes_recorded_pr_association_and_selected_commit():
    sha = "f" * 40
    info = fournos_api._extract_forge_info({
        "spec": {
            "executionEngine": {
                "forge": {"project": "example", "args": ["preset"]}
            },
            "env": {
                "REPO_OWNER": "openshift-psap",
                "REPO_NAME": "forge",
                "PULL_NUMBER": "654",
                "PULL_TITLE": "Expose provenance",
                "PULL_HEAD_REF": "feature/expose-provenance",
                "PULL_PULL_SHA": sha,
                "CONTROL_CENTER_REQUESTED_SHA": sha,
                "CONTROL_CENTER_PR_URL": (
                    "https://github.com/openshift-psap/forge/pull/654"
                ),
            },
        }
    })

    assert info["repository"] == "openshift-psap/forge"
    assert info["pr_number"] == "654"
    assert info["head_branch"] == "feature/expose-provenance"
    assert info["requested_sha"] == sha
    assert info["resolved_sha"] == sha


def test_pr_provenance_schema_migrations_and_indexes_are_registered():
    migrations = {
        (table, column): col_type
        for table, column, col_type in database_core._MIGRATIONS
    }
    indexes = {name: columns for name, _table, columns in database_core._INDEXES}

    for column in (
        "source_repository",
        "source_pr_number",
        "source_pr_url",
        "source_head_branch",
        "source_requested_sha",
        "source_resolved_sha",
    ):
        assert ("fournos_jobs", column) in migrations
    assert indexes["ix_fournos_jobs_source_pr"] == (
        "source_repository, source_pr_number"
    )
def test_rhaiis_build_source_requires_a_pin_or_explicit_latest_main():
    from app.schemas.fournos import SubmitJobRequest

    with pytest.raises(HTTPException, match="build source is required"):
        fournos_api._resolve_build_source(
            SubmitJobRequest(project="rhaiis", cluster="hera")
        )

    assert fournos_api._resolve_build_source(
        SubmitJobRequest(project="rhaiis", cluster="hera", pull_sha="  abc123  ")
    ) == "abc123"
    assert fournos_api._resolve_build_source(
        SubmitJobRequest(project="rhaiis", cluster="hera", use_latest_main=True)
    ) == "main"

    with pytest.raises(HTTPException, match="either a pinned build source"):
        fournos_api._resolve_build_source(
            SubmitJobRequest(
                project="rhaiis",
                cluster="hera",
                pull_sha="abc123",
                use_latest_main=True,
            )
        )


@pytest.mark.parametrize("request_type", ["single", "matrix"])
def test_submission_rejects_noncanonical_gpu_types(request_type):
    kwargs = {"project": "rhaiis", "cluster": "hera", "gpu_type": "NVIDIA-H200"}
    if request_type == "matrix":
        kwargs.update(models=[{"key": "model"}], workloads=["profile1"])
        model = SubmitMatrixRequest
    else:
        model = SubmitJobRequest

    with pytest.raises(ValidationError, match="lowercase alphanumeric"):
        model(**kwargs)

    assert model(**{**kwargs, "gpu_type": "h200"}).gpu_type == "h200"


def test_rhaiis_overrides_normalize_to_forge_keys():
    assert fournos_api._normalize_rhaiis_overrides(
        "rhaiis",
        {
            "tests.rhaiis.slack_member_id": "U0123456789",
            "rhaiis.compare_versions.enabled": "true",
            "tests.rhaiis.workload_key": '["profile1", "custom"]',
        },
    ) == {
        "tests.rhaiis.slack_user": "U0123456789",
        "tests.rhaiis.workload_keys": '["profile1", "custom"]',
    }


def test_rhaiis_schema_adds_legacy_controls(monkeypatch):
    from app.services import rhaiis_ui_schema

    def fake_fetch_yaml(path):
        if path.endswith("config.d/rhaiis.yaml"):
            return {"engines": {"vllm": {"images": {"nvidia": "vllm:latest"}}}}
        raise AssertionError(path)

    monkeypatch.setattr(project_ui_schema, "fetch_yaml", fake_fetch_yaml)
    monkeypatch.setattr(rhaiis_ui_schema, "fetch_yaml", fake_fetch_yaml)
    schema = project_ui_schema.ProjectUiSchema.model_validate(
        {
            "project": "rhaiis",
            "modes": [
                {
                    "id": "single",
                    "sections": [
                        {
                            "id": "infra",
                            "fields": [
                                {"key": "engine", "type": "select", "maps_to": "rhaiis.engine"},
                            ],
                        },
                        {
                            "id": "model",
                            "fields": [
                                {"key": "model", "type": "select"},
                                {"key": "workload", "type": "multiselect"},
                                {"key": "benchmark", "type": "boolean"},
                                {"key": "warmup", "type": "boolean"},
                                {"key": "slack", "type": "boolean"},
                                {"key": "slack_member_id", "type": "text"},
                                {
                                    "key": "compare_version",
                                    "type": "text",
                                    "visible_if": {"field": "compare_versions", "equals": True},
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    )

    resolved = project_ui_schema._resolve_schema("rhaiis", schema, strict=True)
    mode = resolved.modes[0]
    fields = {field.key: field for section in mode.sections for field in section.fields}
    assert "cluster_profile" not in fields
    assert fields["gpu_count"].default == 1
    assert fields["model"].options[-1].value == "__custom_model__"
    assert fields["workload"].options[-1].value == "__custom_workload__"
    assert fields["warmup"].default is True
    assert fields["benchmark"].default is True
    assert fields["slack"].default is True
    assert fields["slack_member_id"].required is True
    assert fields["compare_version"].required is True
    assert fields["prefix_caching"].default is True
    assert fields["engine"].options == []


def test_rhaiis_cpt_cache_uses_pipeline_preset_and_slack_target_is_optional(monkeypatch):
    from app.schemas.ui_schema import ProjectUiSchema
    from app.services import rhaiis_ui_schema

    monkeypatch.setattr(rhaiis_ui_schema, "fetch_yaml", lambda _path: {"engines": {}})
    schema = ProjectUiSchema.model_validate(
        {
            "project": "rhaiis",
            "modes": [
                {
                    "id": "cpt",
                    "kind": "matrix",
                    "sections": [
                        {"id": "infra", "label": "Infrastructure", "fields": []},
                        {
                            "id": "version",
                            "label": "Version & Notifications",
                            "fields": [
                                {"key": "slack_member_id", "type": "text"},
                            ],
                        },
                    ],
                }
            ],
        }
    )

    rhaiis_ui_schema.augment_schema(schema)

    fields = {
        field.key: field
        for section in schema.modes[0].sections
        for field in section.fields
    }
    assert "prefix_caching" not in fields
    assert fields["slack_member_id"].required is False


def test_rhaiis_workload_presets_are_quick_presets(monkeypatch):
    def fake_fetch_yaml(path):
        if path.endswith("presets.d/workloads.yaml"):
            return {
                "__multiple": True,
                "sglang": {"rhaiis.engine": "sglang"},
                "profile1-balanced": {"tests.rhaiis.workload_key": "profile1"},
                "profile4-long-context": {"tests.rhaiis.workload_key": "profile4"},
                "benchmark-standard": {
                    "extends": ["benchmark"],
                    "tests.rhaiis.workload_key": ["profile1", "profile4"],
                },
                "sglang-ci-quick": {
                    "extends": ["sglang"],
                    "tests.rhaiis.workload_key": "profile1",
                },
            }
        if path.endswith("config.d/rhaiis.yaml"):
            return {"engines": {}}
        raise AssertionError(path)

    monkeypatch.setattr(project_ui_schema, "fetch_yaml", fake_fetch_yaml)
    schema = project_ui_schema.ProjectUiSchema.model_validate(
        {
            "project": "rhaiis",
            "modes": [
                {
                    "id": "single",
                    "presets_ref": {"path": "presets.d/workloads.yaml"},
                    "sections": [
                        {
                            "id": "model",
                            "fields": [
                                {
                                    "key": "engine",
                                    "type": "radio",
                                    "maps_to": "rhaiis.engine",
                                },
                                {
                                    "key": "workload",
                                    "type": "multiselect",
                                    "maps_to": "tests.rhaiis.workload_key",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    mode = project_ui_schema._resolve_schema("rhaiis", schema, strict=True).modes[0]
    workload = next(field for section in mode.sections for field in section.fields if field.key == "workload")
    quick = {preset.key: preset for preset in mode.quick_presets}

    assert {option.value for option in workload.options if option.value != "__custom_workload__"} == {
        "profile1-balanced",
        "profile4-long-context",
    }
    assert quick["benchmark-standard"].fills["workload"] == [
        "profile1-balanced",
        "profile4-long-context",
    ]
    assert quick["benchmark-standard"].overrides == {}
    assert quick["sglang-ci-quick"].fills["engine"] == "sglang"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("ERROR: failed to resolve the image", True),
        ("warning: retrying request", True),
        ("ERROR: ----------------", False),
        ("normal output", False),
        ("", False),
    ],
)
def test_log_issue_detection_ignores_decoration_only_markers(line, expected):
    assert fournos_api._is_log_issue(line) is expected


def test_release_refresh_replaces_an_existing_cache(monkeypatch):
    monkeypatch.setattr(fournos_api, "_releases_cache", [{"tag_name": "old"}])
    monkeypatch.setattr(fournos_api, "_releases_inflight", None)
    monkeypatch.setattr(
        fournos_api,
        "_fetch_github_releases_sync",
        lambda: [{"tag_name": "new"}],
    )

    refreshed = asyncio.run(fournos_api.refresh_releases())

    assert refreshed == [{"tag_name": "new"}]
    assert fournos_api._releases_cache == refreshed
