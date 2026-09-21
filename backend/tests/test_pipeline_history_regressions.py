from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.services import fournos_k8s_client as k8s
from app.services import fournos_watcher as watcher
from app.api import fournos as fournos_api
from app.core import database as database_core
from app.services import fournos_db_service as db_service
from app.services import project_ui_schema


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
        lambda _pr: [
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


def test_history_effective_date_index_is_created_for_existing_databases():
    indexes = {name: columns for name, table, columns in database_core._INDEXES}

    assert indexes["ix_fournos_jobs_effective_date"] == (
        "COALESCE(completed_at, created_at)"
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
    def fake_fetch_yaml(path):
        if path.endswith("config.d/rhaiis.yaml"):
            return {"engines": {"vllm": {"images": {"nvidia": "vllm:latest"}}}}
        raise AssertionError(path)

    monkeypatch.setattr(project_ui_schema, "fetch_yaml", fake_fetch_yaml)
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
    assert fields["prefix_caching"].default is False
    assert fields["engine"].options == []


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
