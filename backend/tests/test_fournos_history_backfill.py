import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.fournos_job import FournosCptRunJobArchive, FournosJob
from app.services.fournos_history_backfill import (
    backfill_archived_cpt_jobs,
    backfill_archived_jobs,
)


def test_backfill_imports_rows_idempotently(tmp_path):
    source_url = f"sqlite+aiosqlite:///{tmp_path / 'fournos.sqlite'}"
    target_url = f"sqlite+aiosqlite:///{tmp_path / 'control-center.sqlite'}"

    async def run():
        source_engine = create_async_engine(source_url)
        target_engine = create_async_engine(target_url)
        sessions = async_sessionmaker(target_engine, expire_on_commit=False)
        created_at = datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)

        async with source_engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE jobs ("
                "name TEXT, project TEXT, preset TEXT, cluster TEXT, "
                "pipeline TEXT, owner TEXT, status TEXT, message TEXT, "
                "created_at DATETIME, completed_at DATETIME, "
                "duration_seconds REAL, mlflow_url TEXT, ci_artifacts_url TEXT, "
                "config_overrides TEXT, tags TEXT, fjob_spec TEXT, "
                "fjob_status TEXT, error_message TEXT, "
                "triggered_by_schedule TEXT, trigger_type TEXT)"
            ))
            await connection.execute(text(
                "INSERT INTO jobs VALUES ("
                ":name, :project, :preset, :cluster, :pipeline, :owner, "
                ":status, :message, :created_at, :completed_at, :duration, "
                ":mlflow, :artifacts, :overrides, :tags, :spec, :fjob_status, "
                ":error, :schedule, :trigger_type)"
            ), {
                "name": "rhaiis-hera-example",
                "project": "rhaiis",
                "preset": "vllm hera model-a",
                "cluster": "hera",
                "pipeline": "forge-full",
                "owner": "tester",
                "status": "Failed",
                "message": "test failure",
                "created_at": created_at.isoformat(),
                "completed_at": created_at.isoformat(),
                "duration": 30.0,
                "mlflow": "",
                "artifacts": "",
                "overrides": json.dumps({"tests.rhaiis.version": "v1"}),
                "tags": json.dumps(["cpt"]),
                "spec": json.dumps({"cluster": "hera"}),
                "fjob_status": json.dumps({"phase": "Failed"}),
                "error": "test failure",
                "schedule": None,
                "trigger_type": "manual",
            })
            await connection.execute(text(
                "CREATE TABLE cpt_runs ("
                "id TEXT PRIMARY KEY, project TEXT, pipeline_key TEXT, "
                "forge_pipeline TEXT, version_label TEXT, forge_source TEXT, "
                "owner TEXT, cluster TEXT, accelerator TEXT, engine TEXT, "
                "engine_version TEXT, run_metadata TEXT, created_at DATETIME)"
            ))
            await connection.execute(text(
                "INSERT INTO cpt_runs VALUES ("
                ":id, :project, :pipeline_key, :forge_pipeline, :version, "
                ":source, :owner, :cluster, :accelerator, :engine, :engine_version, "
                ":metadata, :created_at)"
            ), {
                "id": "run-1",
                "project": "rhaiis",
                "pipeline_key": "cpt-rhaiis-hera",
                "forge_pipeline": "forge-full",
                "version": "RHAIIS-test",
                "source": "abc123",
                "owner": "tester",
                "cluster": "hera",
                "accelerator": "nvidia",
                "engine": "vllm",
                "engine_version": "vllm:v1",
                "metadata": json.dumps({"configured_model_count": 2}),
                "created_at": created_at.isoformat(),
            })
            await connection.execute(text(
                "CREATE TABLE cpt_run_jobs ("
                "id TEXT PRIMARY KEY, run_id TEXT, position INTEGER, "
                "job_name TEXT, model_name TEXT, model_preset TEXT, workloads TEXT, "
                "submission_status TEXT, status TEXT, message TEXT, created_at DATETIME)"
            ))
            await connection.execute(text(
                "INSERT INTO cpt_run_jobs VALUES ("
                ":id, :run_id, :position, :job_name, :model_name, :model_preset, "
                ":workloads, :submission_status, :status, :message, :created_at)"
            ), [
                {
                    "id": "child-created",
                    "run_id": "run-1",
                    "position": 0,
                    "job_name": "rhaiis-hera-example",
                    "model_name": "model-a",
                    "model_preset": "model-a",
                    "workloads": json.dumps(["profile1"]),
                    "submission_status": "Created",
                    "status": "Failed",
                    "message": "benchmark failed",
                    "created_at": created_at.isoformat(),
                },
                {
                    "id": "child-not-created",
                    "run_id": "run-1",
                    "position": 1,
                    "job_name": None,
                    "model_name": "model-b",
                    "model_preset": "model-b",
                    "workloads": json.dumps(["profile6"]),
                    "submission_status": "Failed",
                    "status": "Failed",
                    "message": "Kubernetes rejected the request",
                    "created_at": created_at.isoformat(),
                },
            ])

        async with target_engine.begin() as connection:
            for model in (FournosJob, FournosCptRunJobArchive):
                await connection.run_sync(model.__table__.create)

        first = await backfill_archived_jobs(
            source_url, target_sessions=sessions, batch_size=1
        )
        second = await backfill_archived_jobs(
            source_url, target_sessions=sessions, batch_size=1
        )
        first_cpt = await backfill_archived_cpt_jobs(
            source_url, target_sessions=sessions
        )
        second_cpt = await backfill_archived_cpt_jobs(
            source_url, target_sessions=sessions
        )
        async with sessions() as session:
            job = (
                await session.execute(
                    select(
                        FournosJob.project,
                        FournosJob.status,
                        FournosJob.config_overrides,
                        FournosJob.tags,
                        FournosJob.fjob_status,
                        FournosJob.cpt_run_id,
                        FournosJob.cpt_run_metadata,
                    ).where(FournosJob.name == "rhaiis-hera-example")
                )
            ).one_or_none()
            archived = (
                await session.execute(
                    select(FournosCptRunJobArchive).where(
                        FournosCptRunJobArchive.id == "child-not-created"
                    )
                )
            ).scalar_one_or_none()

        await source_engine.dispose()
        await target_engine.dispose()
        return first, second, first_cpt, second_cpt, job, archived

    first, second, first_cpt, second_cpt, job, archived = asyncio.run(run())

    assert first == (1, 1)
    assert second == (1, 0)
    assert first_cpt == (2, 1, 1)
    assert second_cpt == (2, 1, 0)
    assert job is not None
    assert job.project == "rhaiis"
    assert job.status == "Failed"
    assert job.config_overrides == {"tests.rhaiis.version": "v1"}
    assert job.tags == ["cpt"]
    assert job.fjob_status == {"phase": "Failed"}
    assert job.cpt_run_id == "run-1"
    assert job.cpt_run_metadata["pipeline_key"] == "cpt-rhaiis-hera"
    assert job.cpt_run_metadata["version_label"] == "RHAIIS-test"
    assert archived is not None
    assert archived.cpt_run_metadata["model_key"] == "model-b"
    assert archived.submission_status == "Failed"
