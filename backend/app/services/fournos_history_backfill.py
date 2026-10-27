"""Idempotently import archived jobs from the Fournos dashboard database.

The Control Center watcher captures live FournosJob resources. This utility
backfills records that the dashboard archived before Control Center began
watching them (including jobs whose Kubernetes resources have since expired).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import noload
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.fournos_job import FournosCptRunJobArchive, FournosJob


SOURCE_COLUMNS = (
    "name",
    "project",
    "preset",
    "cluster",
    "pipeline",
    "owner",
    "status",
    "message",
    "created_at",
    "completed_at",
    "duration_seconds",
    "mlflow_url",
    "ci_artifacts_url",
    "config_overrides",
    "tags",
    "fjob_spec",
    "fjob_status",
    "error_message",
    "triggered_by_schedule",
    "trigger_type",
)

_BATCH_SIZE = 500
CPT_RUN_COLUMNS = (
    "id", "project", "pipeline_key", "forge_pipeline", "version_label",
    "forge_source", "owner", "cluster", "accelerator", "engine",
    "engine_version", "run_metadata", "created_at",
)
CPT_RUN_JOB_COLUMNS = (
    "id", "run_id", "position", "job_name", "model_name", "model_preset",
    "workloads", "submission_status", "status", "message", "created_at",
)


def _source_database_url() -> str:
    url = (settings.FOURNOS_ARCHIVE_DATABASE_URL or "").strip()
    if not url:
        raise RuntimeError(
            "Set FOURNOS_ARCHIVE_DATABASE_URL to a read-only connection "
            "for the Fournos dashboard database."
        )
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default
    return value


def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _to_control_center_record(row: Any) -> dict[str, Any]:
    spec = _json_value(row.get("fjob_spec"), {})
    return {
        "id": str(uuid4()),
        "name": row["name"],
        "project": row.get("project") or "",
        "preset": row.get("preset") or "",
        "cluster": row.get("cluster") or "",
        "pipeline": row.get("pipeline") or "",
        "owner": row.get("owner") or "",
        "status": row.get("status") or "Unknown",
        "message": row.get("message") or "",
        "created_at": (
            _datetime_value(row.get("created_at"))
            or _datetime_value(row.get("completed_at"))
            or datetime.now(timezone.utc)
        ),
        "completed_at": _datetime_value(row.get("completed_at")),
        "duration_seconds": row.get("duration_seconds"),
        "mlflow_url": row.get("mlflow_url") or "",
        "ci_artifacts_url": row.get("ci_artifacts_url") or "",
        "config_overrides": _json_value(row.get("config_overrides"), {}),
        "tags": list(_json_value(row.get("tags"), []) or []),
        "fjob_spec": spec,
        "fjob_status": _json_value(row.get("fjob_status"), {}),
        "error_message": row.get("error_message") or "",
        "triggered_by_schedule": row.get("triggered_by_schedule"),
        "trigger_type": row.get("trigger_type") or "manual",
        "is_lock": bool(spec.get("lockOnly")) if isinstance(spec, dict) else False,
    }


def _insert_statement(dialect: str, records: list[dict[str, Any]]):
    if dialect == "postgresql":
        return pg_insert(FournosJob).values(records).on_conflict_do_nothing(
            index_elements=[FournosJob.name]
        )
    if dialect == "sqlite":
        return sqlite_insert(FournosJob).values(records).on_conflict_do_nothing(
            index_elements=[FournosJob.name]
        )
    raise RuntimeError(
        f"History backfill does not support target database dialect: {dialect}"
    )


def _cpt_run_metadata(run: Any, child: Any) -> dict[str, Any]:
    run_metadata = _json_value(run.get("run_metadata"), {})
    if not isinstance(run_metadata, dict):
        run_metadata = {}
    model_name = str(child.get("model_name") or "").strip()
    model_preset = str(child.get("model_preset") or "").strip()
    workloads = _json_value(child.get("workloads"), [])
    if not isinstance(workloads, list):
        workloads = [workloads] if workloads else []
    created_at = _datetime_value(run.get("created_at"))
    metadata = {
        "project": run.get("project") or "",
        "pipeline_key": run.get("pipeline_key") or "",
        "version_label": run.get("version_label") or "Unspecified",
        "cluster": run.get("cluster") or "",
        "accelerator": run.get("accelerator") or "",
        "engine": run.get("engine") or "",
        "engine_version": run.get("engine_version") or "",
        "fournos_pipeline": run.get("forge_pipeline") or "",
        "forge_source": run.get("forge_source") or "Unknown",
        "owner": run.get("owner") or "Unknown",
        "configured_model_count": run_metadata.get("configured_model_count"),
        "started_at": created_at.isoformat() if created_at else "",
        "model_key": model_preset or model_name,
        "model_name": model_name or model_preset,
        "model_preset": model_preset,
        "workloads": [str(item).strip() for item in workloads if str(item or "").strip()],
    }
    return metadata


async def backfill_archived_cpt_jobs(
    source_database_url: str,
    *,
    target_sessions: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> tuple[int, int, int]:
    """Attach archived CPT metadata to local jobs and retain submissions without jobs.

    Returns (source CPT child rows read, local FournosJobs linked, missing-job
    submission records saved). Existing Control Center CPT metadata is not
    replaced if that job already belongs to another tracked submission.
    """
    source_engine = create_async_engine(source_database_url, pool_pre_ping=True)
    scanned = linked = archived = 0
    try:
        async with source_engine.connect() as source:
            if source.dialect.name == "postgresql":
                await source.execute(text("SET TRANSACTION READ ONLY"))
            if source.dialect.name == "sqlite":
                table_query = text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            else:
                table_query = text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                )
            table_names = set((await source.execute(table_query)).scalars().all())
            if not {"cpt_runs", "cpt_run_jobs"}.issubset(table_names):
                return 0, 0, 0

            runs_result = await source.execute(text(
                f"SELECT {', '.join(CPT_RUN_COLUMNS)} FROM cpt_runs"
            ))
            runs_by_id = {
                row["id"]: row for row in runs_result.mappings().all()
            }
            children_result = await source.execute(text(
                f"SELECT {', '.join(CPT_RUN_JOB_COLUMNS)} FROM cpt_run_jobs "
                "ORDER BY created_at ASC NULLS FIRST, position ASC, id ASC"
            ))
            children = children_result.mappings().all()

        for offset in range(0, len(children), _BATCH_SIZE):
            batch = children[offset : offset + _BATCH_SIZE]
            scanned += len(batch)
            async with target_sessions() as target:
                async with target.begin():
                    names = [row["job_name"] for row in batch if row.get("job_name")]
                    target_result = await target.execute(
                        select(FournosJob)
                        .options(noload("*"))
                        .where(FournosJob.name.in_(names))
                    ) if names else None
                    jobs_by_name = {
                        job.name: job for job in (
                            target_result.scalars().all() if target_result else []
                        )
                    }
                    for child in batch:
                        run = runs_by_id.get(child["run_id"])
                        if run is None:
                            continue
                        run_id = str(run["id"])
                        metadata = _cpt_run_metadata(run, child)
                        job_name = child.get("job_name")
                        target_job = jobs_by_name.get(job_name) if job_name else None
                        if target_job is not None:
                            if target_job.cpt_run_id not in (None, run_id):
                                continue
                            existing_metadata = dict(target_job.cpt_run_metadata or {})
                            target_job.cpt_run_id = run_id
                            target_job.cpt_run_metadata = {
                                **metadata,
                                **existing_metadata,
                            }
                            linked += 1
                            continue

                        record = await target.get(
                            FournosCptRunJobArchive, str(child["id"])
                        )
                        if record is None:
                            record = FournosCptRunJobArchive(
                                id=str(child["id"]),
                                run_id=run_id,
                                position=child.get("position") or 0,
                                job_name=job_name,
                                model_name=child.get("model_name") or "",
                                model_preset=child.get("model_preset") or "",
                                workloads=_json_value(child.get("workloads"), []) or [],
                                submission_status=child.get("submission_status") or "Submitting",
                                status=child.get("status") or "Pending",
                                message=child.get("message") or "",
                                created_at=(
                                    _datetime_value(child.get("created_at"))
                                    or _datetime_value(run.get("created_at"))
                                    or datetime.now(timezone.utc)
                                ),
                                cpt_run_metadata=metadata,
                            )
                            target.add(record)
                            archived += 1
                        else:
                            # Keep any user-corrected version metadata while
                            # refreshing the latest dashboard submission status.
                            record.submission_status = child.get("submission_status") or "Submitting"
                            record.status = child.get("status") or "Pending"
                            record.message = child.get("message") or ""

    finally:
        await source_engine.dispose()

    return scanned, linked, archived


async def backfill_archived_jobs(
    source_database_url: str,
    *,
    target_sessions: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    batch_size: int = _BATCH_SIZE,
) -> tuple[int, int]:
    """Copy dashboard job rows missing from Control Center; return (read, added).

    Existing Control Center records are never overwritten, preserving richer
    stage snapshots, provenance, and identity data collected by its watcher.
    """
    source_engine = create_async_engine(source_database_url, pool_pre_ping=True)
    scanned = 0
    inserted = 0
    selected_columns = ", ".join(SOURCE_COLUMNS)
    try:
        async with source_engine.connect() as source:
            if source.dialect.name == "postgresql":
                await source.execute(text("SET TRANSACTION READ ONLY"))
            offset = 0
            while True:
                result = await source.execute(
                    text(
                        f"SELECT {selected_columns} FROM jobs "
                        "ORDER BY created_at ASC NULLS FIRST, name ASC "
                        "LIMIT :batch_size OFFSET :offset"
                    ),
                    {"batch_size": batch_size, "offset": offset},
                )
                rows = result.mappings().all()
                if not rows:
                    break

                records = [_to_control_center_record(row) for row in rows]
                async with target_sessions() as target:
                    async with target.begin():
                        insert_result = await target.execute(
                            _insert_statement(
                                target.bind.dialect.name, records
                            )
                        )
                        rowcount = insert_result.rowcount
                        if rowcount is not None and rowcount >= 0:
                            inserted += rowcount
                        else:
                            inserted += len(records)

                scanned += len(rows)
                offset += len(rows)
    finally:
        await source_engine.dispose()

    return scanned, inserted


async def _run() -> None:
    scanned, inserted = await backfill_archived_jobs(_source_database_url())
    cpt_scanned, cpt_linked, cpt_archived = await backfill_archived_cpt_jobs(
        _source_database_url()
    )
    print(
        f"Fournos history backfill complete: scanned {scanned} dashboard "
        f"jobs; inserted {inserted}; already present {scanned - inserted}."
    )
    print(
        f"CPT history backfill complete: scanned {cpt_scanned} CPT child rows; "
        f"linked {cpt_linked} jobs; retained {cpt_archived} entries without "
        "a matching local FournosJob."
    )


if __name__ == "__main__":
    asyncio.run(_run())
