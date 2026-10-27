"""Build the rolling, version-level view for matrix/CPT Fournos submissions."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


ACTIVE = {"Submitting", "Pending", "Resolving", "Admitted", "Running"}
FAILED = {"Failed", "Not submitted"}
TERMINAL = {"Succeeded", "Failed", "Stopped"}
PROFILE_ORDER = ("profile1", "profile4", "profile5", "profile7", "profile6")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fields(metadata: dict[str, Any]) -> tuple[str, ...]:
    return (
        _text(metadata.get("project")),
        _text(metadata.get("pipeline_key")),
        _text(metadata.get("version_label")) or "Unspecified",
        _text(metadata.get("cluster")),
        _text(metadata.get("accelerator")),
        _text(metadata.get("engine")),
        _text(metadata.get("engine_version")),
        _text(metadata.get("fournos_pipeline")),
    )


def _group_id(fields: tuple[str, ...]) -> str:
    encoded = json.dumps(fields, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _status(job: Any) -> str:
    if getattr(job, "submission_status", "") == "Failed":
        return "Not submitted"
    if not _job_name(job) and getattr(job, "submission_status", "") != "Created":
        return "Submitting"
    status = _text(getattr(job, "status", "")) or "Unknown"
    requested_at = getattr(job, "cpt_stop_requested_at", None)
    requested_at = requested_at or getattr(job, "stop_requested_at", None)
    if requested_at and status not in TERMINAL:
        return "Stopped"
    return status


def _job_name(job: Any) -> str:
    return _text(getattr(job, "name", "") or getattr(job, "job_name", ""))


def _aggregate(statuses: list[str]) -> str:
    if not statuses:
        return "Submitting"
    if any(status in ACTIVE for status in statuses):
        return "Running" if any(status != "Submitting" for status in statuses) else "Submitting"
    failed = any(status in FAILED for status in statuses)
    succeeded = any(status == "Succeeded" for status in statuses)
    stopped = any(status == "Stopped" for status in statuses)
    if failed:
        return "Partially failed" if succeeded or stopped else "Failed"
    if stopped:
        return "Partially stopped" if succeeded else "Stopped"
    if all(status == "Succeeded" for status in statuses):
        return "Succeeded"
    return "Unknown"


def _job_metadata(job: Any) -> dict[str, Any]:
    value = getattr(job, "cpt_run_metadata", None)
    return value if isinstance(value, dict) else {}


def build_new_cpt_attempt(
    source_job: Any,
    spec: dict[str, Any],
    owner: str,
    *,
    now: datetime | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Carry a tracked CPT child into the rolling matrix as a new attempt.

    Ordinary jobs deliberately return no tracking metadata. For a tracked
    RHAIIS child, derive editable dimensions from the submitted spec while
    preserving the source CPT pipeline identity and model count.
    """
    run_id = _text(getattr(source_job, "cpt_run_id", ""))
    source_metadata = _job_metadata(source_job)
    if not run_id or not source_metadata.get("pipeline_key"):
        return None, {}

    metadata = dict(source_metadata)
    metadata["started_at"] = _utc(now or datetime.now(timezone.utc)).isoformat()
    metadata["owner"] = owner
    metadata["cluster"] = _text(spec.get("cluster")) or metadata.get("cluster", "")
    metadata["fournos_pipeline"] = _text(spec.get("pipeline")) or metadata.get(
        "fournos_pipeline", ""
    )

    execution_engine = spec.get("executionEngine") or {}
    engine_block = (
        execution_engine.get("forge") or {}
        if isinstance(execution_engine, dict) else {}
    )
    if not isinstance(engine_block, dict):
        engine_block = {}
    overrides = engine_block.get("configOverrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    args = engine_block.get("args") or []
    if not isinstance(args, list):
        args = []
    old_spec = getattr(source_job, "fjob_spec", None) or {}
    old_engine_block = (
        ((old_spec.get("executionEngine") or {}).get("forge") or {})
        if isinstance(old_spec, dict) else {}
    )
    old_args = old_engine_block.get("args") or []
    if not isinstance(old_args, list):
        old_args = []

    project = _text(engine_block.get("project")) or metadata.get("project", "")
    metadata["project"] = project
    if project == "rhaiis":
        version = _text(overrides.get("tests.rhaiis.version"))
        if version:
            metadata["version_label"] = version

        model_key = _text(metadata.get("model_key"))
        if model_key in old_args:
            model_index = old_args.index(model_key)
            if model_index < len(args) and _text(args[model_index]):
                metadata["model_key"] = _text(args[model_index])
                if metadata["model_key"] != model_key:
                    metadata["model_name"] = metadata["model_key"]

        engine = _text(metadata.get("engine"))
        if engine in old_args:
            engine_index = old_args.index(engine)
            if engine_index < len(args) and _text(args[engine_index]):
                metadata["engine"] = _text(args[engine_index])
                if engine_index > 0 and _text(args[engine_index - 1]):
                    metadata["accelerator"] = _text(args[engine_index - 1])

        workloads = overrides.get("tests.rhaiis.workload_keys")
        if isinstance(workloads, str):
            try:
                workloads = json.loads(workloads)
            except (TypeError, ValueError):
                workloads = [workloads]
        if isinstance(workloads, list):
            metadata["workloads"] = [
                _text(item) for item in workloads if _text(item)
            ]

        engine = _text(metadata.get("engine"))
        accelerator = _text(metadata.get("accelerator"))
        image_key = f"rhaiis.engines.{engine}.images.{accelerator}"
        image = _text(overrides.get(image_key))
        if image:
            metadata["engine_version"] = image

    env = spec.get("env") or {}
    if isinstance(env, dict) and _text(env.get("PULL_PULL_SHA")):
        metadata["forge_source"] = _text(env.get("PULL_PULL_SHA"))

    return str(uuid4()), metadata


def build_cpt_run_groups(jobs: list[Any]) -> list[dict[str, Any]]:
    """Group matrix submissions and retain the latest result per model/profile."""
    grouped: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    metadata_by_job: dict[int, dict[str, Any]] = {}
    for job in jobs:
        run_id = _text(getattr(job, "cpt_run_id", ""))
        metadata = _job_metadata(job)
        if not run_id or not metadata.get("pipeline_key"):
            continue
        grouped[_fields(metadata)].append(job)
        metadata_by_job[id(job)] = metadata

    groups: list[dict[str, Any]] = []
    for fields, group_jobs in grouped.items():
        group_jobs.sort(key=lambda job: _utc(getattr(job, "created_at", None)), reverse=True)
        jobs_by_run: dict[str, list[Any]] = defaultdict(list)
        for job in group_jobs:
            jobs_by_run[_text(getattr(job, "cpt_run_id", ""))].append(job)

        entries: list[dict[str, Any]] = []
        for job in group_jobs:
            metadata = metadata_by_job.get(id(job), {})
            workloads = metadata.get("workloads") or []
            if not isinstance(workloads, list):
                workloads = []
            model_key = (
                _text(metadata.get("model_key"))
                or _text(metadata.get("model_preset"))
                or _text(getattr(job, "preset", ""))
                or _job_name(job)
            )
            entries.append({
                "job": job,
                "run_id": _text(getattr(job, "cpt_run_id", "")),
                "job_name": _job_name(job) or None,
                "model_key": model_key,
                "model_name": _text(metadata.get("model_name")) or model_key,
                "workloads": [
                    str(item).strip()
                    for item in workloads
                    if str(item or "").strip()
                ],
                "status": _status(job),
                "message": _text(getattr(job, "cpt_stop_message", "")) or _text(getattr(job, "message", "")),
                "created_at": _utc(getattr(job, "created_at", None)),
                "source": _text(metadata.get("forge_source")) or "Unknown",
                "owner": _text(metadata.get("owner")) or _text(getattr(job, "owner", "")) or "Unknown",
                "configured_model_count": metadata.get("configured_model_count"),
                "run_started_at": _text(metadata.get("started_at")),
            })

        models_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            models_by_key[entry["model_key"] or entry["job_name"] or entry["run_id"]].append(entry)

        discovered_profiles = {
            profile for entry in entries for profile in entry["workloads"]
        }
        profile_priority = {
            profile: index for index, profile in enumerate(PROFILE_ORDER)
        }
        all_profiles = sorted(
            discovered_profiles,
            key=lambda profile: (
                profile_priority.get(profile, len(profile_priority)), profile
            ),
        )
        models: list[dict[str, Any]] = []
        latest_cells: list[dict[str, Any]] = []
        for model_key, attempts in models_by_key.items():
            cells: dict[str, dict[str, Any]] = {}
            for attempt in attempts:  # group entries are newest first
                for profile in attempt["workloads"]:
                    cells.setdefault(profile, {
                        "profile": profile,
                        "status": attempt["status"],
                        "job_name": attempt["job_name"],
                        "message": attempt["message"],
                        "run_id": attempt["run_id"],
                        "created_at": attempt["created_at"].isoformat(),
                        "source": attempt["source"],
                    })
            latest_cells.extend(cells.values())
            model_statuses = [cell["status"] for cell in cells.values()]
            models.append({
                "model_key": model_key,
                "model_name": attempts[0]["model_name"] or model_key,
                "status": _aggregate(model_statuses) if model_statuses else attempts[0]["status"],
                "submitted": any(attempt["job_name"] for attempt in attempts),
                "attempt_count": len(attempts),
                "cells": cells,
                "latest_job_name": attempts[0]["job_name"],
                "latest_created_at": attempts[0]["created_at"].isoformat(),
            })
        models.sort(key=lambda item: item["model_name"].casefold())

        run_summaries: list[dict[str, Any]] = []
        for run_id, run_jobs in jobs_by_run.items():
            run_jobs.sort(key=lambda job: _utc(getattr(job, "created_at", None)), reverse=True)
            run_statuses = [_status(job) for job in run_jobs]
            run_metadata = _job_metadata(run_jobs[0])
            run_summaries.append({
                "id": run_id,
                "status": _aggregate(run_statuses),
                "job_count": len(run_jobs),
                "succeeded_jobs": sum(status == "Succeeded" for status in run_statuses),
                "failed_jobs": sum(status in FAILED for status in run_statuses),
                "stopped_jobs": sum(status == "Stopped" for status in run_statuses),
                "model_names": list(dict.fromkeys(
                    _text(_job_metadata(job).get("model_name"))
                    or _text(_job_metadata(job).get("model_key"))
                    for job in run_jobs
                )),
                "created_at": _text(run_metadata.get("started_at")) or _utc(
                    getattr(run_jobs[0], "created_at", None)
                ).isoformat(),
                "owner": _text(run_metadata.get("owner")) or _text(getattr(run_jobs[0], "owner", "")),
                "source": _text(run_metadata.get("forge_source")) or "Unknown",
                "jobs": [
                    {
                        "name": _job_name(job) or None,
                        "model": _text(_job_metadata(job).get("model_name")) or _text(_job_metadata(job).get("model_key")),
                        "status": _status(job),
                    }
                    for job in run_jobs
                ],
            })
        run_summaries.sort(key=lambda item: item["created_at"], reverse=True)

        sources = list(dict.fromkeys(entry["source"] for entry in entries))
        owners = list(dict.fromkeys(entry["owner"] for entry in entries))
        configured_count = next((
            int(entry["configured_model_count"])
            for entry in entries
            if isinstance(entry["configured_model_count"], int)
            and entry["configured_model_count"] >= 0
        ), None)
        cell_statuses = [cell["status"] for cell in latest_cells]
        submitted_model_keys = {
            entry["model_key"] for entry in entries if entry["job_name"]
        }
        not_submitted_model_keys = {
            entry["model_key"]
            for entry in entries
            if entry["status"] == "Not submitted"
            and entry["model_key"] not in submitted_model_keys
        }
        groups.append({
            "id": _group_id(fields),
            "project": fields[0],
            "pipeline_key": fields[1],
            "version_label": fields[2],
            "cluster": fields[3],
            "accelerator": fields[4],
            "engine": fields[5],
            "engine_version": fields[6],
            "fournos_pipeline": fields[7],
            "status": _aggregate(cell_statuses),
            "attempt_count": len(jobs_by_run),
            "models": models,
            "profiles": all_profiles,
            "configured_model_count": configured_count,
            "submitted_model_count": len(submitted_model_keys),
            "not_submitted_model_count": len(not_submitted_model_keys),
            "passed_profile_count": sum(status == "Succeeded" for status in cell_statuses),
            "failed_profile_count": sum(status in FAILED for status in cell_statuses),
            "stopped_profile_count": sum(status == "Stopped" for status in cell_statuses),
            "active_profile_count": sum(status in ACTIVE for status in cell_statuses),
            "total_profile_count": len(latest_cells),
            "sources": sources,
            "source_label": sources[0] if len(sources) == 1 else f"Mixed ({len(sources)})",
            "owners": owners,
            "owner_label": owners[0] if len(owners) == 1 else f"Multiple ({len(owners)})",
            "latest_created_at": run_summaries[0]["created_at"] if run_summaries else "",
            "attempts": run_summaries,
            "active_jobs": [
                entry["job_name"]
                for entry in entries
                if entry["job_name"] and entry["status"] in ACTIVE
            ],
        })

    groups.sort(key=lambda group: group["latest_created_at"], reverse=True)
    return groups


def redact_cpt_group_owners(group: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a CPT group without owner identities."""
    public_group = dict(group)
    public_group["owners"] = []
    public_group["owner_label"] = ""
    public_group["attempts"] = [
        {**attempt, "owner": ""}
        for attempt in group.get("attempts", [])
    ]
    return public_group


def filter_cpt_run_groups(
    groups: list[dict[str, Any]], *, project: str = "", version: str = "",
    accelerator: str = "", cluster: str = "", owner: str = "",
    show_stopped: bool = False,
) -> list[dict[str, Any]]:
    return [
        group for group in groups
        if (not project or group["project"] == project)
        and (show_stopped or group["status"] != "Stopped")
        and (not version or group["version_label"] == version)
        and (not accelerator or group["accelerator"] == accelerator)
        and (not cluster or group["cluster"] == cluster)
        and (not owner or owner in group["owners"])
    ]
