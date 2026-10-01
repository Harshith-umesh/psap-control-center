"""Fournos Testing Tab API router.

Re-implements fournos-ui routes as a JSON API for the React frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    REQUESTER_EMAIL_ANNOTATION,
    REQUESTER_NAME_ANNOTATION,
    REQUESTER_PROVIDER_ANNOTATION,
    REQUESTER_SUBJECT_ANNOTATION,
    actor_label,
    get_current_user,
    requester_annotations,
    require_admin,
    require_auth,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.schemas.fournos import (
    ClusterLockResponse,
    ClusterOverviewResponse,
    CreateClusterLockRequest,
    FournosJobSummary,
    FournosJobDetailResponse,
    GitHubPR,
    GitHubRelease,
    GithubSyncStatusResponse,
    HistoryFilterOptionsResponse,
    HistoryPreferenceResponse,
    HistoryPreferenceUpdate,
    HistoryViewState,
    HoldSlotRequest,
    JobEventResponse,
    JobListResponse,
    PullRequestSelection,
    ProjectInfoResponse,
    RecurringJobResponse,
    RunGroupCreate,
    RunGroupMembershipUpdate,
    RunGroupReference,
    RunGroupUpdate,
    ScheduleChildJobResponse,
    SlotHoldResponse,
    SubmitJobRequest,
    SubmitJobResponse,
    SubmitMatrixRequest,
    SubmitMatrixResponse,
    WorkItemConfigResponse,
    WorkItemReference,
    WorkItemUpdate,
)
from app.services import github_sync_service
from app.services import slot_hold_service as slot_holds
from app.schemas.ui_schema import ProjectUiSchemaResponse
from app.services import fournos_db_service as db_svc
from app.services import fournos_k8s_client as k8s
from app.services import pipeline_definitions
from app.services import project_adapters
from app.services import project_ui_schema
from app.services.source_provenance import extract_source_request_fields
from app.services.failure_details import (
    first_actionable_failure,
    select_failure_summary,
)
from app.services.forge_discovery import discover_projects, get_project
from app.services.work_items import (
    WorkItemValidationError,
    normalize_work_items,
    work_items_enabled,
)
from app.services.run_groups import (
    RunGroupValidationError,
    can_manage_group,
    normalize_description,
    normalize_display_name,
    normalize_group_ids,
    normalize_group_key,
    normalize_group_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fournos", tags=["fournos"])

_COMPLETED_GRACE_SECONDS = 180
_LOG_ISSUE_RE = re.compile(r"\b(?:error|warning|warn)\b", re.IGNORECASE)
_LOG_DECORATION_RE = re.compile(
    r"^\s*(?:error|warning|warn)?\s*:?\s*[-=*_]{3,}\s*$",
    re.IGNORECASE,
)
_LOG_CONTEXT_LINES = 300
_LOG_CONTEXT_HISTORY = _LOG_CONTEXT_LINES - 1
_LOG_CONTEXT_AFTER = (_LOG_CONTEXT_LINES - 1) // 2


def _is_log_issue(line: str) -> bool:
    """Return whether a log line contains a meaningful error/warning marker."""
    stripped = line.strip()
    if not stripped or not _LOG_ISSUE_RE.search(stripped):
        return False
    if _LOG_DECORATION_RE.fullmatch(stripped):
        return False
    issue_text = re.sub(
        r"^\s*(?:error|warning|warn)\s*:?\s*",
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip()
    return bool(issue_text) and not re.fullmatch(r"[-=*_]{3,}", issue_text)

_REQUESTER_ANNOTATIONS = {
    REQUESTER_SUBJECT_ANNOTATION,
    REQUESTER_EMAIL_ANNOTATION,
    REQUESTER_NAME_ANNOTATION,
    REQUESTER_PROVIDER_ANNOTATION,
}

_VERSION_KEYS = {"mcp_gateway": "infrastructure.mcp_gateway_version"}


async def _resolve_source_request(
    selection: Optional[PullRequestSelection],
    legacy_pull_sha: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate a structured PR selection and build its Fournos/DB record.

    Control Center records the exact server-side PR snapshot the user chose.
    It never silently replaces a stale selection with a newer PR head.
    """
    if selection is None:
        sha = legacy_pull_sha.strip()
        env = {"PULL_PULL_SHA": sha} if sha else {}
        return env, extract_source_request_fields({"env": env})

    repository = selection.repository.strip()
    if repository != settings.FORGE_GITHUB_REPO:
        raise HTTPException(
            400,
            "Pull request repository must match the configured Forge repository",
        )
    if not re.fullmatch(r"[0-9a-fA-F]{40}", selection.requested_sha.strip()):
        raise HTTPException(400, "Pull request SHA must be a full 40-character commit SHA")

    prs = _open_prs_cache
    if prs is None:
        try:
            prs = await _fetch_open_prs_coalesced()
        except Exception as exc:
            raise HTTPException(
                502, "Unable to validate pull request selection"
            ) from exc
    current = next(
        (pr for pr in prs if pr.get("number") == selection.number), None
    )
    if current is None:
        raise HTTPException(
            409,
            "The selected pull request is no longer in the current Forge PR snapshot; refresh and select it again",
        )

    expected_url = current["url"]
    requested_sha = selection.requested_sha.strip().lower()
    if (
        current["repository"] != repository
        or current["head_sha"].lower() != requested_sha
        or current["branch"] != selection.head_branch
        or expected_url != selection.url
    ):
        raise HTTPException(
            409,
            "The selected pull request changed; refresh and select it again",
        )

    repo_owner, repo_name = repository.split("/", 1)
    env = {
        "REPO_OWNER": repo_owner,
        "REPO_NAME": repo_name,
        "PULL_NUMBER": str(current["number"]),
        "PULL_TITLE": current["title"],
        "PULL_HEAD_REF": current["branch"],
        "PULL_PULL_SHA": current["head_sha"],
        "CONTROL_CENTER_REQUESTED_SHA": selection.requested_sha.strip(),
        "CONTROL_CENTER_PR_URL": expected_url,
    }
    return env, extract_source_request_fields({"env": env})


# Compatibility shims for callers that imported these helpers from the API
# module before the RHAIIS adapter was split into its own service.
def _resolve_build_source(req: SubmitJobRequest | SubmitMatrixRequest) -> str:
    return project_adapters.resolve_build_source(
        req.project, req.pull_sha, req.use_latest_main
    )


def _normalize_rhaiis_overrides(
    project: str, overrides: dict[str, Any]
) -> dict[str, Any]:
    return project_adapters.normalize_overrides(project, overrides)


# ─── helper functions ────────────────────────────────────────────────────

def _enqueue_stream_item(
    queue: asyncio.Queue,
    item: Optional[str],
) -> None:
    """Enqueue a log item from the event-loop thread without raising.

    Log lines may be dropped when a slow client fills the bounded queue. The
    terminal sentinel must always be delivered, so it replaces the oldest
    queued line when necessary.
    """
    if queue.full():
        if item is not None:
            return
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        # Another callback cannot interleave while this synchronous helper is
        # running, but retain a defensive guard for alternate event loops.
        pass

def _public_job_metadata(metadata: dict) -> dict:
    """Return job metadata without private requester identity annotations."""
    public_metadata = dict(metadata or {})
    annotations = dict(public_metadata.get("annotations") or {})
    public_metadata["annotations"] = {
        key: value
        for key, value in annotations.items()
        if key not in _REQUESTER_ANNOTATIONS
    }
    return public_metadata


def _public_job_spec(spec: dict, *, include_owner: bool) -> dict:
    """Return a copy of a job spec with identity hidden from public callers."""
    public_spec = dict(spec or {})
    if not include_owner:
        public_spec.pop("owner", None)
    return public_spec


def _verified_owner(user: dict) -> str:
    """Visible Fournos owner derived only from the authenticated identity."""
    return str(user.get("name") or actor_label(user))


def _requester_subject_for_scope(
    requester_scope: str,
    user: Optional[dict],
) -> Optional[str]:
    """Resolve the caller-controlled scope without accepting an identity."""
    if requester_scope == "all":
        return None
    subject = str((user or {}).get("subject") or "")
    if not subject:
        raise HTTPException(status_code=401, detail="Sign in to view your jobs")
    return subject


def _validate_identity_search(
    identity: str, user: Optional[dict]
) -> None:
    """Identity predicates are private even though general History is public."""
    if identity and user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to search owner or requester identity",
        )


def _visible_sort_by(sort_by: str, user: Optional[dict]) -> str:
    """Do not let anonymous ordering reveal otherwise-redacted identity."""
    if sort_by == "owner" and user is None:
        return ""
    return sort_by


def _live_job_requester_subject(job: dict) -> str:
    annotations = job.get("metadata", {}).get("annotations", {}) or {}
    return str(annotations.get(REQUESTER_SUBJECT_ANNOTATION, ""))


def _inherit_live_requester(
    job: dict,
    parent_subjects: dict[str, str],
) -> dict:
    """Resolve recurring children whose operator-created metadata has no annotations."""
    if _live_job_requester_subject(job):
        return job
    metadata = job.get("metadata", {}) or {}
    parent_name = (metadata.get("labels", {}) or {}).get(
        k8s.LABEL_RECURRING_PARENT, ""
    )
    parent_subject = parent_subjects.get(parent_name, "")
    if not parent_subject:
        return job

    resolved_job = dict(job)
    resolved_metadata = dict(metadata)
    resolved_annotations = dict(resolved_metadata.get("annotations", {}) or {})
    resolved_annotations[REQUESTER_SUBJECT_ANNOTATION] = parent_subject
    resolved_metadata["annotations"] = resolved_annotations
    resolved_job["metadata"] = resolved_metadata
    return resolved_job


def _extract_forge_info(job: dict) -> dict:
    forge = job.get("spec", {}).get("executionEngine", {}).get("forge", {})
    env = job.get("spec", {}).get("env", {})
    pr_number = env.get("PULL_NUMBER", "")
    pr_title = env.get("PULL_TITLE", "")
    repo_owner = env.get("REPO_OWNER", "")
    repo_name = env.get("REPO_NAME", "")
    repository = "/".join(
        part for part in (repo_owner, repo_name) if part
    )
    pr_url = env.get("CONTROL_CENTER_PR_URL", "")
    if not pr_url and pr_number:
        pr_url = "https://github.com/{}/{}/pull/{}".format(
            repo_owner, repo_name, pr_number
        )
    resolved_sha = env.get("PULL_PULL_SHA", "")
    return {
        "project": forge.get("project", ""),
        "args": forge.get("args", []),
        "config_overrides": forge.get("configOverrides", {}),
        "pr_number": pr_number,
        "pr_title": pr_title,
        "pr_url": pr_url,
        "repository": repository,
        "head_branch": env.get("PULL_HEAD_REF", ""),
        "requested_sha": env.get(
            "CONTROL_CENTER_REQUESTED_SHA", resolved_sha
        ),
        "resolved_sha": resolved_sha,
    }


def _parse_task_progress(message: str) -> Optional[dict]:
    m = re.search(
        r"Tasks Completed:\s*(\d+)\s*\(Failed:\s*(\d+),\s*Cancelled\s*(\d+)\),"
        r"\s*Incomplete:\s*(\d+),\s*Skipped:\s*(\d+)",
        message,
    )
    if not m:
        return None
    return {
        "completed": int(m.group(1)),
        "failed": int(m.group(2)),
        "cancelled": int(m.group(3)),
        "incomplete": int(m.group(4)),
        "skipped": int(m.group(5)),
        "total": int(m.group(1)) + int(m.group(4)) + int(m.group(5)),
    }


def _trigger_type_for_live_job(meta: dict, spec: dict, schedule_parent: str) -> str:
    if schedule_parent:
        return "recurring"
    if spec.get("scheduledStartTime"):
        return "deferred"
    if spec.get("schedule"):
        return "recurring-parent"
    return "manual"


def _live_job_to_summary(
    job: dict,
    *,
    include_owner: bool = True,
    work_items: Optional[list[dict[str, str]]] = None,
) -> dict:
    meta = job.get("metadata", {})
    spec = job.get("spec", {})
    status = job.get("status", {})
    forge = spec.get("executionEngine", {}).get("forge", {})
    schedule_parent = meta.get("labels", {}).get(k8s.LABEL_RECURRING_PARENT, "")

    return {
        "name": meta.get("name", ""),
        "project": forge.get("project", ""),
        "preset": " ".join(forge.get("args", [])),
        "cluster": spec.get("cluster", ""),
        "pipeline": spec.get("pipeline", ""),
        "owner": spec.get("owner", "") if include_owner else "",
        "status": status.get("phase", "Pending"),
        "message": status.get("message", ""),
        "created_at": meta.get("creationTimestamp", ""),
        "completed_at": None,
        "duration_seconds": None,
        "mlflow_url": "",
        "trigger_type": _trigger_type_for_live_job(meta, spec, schedule_parent),
        "triggered_by_schedule": schedule_parent or None,
        "scheduled_start_time": spec.get("scheduledStartTime"),
        "source": "live",
        "work_items": work_items or [],
        **extract_source_request_fields(spec),
    }


def _forge_execution_summary(execution: Optional[dict]) -> dict:
    execution = execution or {}
    versions = execution.get("gitVersions", []) or []
    images = execution.get("images", []) or []
    version = next(
        (item.get("version", "") for item in versions if isinstance(item, dict)),
        "",
    )
    image_id = next(
        (item.get("imageID", "") for item in images if isinstance(item, dict)),
        "",
    )
    digest = image_id.rsplit("@", 1)[-1] if "@" in image_id else image_id
    return {
        "forge_git_version": version,
        "forge_image_digest": digest,
    }


def _db_job_to_summary(job, *, include_owner: bool = True) -> dict:
    return {
        "name": job.name,
        "project": job.project,
        "preset": job.preset,
        "cluster": job.cluster,
        "pipeline": job.pipeline,
        "owner": job.owner if include_owner else "",
        "status": job.status,
        "message": job.message,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else ""
        ),
        "duration_seconds": job.duration_seconds,
        "mlflow_url": job.mlflow_url or "",
        "trigger_type": job.trigger_type or "manual",
        "triggered_by_schedule": job.triggered_by_schedule,
        "source": "history",
        "source_repository": job.source_repository or "",
        "source_pr_number": job.source_pr_number,
        "source_pr_url": job.source_pr_url or "",
        "source_head_branch": job.source_head_branch or "",
        "source_requested_sha": job.source_requested_sha or "",
        "source_resolved_sha": job.source_resolved_sha or "",
        **_forge_execution_summary(job.forge_execution),
        "forge_provenance_state": job.forge_provenance_state or "pending",
        "work_items": (
            db_svc.serialize_work_items(job) if include_owner else []
        ),
        "run_groups": db_svc.serialize_run_groups(job),
    }


def _can_edit_work_items(job, user: Optional[dict]) -> bool:
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    subject = str(user.get("subject") or "")
    return bool(subject and subject == (job.requester_subject or ""))


def _can_edit_run_groups(job, user: Optional[dict]) -> bool:
    return _can_edit_work_items(job, user)


def _db_job_to_fjob_dict(job) -> dict:
    spec = job.fjob_spec or {}
    status = job.fjob_status or {}

    forge = spec.get("executionEngine", {}).get("forge", {})
    if not forge:
        forge = {
            "project": job.project,
            "args": job.preset.split() if job.preset else [],
            "configOverrides": job.config_overrides or {},
        }
        spec.setdefault("executionEngine", {})["forge"] = forge

    spec.setdefault("cluster", job.cluster)
    spec.setdefault("pipeline", job.pipeline)
    spec.setdefault("owner", job.owner)
    spec.setdefault("displayName", "{} {}".format(job.project, job.preset).strip())
    if job.source_resolved_sha:
        env = spec.setdefault("env", {})
        if job.source_repository and "/" in job.source_repository:
            repo_owner, repo_name = job.source_repository.split("/", 1)
            env.setdefault("REPO_OWNER", repo_owner)
            env.setdefault("REPO_NAME", repo_name)
        if job.source_pr_number:
            env.setdefault("PULL_NUMBER", str(job.source_pr_number))
        if job.source_head_branch:
            env.setdefault("PULL_HEAD_REF", job.source_head_branch)
        if job.source_requested_sha:
            env.setdefault(
                "CONTROL_CENTER_REQUESTED_SHA", job.source_requested_sha
            )
        if job.source_pr_url:
            env.setdefault("CONTROL_CENTER_PR_URL", job.source_pr_url)
        env.setdefault("PULL_PULL_SHA", job.source_resolved_sha)

    status.setdefault("phase", job.status)
    status.setdefault("message", job.message)
    status.setdefault("conditions", [])

    return {
        "metadata": {
            "name": job.name,
            "namespace": settings.FOURNOS_NAMESPACE,
            "creationTimestamp": (
                job.created_at.isoformat() if job.created_at else ""
            ),
            "uid": job.id,
            "annotations": {
                key: value for key, value in {
                    REQUESTER_SUBJECT_ANNOTATION: job.requester_subject,
                    REQUESTER_EMAIL_ANNOTATION: job.requester_email,
                    REQUESTER_NAME_ANNOTATION: job.requester_name,
                    REQUESTER_PROVIDER_ANNOTATION: job.auth_provider,
                }.items() if value
            },
        },
        "spec": spec,
        "status": status,
        "source": "history",
        "duration_seconds": job.duration_seconds,
        "mlflow_url": job.mlflow_url or "",
        "ci_artifacts_url": job.ci_artifacts_url or "",
    }


def _get_live_jobs_sync() -> list:
    from dateutil.parser import parse

    jobs = k8s.list_fournos_jobs()
    parent_subjects = {
        job.get("metadata", {}).get("name", ""): _live_job_requester_subject(job)
        for job in jobs
        if job.get("spec", {}).get("schedule")
    }
    now = datetime.now(timezone.utc)
    visible = []
    for j in jobs:
        # Cluster locks are FournosJobs too (spec.lockOnly) but they're
        # surfaced separately via /cluster-locks (Schedules/Locks tab, the
        # scheduling calendar's `locks` array) — without this they'd show
        # up a second time here as if they were an ordinary running job.
        if j.get("spec", {}).get("lockOnly"):
            continue
        # Likewise, a recurring *template* (spec.schedule set) isn't itself
        # a run — it's surfaced in the Schedules/Locks tab's Recurring Jobs
        # list. Its actual child runs (fournos.dev/recurring-parent label,
        # no spec.schedule of their own) are real jobs and stay visible here.
        if j.get("spec", {}).get("schedule"):
            continue
        phase = j.get("status", {}).get("phase", "")
        if phase in ("Succeeded", "Failed", "Stopped"):
            conditions = j.get("status", {}).get("conditions", [])
            last_ts = None
            for c in conditions:
                ts_str = c.get("lastTransitionTime")
                if ts_str:
                    try:
                        last_ts = parse(ts_str)
                    except Exception:
                        pass
            if last_ts and (now - last_ts).total_seconds() > _COMPLETED_GRACE_SECONDS:
                continue
        visible.append(_inherit_live_requester(j, parent_subjects))
    visible.sort(
        key=lambda j: j.get("metadata", {}).get("creationTimestamp", ""),
        reverse=True,
    )
    return visible


async def _get_live_jobs() -> list:
    return await asyncio.to_thread(_get_live_jobs_sync)


# _merge_pipeline_stages moved to pipeline_definitions.merge_pipeline_stages
# — shared with fournos_watcher.py, which snapshots the merged stage list
# into the DB when a job finishes so History can show it too.


def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse a UTC ISO-8601 timestamp (as sent by the frontend's
    timezone.ts, always ending in "Z") into an aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# Sortable-column keys shared by every jobs-table column header on the
# frontend (Live Jobs + History use these; "date" and "duration" only make
# sense for history, since a live job has no completed_at/duration yet —
# see the fallback to created_at below).
_LIVE_SORT_KEYS = {
    "name": lambda s: (s.get("name") or "").lower(),
    "project": lambda s: (s.get("project") or "").lower(),
    "cluster": lambda s: (s.get("cluster") or "").lower(),
    "status": lambda s: (s.get("status") or "").lower(),
    "owner": lambda s: (s.get("owner") or "").lower(),
    "date": lambda s: s.get("created_at") or "",
    "age": lambda s: s.get("created_at") or "",
    "triggered_by": lambda s: (s.get("triggered_by_schedule") or "").lower(),
}


def _live_sort_key(sort_by: str):
    """Resolve a requested key, defaulting unknown values to newest-first age."""
    return _LIVE_SORT_KEYS.get(sort_by or "age", _LIVE_SORT_KEYS["age"])


def _sort_latest(items: List[dict], *date_fields: str) -> List[dict]:
    """Return API rows newest-first using the first populated date field.

    Kubernetes list ordering is not a stable API contract. Keep every Testing
    list deterministic at the backend boundary, while allowing the frontend's
    sortable tables to apply a different user-selected order afterward.
    """
    return sorted(
        items,
        key=lambda item: (
            next((item.get(field) for field in date_fields if item.get(field)), ""),
            item.get("name", ""),
        ),
        reverse=True,
    )


# ─── routes: jobs ────────────────────────────────────────────────────────

@router.get("/jobs", response_model=JobListResponse)
@router.get("/jobs/", response_model=JobListResponse, include_in_schema=False)
@router.get("/runs", response_model=JobListResponse)
@router.get("/runs/", response_model=JobListResponse, include_in_schema=False)
async def list_jobs(
    request: Request,
    tab: str = Query("live", regex="^(live|history)$"),
    project: str = Query(""),
    cluster: str = Query(""),
    status: str = Query(""),
    owner: str = Query(""),
    requester_scope: str = Query("all", pattern="^(all|mine)$"),
    q: str = Query("", max_length=200),
    identity: str = Query("", max_length=255),
    failure_outcome: str = Query(
        "", pattern="^$|^(failed|cancelled|infrastructure_error|unknown)$"
    ),
    repository: str = Query("", max_length=255),
    pr_number: Optional[int] = Query(None, ge=1, le=2147483647),
    source_sha: str = Query(
        "", max_length=64, pattern=r"^$|^[0-9a-fA-F]{4,64}$"
    ),
    forge: str = Query("", max_length=255),
    work_item_provider: str = Query("", max_length=50),
    work_item_key: str = Query("", max_length=100),
    tags: str = Query("", max_length=2020),
    start_time: Optional[str] = Query(None, description="ISO 8601 UTC — history tab only"),
    end_time: Optional[str] = Query(None, description="ISO 8601 UTC — history tab only"),
    sort_by: str = Query(""),
    sort_dir: str = Query("desc", regex="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    current_user = get_current_user(request)
    include_owner = current_user is not None
    effective_sort_by = _visible_sort_by(sort_by, current_user)
    _validate_identity_search(identity, current_user)
    if (work_item_provider or work_item_key) and current_user is None:
        raise HTTPException(401, "Sign in to filter by work item")
    parsed_tags = []
    for raw_tag in tags.split(","):
        tag = raw_tag.strip()
        if not tag or tag in parsed_tags:
            continue
        if len(tag) > 100:
            raise HTTPException(400, "Tags must be at most 100 characters")
        parsed_tags.append(tag)
    if len(parsed_tags) > 20:
        raise HTTPException(400, "At most 20 tags may be filtered at once")
    requester_subject = _requester_subject_for_scope(
        requester_scope, current_user
    )
    if tab == "live":
        jobs = await _get_live_jobs()
        if requester_subject:
            missing_parent_names = {
                (job.get("metadata", {}).get("labels", {}) or {}).get(
                    k8s.LABEL_RECURRING_PARENT, ""
                )
                for job in jobs
                if not _live_job_requester_subject(job)
            }
            missing_parent_names.discard("")
            if missing_parent_names:
                async with AsyncSessionLocal() as session:
                    archived_parent_subjects = (
                        await db_svc.get_requester_subjects_by_names(
                            session, sorted(missing_parent_names)
                        )
                    )
                jobs = [
                    _inherit_live_requester(job, archived_parent_subjects)
                    for job in jobs
                ]
            jobs = [
                job for job in jobs
                if _live_job_requester_subject(job) == requester_subject
            ]
        if project:
            jobs = [
                j for j in jobs
                if _extract_forge_info(j).get("project") == project
            ]
        if cluster:
            jobs = [
                j for j in jobs
                if j.get("spec", {}).get("cluster") == cluster
            ]
        if status:
            jobs = [
                j for j in jobs
                if j.get("status", {}).get("phase") == status
            ]
        if owner and include_owner:
            jobs = [
                j for j in jobs
                if j.get("spec", {}).get("owner") == owner
            ]
        work_items_by_name: dict[str, list[dict[str, str]]] = {}
        if include_owner:
            async with AsyncSessionLocal() as session:
                work_items_by_name = await db_svc.get_work_items_by_job_names(
                    session,
                    [j.get("metadata", {}).get("name", "") for j in jobs],
                )
        if work_item_provider or work_item_key:
            jobs = [
                job for job in jobs
                if any(
                    (not work_item_provider or item["provider"] == work_item_provider.lower())
                    and (not work_item_key or item["key"] == work_item_key.upper())
                    for item in work_items_by_name.get(
                        job.get("metadata", {}).get("name", ""), []
                    )
                )
            ]
        summaries = [
            _live_job_to_summary(
                j,
                include_owner=include_owner,
                work_items=work_items_by_name.get(
                    j.get("metadata", {}).get("name", ""), []
                ),
            )
            for j in jobs
        ]
        key_fn = _live_sort_key(effective_sort_by)
        summaries.sort(key=key_fn, reverse=(sort_dir == "desc"))
        total = len(summaries)
        offset = (page - 1) * per_page
        summaries = summaries[offset: offset + per_page]
    else:
        created_after = _parse_iso_utc(start_time)
        created_before = _parse_iso_utc(end_time)
        async with AsyncSessionLocal() as session:
            db_jobs, total = await db_svc.list_jobs(
                session,
                project=project or None,
                cluster=cluster or None,
                status=status or None,
                owner=(owner or None) if include_owner else None,
                requester_subject=requester_subject,
                query=q.strip() or None,
                identity=identity.strip() or None,
                failure_outcome=failure_outcome.strip() or None,
                repository=repository.strip() or None,
                pr_number=pr_number,
                source_sha=source_sha.strip() or None,
                forge=forge.strip() or None,
                tags=parsed_tags or None,
                work_item_provider=work_item_provider.strip() or None,
                work_item_key=work_item_key.strip() or None,
                created_after=created_after,
                created_before=created_before,
                sort_by=effective_sort_by or None,
                sort_dir=sort_dir,
                limit=per_page,
                offset=(page - 1) * per_page,
            )
        summaries = [
            _db_job_to_summary(j, include_owner=include_owner) for j in db_jobs
        ]

    return {
        "jobs": summaries,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get(
    "/history/filter-options",
    response_model=HistoryFilterOptionsResponse,
)
async def get_history_filter_options(request: Request):
    async with AsyncSessionLocal() as session:
        options = await db_svc.get_history_filter_options(
            session,
            include_identity=get_current_user(request) is not None,
        )
    return HistoryFilterOptionsResponse(**options)


@router.get(
    "/history/preferences",
    response_model=HistoryPreferenceResponse,
)
async def get_history_preferences(user: dict = Depends(require_auth)):
    subject = str(user.get("subject") or "")
    async with AsyncSessionLocal() as session:
        preference = await db_svc.get_history_preference(session, subject)
    if preference is None:
        return HistoryPreferenceResponse()
    return HistoryPreferenceResponse(
        schema_version=preference.schema_version,
        state=HistoryViewState.model_validate(preference.state or {}),
        updated_at=preference.updated_at,
    )


@router.put(
    "/history/preferences",
    response_model=HistoryPreferenceResponse,
)
async def update_history_preferences(
    body: HistoryPreferenceUpdate,
    user: dict = Depends(require_auth),
):
    subject = str(user.get("subject") or "")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            preference = await db_svc.save_history_preference(
                session,
                subject=subject,
                schema_version=1,
                state=body.state.model_dump(),
            )
    return HistoryPreferenceResponse(
        schema_version=preference.schema_version,
        state=HistoryViewState.model_validate(preference.state or {}),
        updated_at=preference.updated_at,
    )


@router.delete("/history/preferences")
async def reset_history_preferences(user: dict = Depends(require_auth)):
    subject = str(user.get("subject") or "")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            deleted = await db_svc.delete_history_preference(
                session, subject
            )
    return {"status": "ok", "deleted": deleted}


@router.get("/work-items/config", response_model=WorkItemConfigResponse)
async def get_work_item_config():
    enabled = work_items_enabled()
    return {
        "enabled": enabled,
        "providers": ["jira"] if enabled else [],
    }


@router.get("/run-groups", response_model=List[RunGroupReference])
async def get_run_groups(
    include_archived: bool = False,
    group_type: str = Query("", max_length=50),
    q: str = Query("", max_length=100),
    _=Depends(require_auth),
):
    normalized_type = ""
    if group_type:
        try:
            normalized_type = normalize_group_type(group_type)
        except RunGroupValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
    async with AsyncSessionLocal() as session:
        groups = await db_svc.list_run_groups(
            session,
            include_archived=include_archived,
            group_type=normalized_type or None,
            query=q.strip() or None,
        )
        return [db_svc.serialize_run_group(group) for group in groups]


@router.post(
    "/run-groups",
    response_model=RunGroupReference,
    status_code=201,
)
async def create_run_group(
    body: RunGroupCreate,
    user: dict = Depends(require_auth),
):
    try:
        values = {
            "group_type": normalize_group_type(body.group_type),
            "key": normalize_group_key(body.key),
            "display_name": normalize_display_name(body.display_name),
            "description": normalize_description(body.description),
        }
    except RunGroupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        async with AsyncSessionLocal() as session, session.begin():
            group = await db_svc.create_run_group(
                session,
                **values,
                created_by_subject=str(user.get("subject") or ""),
            )
            result = db_svc.serialize_run_group(group)
    except IntegrityError as exc:
        raise HTTPException(
            409, "A run group with this type and key already exists"
        ) from exc
    return result


@router.patch(
    "/run-groups/{group_id}", response_model=RunGroupReference
)
async def update_run_group(
    group_id: str,
    body: RunGroupUpdate,
    user: dict = Depends(require_auth),
):
    try:
        normalized_id = normalize_group_ids([group_id])[0]
        display_name = (
            normalize_display_name(body.display_name)
            if body.display_name is not None else None
        )
        description = (
            normalize_description(body.description)
            if body.description is not None else None
        )
    except RunGroupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    async with AsyncSessionLocal() as session, session.begin():
        group = await db_svc.get_run_group(session, normalized_id)
        if group is None:
            raise HTTPException(404, "Run group not found")
        if not can_manage_group(group, user):
            raise HTTPException(
                403, "Only the group owner or an administrator may update it"
            )
        if display_name is not None:
            group.display_name = display_name
        if description is not None:
            group.description = description
        if body.archived is not None:
            group.archived = body.archived
        await session.flush()
        result = db_svc.serialize_run_group(group)
    return result


@router.get(
    "/jobs/{job_name}/run-groups",
    response_model=List[RunGroupReference],
)
async def get_job_run_groups(job_name: str, _=Depends(require_auth)):
    async with AsyncSessionLocal() as session:
        job = await db_svc.get_job_by_name(session, job_name)
        if job is None:
            raise HTTPException(404, "Job not found")
        return db_svc.serialize_run_groups(job)


@router.put(
    "/jobs/{job_name}/run-groups",
    response_model=List[RunGroupReference],
)
async def update_job_run_groups(
    job_name: str,
    body: RunGroupMembershipUpdate,
    user: dict = Depends(require_auth),
):
    try:
        group_ids = normalize_group_ids(body.group_ids)
    except RunGroupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    async with AsyncSessionLocal() as session, session.begin():
        job = await db_svc.get_job_by_name(session, job_name)
        if job is None:
            raise HTTPException(404, "Job not found")
        if not _can_edit_run_groups(job, user):
            raise HTTPException(
                403,
                "Only the recorded requester or an administrator may edit run groups",
            )
        try:
            await db_svc.replace_run_groups(
                session,
                job,
                group_ids,
                created_by_subject=str(user.get("subject") or ""),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        await session.refresh(job, attribute_names=["group_memberships"])
        return db_svc.serialize_run_groups(job)


@router.get(
    "/jobs/{job_name}/work-items",
    response_model=List[WorkItemReference],
)
async def get_job_work_items(job_name: str, _=Depends(require_auth)):
    async with AsyncSessionLocal() as session:
        job = await db_svc.get_job_by_name(session, job_name)
        if job is None:
            raise HTTPException(404, "Job not found")
        return db_svc.serialize_work_items(job)


@router.put(
    "/jobs/{job_name}/work-items",
    response_model=List[WorkItemReference],
)
async def update_job_work_items(
    job_name: str,
    body: WorkItemUpdate,
    user: dict = Depends(require_auth),
):
    try:
        normalized = normalize_work_items(body.work_items)
    except WorkItemValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    async with AsyncSessionLocal() as session, session.begin():
        job = await db_svc.get_job_by_name(session, job_name)
        if job is None:
            raise HTTPException(404, "Job not found")
        if not _can_edit_work_items(job, user):
            raise HTTPException(
                403,
                "Only the recorded requester or an administrator may edit work items",
            )
        await db_svc.replace_work_items(
            session,
            job,
            normalized,
            created_by_subject=str(user.get("subject") or ""),
        )
    return normalized


@router.get("/jobs/{job_name}", response_model=FournosJobDetailResponse)
async def get_job(job_name: str, request: Request):
    current_user = get_current_user(request)
    include_owner = current_user is not None
    job = await asyncio.to_thread(k8s.get_fournos_job, job_name)

    pods = []
    stages = []
    current_step = None

    if job:
        pr_name = job.get("status", {}).get("pipelineRun", "")

        # Pods and the PipelineRun are independent of each other — fetch
        # them concurrently instead of one-after-another. (Each of these
        # is itself a single blocking K8s API call, dispatched to a worker
        # thread via asyncio.to_thread so the event loop isn't blocked.)
        pods_task = asyncio.to_thread(k8s.list_pods_for_job, job_name)
        if pr_name:
            pr_task = asyncio.to_thread(k8s.get_pipelinerun, pr_name)
        else:
            pr_task = asyncio.to_thread(k8s.list_pipelineruns_for_job, job_name)
        pods_raw, pr_result = await asyncio.gather(pods_task, pr_task)

        # Preserve the previous label-based fallback for stale/missing
        # status.pipelineRun references without adding calls to the happy path.
        if pr_name and not pr_result:
            prs = await asyncio.to_thread(
                k8s.list_pipelineruns_for_job, job_name
            )
            pr_result = prs[0] if prs else None

        pods = [
            {
                "name": p["name"],
                "phase": p["phase"],
                "container": p["container"],
                "ready": p["ready"],
                "restarts": p["restarts"],
                "age_minutes": p["age_minutes"],
                "exit_code": p.get("exit_code"),
                "term_reason": p.get("term_reason", ""),
                "term_message": p.get("term_message", ""),
            }
            for p in pods_raw
        ]

        pr = pr_result if pr_name else (pr_result[0] if pr_result else None)
        if pr:
            # extract_pipeline_stages itself fans out one K8s call per
            # TaskRun concurrently (see fournos_k8s_client.py) rather than
            # one after another, so this one to_thread call is the only
            # slow leg left in the chain.
            stages = await asyncio.to_thread(
                k8s.extract_pipeline_stages, pr
            )

        pipeline_name = job.get("spec", {}).get("pipeline", "")
        if pipeline_name:
            pipeline_def = await pipeline_definitions.get_definition(pipeline_name)
            stages = pipeline_definitions.merge_pipeline_stages(pipeline_def, stages)

        # Derived straight from the stages we already fetched above —
        # no need for get_current_step_for_job's own separate PipelineRun
        # list + a second, redundant round of per-TaskRun fetches for the
        # exact same data extract_pipeline_stages just retrieved.
        running_stage = next((s for s in stages if s.get("status") == "Running"), None)
        current_step = (
            {
                "name": running_stage["name"],
                "displayName": running_stage.get("displayName", running_stage["name"]),
                "startTime": running_stage.get("startTime"),
            }
            if running_stage
            else None
        )

        forge_info = _extract_forge_info(job)
        task_progress = _parse_task_progress(
            job.get("status", {}).get("message", "")
        )
        phase = job.get("status", {}).get("phase", "")
        failure_summary = first_actionable_failure(
            stages,
            job_phase=phase,
            job_message=job.get("status", {}).get("message", ""),
        )
        enrichment_state = "pending" if phase in (
            "Succeeded", "Failed", "Stopped"
        ) else "not_applicable"
        async with AsyncSessionLocal() as session:
            archived = await db_svc.get_job_by_name(session, job_name)
        if phase in ("Succeeded", "Failed", "Stopped"):
            if archived:
                failure_summary = select_failure_summary(
                    failure_summary, archived.failure_summary
                )
                enrichment_state = (
                    archived.failure_enrichment_state or enrichment_state
                )

        return {
            "job": {
                "metadata": _public_job_metadata(job.get("metadata", {})),
                "spec": _public_job_spec(
                    job.get("spec", {}), include_owner=include_owner
                ),
                "status": job.get("status", {}),
                "source": "live",
                "duration_seconds": None,
                "mlflow_url": (archived.mlflow_url or "") if archived else "",
                "ci_artifacts_url": "",
            },
            "pods": pods,
            "stages": stages,
            "current_step": current_step,
            "forge_info": forge_info,
            "task_progress": task_progress,
            "failure_summary": failure_summary or None,
            "failure_enrichment_state": enrichment_state,
            "forge_execution": (
                archived.forge_execution or {} if archived else {}
            ),
            "forge_provenance_state": (
                archived.forge_provenance_state or "pending"
                if archived else "pending"
            ),
            "work_items": (
                db_svc.serialize_work_items(archived)
                if archived and include_owner else []
            ),
            "can_edit_work_items": (
                _can_edit_work_items(archived, current_user)
                if archived else False
            ),
            "run_groups": (
                db_svc.serialize_run_groups(archived) if archived else []
            ),
            "can_edit_run_groups": (
                _can_edit_run_groups(archived, current_user)
                if archived else False
            ),
        }

    async with AsyncSessionLocal() as session:
        db_job = await db_svc.get_job_by_name(session, job_name)
    if db_job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    fjob = _db_job_to_fjob_dict(db_job)
    forge_info = _extract_forge_info(fjob)
    task_progress = _parse_task_progress(
        fjob.get("status", {}).get("message", "")
    )
    stages = db_job.stages or []
    # A terminal job has no live "current" step. For a failed job, retain the
    # useful pointer to the concrete TaskRun that failed, but never promote a
    # queued/not-run placeholder to current work.
    failed_stage = next(
        (s for s in stages if s.get("status") == "Failed"), None
    ) if db_job.status == "Failed" else None
    current_step = (
        {
            "name": failed_stage["name"],
            "displayName": failed_stage.get("displayName", failed_stage["name"]),
            "startTime": failed_stage.get("startTime"),
        }
        if failed_stage
        else None
    )

    return {
        "job": {
            **fjob,
            "metadata": _public_job_metadata(fjob.get("metadata", {})),
            "spec": _public_job_spec(
                fjob.get("spec", {}), include_owner=include_owner
            ),
        },
        "pods": [],
        "stages": stages,
        "current_step": current_step,
        "forge_info": forge_info,
        "task_progress": task_progress,
        "failure_summary": db_job.failure_summary or None,
        "failure_enrichment_state": (
            db_job.failure_enrichment_state or "unavailable"
        ),
        "forge_execution": db_job.forge_execution or {},
        "forge_provenance_state": (
            db_job.forge_provenance_state or "unavailable"
        ),
        "work_items": (
            db_svc.serialize_work_items(db_job) if include_owner else []
        ),
        "can_edit_work_items": _can_edit_work_items(db_job, current_user),
        "run_groups": db_svc.serialize_run_groups(db_job),
        "can_edit_run_groups": _can_edit_run_groups(db_job, current_user),
    }


@router.get("/jobs/{job_name}/events", response_model=List[JobEventResponse])
async def get_job_events(job_name: str):
    async with AsyncSessionLocal() as session:
        db_job = await db_svc.get_job_by_name(session, job_name)
        if db_job is None:
            raise HTTPException(404, "Job not found in history")
        events = await db_svc.get_job_events(session, db_job.id)
        return [
            {
                "id": e.id,
                "phase": e.phase,
                "message": e.message,
                "timestamp": e.timestamp,
            }
            for e in events
        ]


@router.post("/jobs/{job_name}/cancel")
async def cancel_job(job_name: str, _=Depends(require_admin)):
    try:
        await asyncio.to_thread(k8s.shutdown_fournos_job, job_name)
        return {"status": "ok", "message": "Shutdown requested for {}".format(job_name)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/jobs/{job_name}/rerun")
async def rerun_job(job_name: str, user=Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        source_db_job = await db_svc.get_job_by_name(session, job_name)
        source_work_items = (
            db_svc.serialize_work_items(source_db_job)
            if source_db_job else []
        )
        source_run_group_ids = (
            [
                membership.group_id
                for membership in source_db_job.group_memberships or []
            ]
            if source_db_job else []
        )
    job = await asyncio.to_thread(k8s.get_fournos_job, job_name)
    if not job:
        if source_db_job:
            job = _db_job_to_fjob_dict(source_db_job)

    if job is None:
        raise HTTPException(404, "Job not found")

    spec = dict(job.get("spec", {}))
    forge = spec.get("executionEngine", {}).get("forge", {})
    project = forge.get("project", "unknown")
    spec.pop("shutdown", None)
    spec["owner"] = _verified_owner(user)

    new_name = k8s.sanitize_job_name("forge-{}".format(project))
    body = {
        "apiVersion": "{}/{}".format(
            settings.FOURNOS_API_GROUP, settings.FOURNOS_API_VERSION
        ),
        "kind": "FournosJob",
        "metadata": {
            "name": new_name,
            "namespace": settings.FOURNOS_NAMESPACE,
            "annotations": requester_annotations(user),
        },
        "spec": spec,
    }

    source_fields = extract_source_request_fields(spec)
    initial_status = (
        "Recurring" if spec.get("schedule")
        else "Scheduled" if spec.get("scheduledStartTime")
        else "Pending"
    )
    try:
        async with AsyncSessionLocal() as session, session.begin():
            db_job = await db_svc.upsert_job(
                session,
                name=new_name,
                project=project,
                preset=" ".join(forge.get("args", [])),
                cluster=spec.get("cluster", ""),
                pipeline=spec.get("pipeline", ""),
                owner=spec.get("owner", ""),
                requester_subject=user.get("subject", ""),
                requester_email=user.get("email", ""),
                requester_name=user.get("name", ""),
                auth_provider=user.get("auth_provider", ""),
                status=initial_status,
                config_overrides=forge.get("configOverrides", {}),
                fjob_spec=spec,
                trigger_type=(
                    "recurring-parent" if spec.get("schedule")
                    else "deferred" if spec.get("scheduledStartTime")
                    else "manual"
                ),
                **source_fields,
            )
            if source_work_items:
                await db_svc.replace_work_items(
                    session,
                    db_job,
                    source_work_items,
                    created_by_subject=str(user.get("subject") or ""),
                )
            if source_run_group_ids:
                await db_svc.replace_run_groups(
                    session,
                    db_job,
                    source_run_group_ids,
                    created_by_subject=str(user.get("subject") or ""),
                    allow_archived=True,
                )
    except Exception as exc:
        logger.error("Could not persist rerun intent for %s: %s", new_name, exc)
        raise HTTPException(500, "Failed to persist job rerun") from exc

    try:
        created = await asyncio.to_thread(k8s.create_fournos_job, body)
        created_name = created.get("metadata", {}).get("name", new_name)
        return {
            "status": "ok",
            "job_name": created_name,
            "redirect": "/testing/jobs/{}".format(created_name),
        }
    except Exception as exc:
        try:
            async with AsyncSessionLocal() as session, session.begin():
                await db_svc.delete_job_by_name(session, new_name)
        except Exception as cleanup_exc:
            logger.error(
                "Failed to remove provisional rerun %s: %s",
                new_name,
                cleanup_exc,
            )
        raise HTTPException(500, str(exc))


@router.delete("/history/{job_name}")
async def delete_history_job(job_name: str, _=Depends(require_admin)):
    async with AsyncSessionLocal() as session, session.begin():
        deleted = await db_svc.delete_job_by_name(session, job_name)
    if not deleted:
        raise HTTPException(404, "Job not found in history")
    return {"status": "ok"}


@router.get("/jobs/{job_name}/logs/{pod_name}")
async def stream_logs(job_name: str, pod_name: str):
    """Stream the latest warning/error context for a pod.

    The browser needs the end of a failed log, not an unbounded stream from
    the beginning. Keep the latest issue and up to 299 surrounding lines in
    memory; the complete log remains available from the download endpoint.
    """
    job_pods = await asyncio.to_thread(k8s.list_pods_for_job, job_name)
    pod_map = {p["name"]: p for p in job_pods}
    if pod_name not in pod_map:
        raise HTTPException(404, "Pod not found for this job")

    is_running = pod_map[pod_name].get("phase") in ("Running", "Pending")

    async def generate():
        stop = asyncio.Event()
        queue: asyncio.Queue[tuple[list[str], int] | None] = asyncio.Queue(maxsize=2)
        loop = asyncio.get_event_loop()

        def _publish_latest(context: tuple[list[str], int]) -> None:
            # Only the newest issue is useful. Drop stale intermediate
            # contexts if a noisy live pod produces several quickly.
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait(context)

        def _finish() -> None:
            queue.put_nowait(None)

        def _reader():
            recent_lines: deque[str] = deque(maxlen=_LOG_CONTEXT_HISTORY)
            current_issue: str | None = None
            current_before: list[str] = []
            current_after: list[str] = []

            def _build_context() -> tuple[list[str], int] | None:
                if current_issue is None:
                    return None
                after = current_after[:_LOG_CONTEXT_AFTER]
                before_limit = _LOG_CONTEXT_LINES - 1 - len(after)
                before = current_before[-before_limit:] if before_limit else []
                return before + [current_issue] + after, len(before)

            def _publish_context() -> None:
                context = _build_context()
                if context is not None:
                    loop.call_soon_threadsafe(_publish_latest, context)

            try:
                for line in k8s.read_pod_log(pod_name, follow=is_running):
                    if stop.is_set():
                        break
                    if _is_log_issue(line):
                        current_issue = line
                        current_before = list(recent_lines)
                        current_after = []
                        if is_running:
                            _publish_context()
                    elif current_issue is not None:
                        if len(current_after) < _LOG_CONTEXT_AFTER:
                            current_after.append(line)
                            if is_running and (
                                len(current_after) % 25 == 0
                                or len(current_after) == _LOG_CONTEXT_AFTER
                            ):
                                _publish_context()
                    recent_lines.append(line)
            finally:
                _publish_context()
                loop.call_soon_threadsafe(_finish)

        loop.run_in_executor(None, _reader)
        latest_context = None
        try:
            while True:
                context = await queue.get()
                if context is None:
                    break
                latest_context = context
                if is_running:
                    lines, issue_index = context
                    payload = json.dumps(
                        {"lines": lines, "issue_index": issue_index},
                        ensure_ascii=False,
                    )
                    yield "event: context\ndata: {}\n\n".format(payload)

            if not is_running and latest_context is not None:
                lines, issue_index = latest_context
                payload = json.dumps(
                    {"lines": lines, "issue_index": issue_index},
                    ensure_ascii=False,
                )
                yield "event: context\ndata: {}\n\n".format(payload)

            yield "event: complete\ndata: {}\n\n".format(
                "found" if latest_context else "none"
            )
        finally:
            stop.set()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_name}/logs/{pod_name}/download")
async def download_logs(job_name: str, pod_name: str):
    """Download the complete current log for a job pod."""
    job_pods = await asyncio.to_thread(k8s.list_pods_for_job, job_name)
    pod_names = {p["name"] for p in job_pods}
    if pod_name not in pod_names:
        raise HTTPException(404, "Pod not found for this job")

    log_text = await asyncio.to_thread(
        lambda: "\n".join(k8s.read_pod_log(pod_name, follow=False))
    )
    safe_job = re.sub(r"[^A-Za-z0-9._-]", "-", job_name)
    safe_pod = re.sub(r"[^A-Za-z0-9._-]", "-", pod_name)
    return Response(
        content=log_text,
        media_type="text/plain",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                'attachment; filename="{}-{}.log"'.format(safe_job, safe_pod)
            ),
        },
    )


# ─── routes: submit ──────────────────────────────────────────────────────

def _apply_scheduling(spec: dict, schedule: str, scheduled_start_time: Optional[str]) -> None:
    """Set spec.schedule / spec.scheduledStartTime exactly as the FournosJob
    CRD expects (see fournos/manifests/crd.yaml) — the two are mutually
    exclusive on the CRD itself, so reject both being set here too rather
    than let the operator silently pick one.
    """
    if schedule and scheduled_start_time:
        raise HTTPException(
            400, "schedule and scheduled_start_time are mutually exclusive"
        )
    if schedule:
        spec["schedule"] = schedule
    elif scheduled_start_time:
        spec["scheduledStartTime"] = scheduled_start_time


@router.post("/submit", response_model=SubmitJobResponse)
async def submit_job(req: SubmitJobRequest, user=Depends(require_auth)):
    config_overrides = project_adapters.normalize_overrides(
        req.project, dict(req.config_overrides)
    )
    owner = _verified_owner(user)
    resolved_pull_sha = project_adapters.resolve_build_source(
        req.project, req.pull_sha, req.use_latest_main
    )
    env, source_fields = await _resolve_source_request(
        req.pull_request, resolved_pull_sha
    )
    try:
        normalized_work_items = normalize_work_items(req.work_items)
        normalized_run_group_ids = normalize_group_ids(req.run_group_ids)
    except WorkItemValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RunGroupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    if req.version:
        version_key = _VERSION_KEYS.get(
            req.project, "infrastructure.version"
        )
        config_overrides[version_key] = req.version

    display_name = "{} {}".format(req.project, req.preset).strip()
    secret_refs: List[str] = []

    if req.args:
        # Generic multi-arg submission from a schema-driven form (see
        # app/schemas/ui_schema.py) — one or more preset keys, in field order.
        args = req.args
        display_name = "{} {}".format(req.project, " ".join(args)).strip()
        job_name = k8s.sanitize_job_name("forge-{}".format(req.project))
    else:
        args = [req.preset] if req.preset else []
        job_name = k8s.sanitize_job_name("forge-{}".format(req.project))

    spec: dict[str, Any] = {
        "cluster": req.cluster,
        "displayName": display_name,
        "owner": owner,
        "pipeline": req.pipeline,
        "exclusive": req.exclusive,
        "priority": req.priority,
        "executionEngine": {
            "forge": {
                "project": req.project,
                "args": args,
                "configOverrides": config_overrides,
            }
        },
    }
    if req.gpu_type.strip():
        spec["hardware"] = {
            "gpuType": req.gpu_type.strip(),
            "gpuCount": req.gpu_count or 1,
        }
    if secret_refs:
        spec["secretRefs"] = secret_refs
    _apply_scheduling(spec, req.schedule, req.scheduled_start_time)

    body: dict[str, Any] = {
        "apiVersion": "{}/{}".format(
            settings.FOURNOS_API_GROUP, settings.FOURNOS_API_VERSION
        ),
        "kind": "FournosJob",
        "metadata": {
            "name": job_name,
            "namespace": settings.FOURNOS_NAMESPACE,
            "annotations": requester_annotations(user),
        },
        "spec": spec,
    }
    if env:
        body["spec"]["env"] = env

    initial_status = (
        "Recurring" if req.schedule
        else "Scheduled" if req.scheduled_start_time
        else "Pending"
    )

    # The request record must exist before the execution resource can start.
    # If Kubernetes rejects the submission, remove the provisional row so it
    # cannot appear as a run that actually executed.
    try:
        async with AsyncSessionLocal() as session, session.begin():
            await db_svc.validate_active_run_group_ids(
                session, normalized_run_group_ids
            )
            db_job = await db_svc.upsert_job(
                session,
                name=job_name,
                project=req.project,
                preset=req.preset or " ".join(req.args),
                cluster=req.cluster,
                pipeline=req.pipeline,
                owner=owner,
                requester_subject=user.get("subject", ""),
                requester_email=user.get("email", ""),
                requester_name=user.get("name", ""),
                auth_provider=user.get("auth_provider", ""),
                status=initial_status,
                config_overrides=config_overrides,
                fjob_spec=body.get("spec", {}),
                trigger_type=(
                    "recurring-parent" if req.schedule
                    else "deferred" if req.scheduled_start_time
                    else "manual"
                ),
                **source_fields,
            )
            if normalized_work_items:
                await db_svc.replace_work_items(
                    session,
                    db_job,
                    normalized_work_items,
                    created_by_subject=str(user.get("subject") or ""),
                )
            if normalized_run_group_ids:
                await db_svc.replace_run_groups(
                    session,
                    db_job,
                    normalized_run_group_ids,
                    created_by_subject=str(user.get("subject") or ""),
                )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.error("Could not persist submission intent for %s: %s", job_name, exc)
        raise HTTPException(500, "Failed to persist job submission") from exc

    try:
        created = await asyncio.to_thread(k8s.create_fournos_job, body)
    except Exception as exc:
        try:
            async with AsyncSessionLocal() as session, session.begin():
                await db_svc.delete_job_by_name(session, job_name)
        except Exception as cleanup_exc:
            logger.error(
                "Failed to remove provisional job %s: %s",
                job_name,
                cleanup_exc,
            )
        raise HTTPException(500, "Failed to create FournosJob: {}".format(exc))

    created_name = created.get("metadata", {}).get("name", job_name)

    return {
        "status": "ok",
        "job_name": created_name,
        "redirect": "/testing/jobs/{}".format(created_name),
    }


# ─── routes: matrix (pipeline/CPT-style) submission ─────────────────────
#
# Generic across every project that declares a `kind: matrix` mode in its
# ui/submit.yaml (see app/schemas/ui_schema.py) — no project-specific code.

@router.post("/submit-matrix", response_model=SubmitMatrixResponse)
async def submit_matrix(req: SubmitMatrixRequest, user=Depends(require_auth)):
    """Submit a matrix pipeline — creates one FournosJob per model, each
    carrying all of the selected workloads plus the shared args/overrides.
    """
    if not req.models or not req.workloads:
        raise HTTPException(400, "models and workloads are required")

    owner = _verified_owner(user)
    try:
        normalized_work_items = normalize_work_items(req.work_items)
        normalized_run_group_ids = normalize_group_ids(req.run_group_ids)
    except WorkItemValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RunGroupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with AsyncSessionLocal() as session:
        try:
            await db_svc.validate_active_run_group_ids(
                session, normalized_run_group_ids
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    resolved_pull_sha = project_adapters.resolve_build_source(
        req.project, req.pull_sha, req.use_latest_main
    )
    source_env, source_fields = await _resolve_source_request(
        req.pull_request, resolved_pull_sha
    )
    model_prefixes = [
        re.sub(
            r"[^a-z0-9-]",
            "-",
            "{}-{}".format(req.project, model_item.key).lower(),
        )
        for model_item in req.models
    ]
    job_names = [k8s.sanitize_job_name(prefix) for prefix in model_prefixes]
    if (
        len(set(model_prefixes)) != len(model_prefixes)
        or len(set(job_names)) != len(job_names)
    ):
        raise HTTPException(
            400, "models must produce unique Kubernetes job names"
        )
    results = []
    for model_item, job_name in zip(req.models, job_names):
        args = list(req.args) + [model_item.key] + list(req.workloads)

        job_overrides: dict[str, Any] = dict(req.config_overrides)
        job_overrides.update({k: v for k, v in model_item.overrides.items()})
        job_overrides = project_adapters.normalize_overrides(
            req.project, job_overrides
        )
        project_adapters.add_matrix_workload_override(
            req.project, job_overrides, req.workloads
        )

        display_name = "{}-{}-{}".format(req.project, model_item.key, req.cluster)
        env = dict(source_env)

        spec: dict[str, Any] = {
            "cluster": req.cluster,
            "displayName": display_name,
            "owner": owner,
            "pipeline": req.pipeline,
            "exclusive": req.exclusive,
            "priority": req.priority,
            "executionEngine": {
                "forge": {
                    "project": req.project,
                    "args": args,
                    "configOverrides": job_overrides,
                }
            },
        }
        if req.gpu_type.strip() or model_item.gpu_count:
            spec["hardware"] = {
                "gpuType": req.gpu_type.strip() or "unknown",
                "gpuCount": model_item.gpu_count or 1,
            }
        _apply_scheduling(spec, req.schedule, req.scheduled_start_time)

        body: dict[str, Any] = {
            "apiVersion": "{}/{}".format(
                settings.FOURNOS_API_GROUP, settings.FOURNOS_API_VERSION
            ),
            "kind": "FournosJob",
            "metadata": {
                "name": job_name,
                "namespace": settings.FOURNOS_NAMESPACE,
                "annotations": requester_annotations(user),
            },
            "spec": spec,
        }
        if env:
            body["spec"]["env"] = env

        initial_status = (
            "Recurring" if req.schedule
            else "Scheduled" if req.scheduled_start_time
            else "Pending"
        )
        try:
            async with AsyncSessionLocal() as session, session.begin():
                db_job = await db_svc.upsert_job(
                    session,
                    name=job_name,
                    project=req.project,
                    preset="{} {}".format(model_item.key, " ".join(req.workloads)),
                    cluster=req.cluster,
                    pipeline=req.pipeline,
                    owner=owner,
                    requester_subject=user.get("subject", ""),
                    requester_email=user.get("email", ""),
                    requester_name=user.get("name", ""),
                    auth_provider=user.get("auth_provider", ""),
                    status=initial_status,
                    config_overrides=job_overrides,
                    fjob_spec=body.get("spec", {}),
                    trigger_type=(
                        "recurring-parent" if req.schedule
                        else "deferred" if req.scheduled_start_time
                        else "manual"
                    ),
                    **source_fields,
                )
                if normalized_work_items:
                    await db_svc.replace_work_items(
                        session,
                        db_job,
                        normalized_work_items,
                        created_by_subject=str(user.get("subject") or ""),
                    )
                if normalized_run_group_ids:
                    await db_svc.replace_run_groups(
                        session,
                        db_job,
                        normalized_run_group_ids,
                        created_by_subject=str(user.get("subject") or ""),
                    )
        except Exception as exc:
            logger.error(
                "Could not persist matrix submission intent for %s: %s",
                job_name,
                exc,
            )
            results.append({
                "model": model_item.key,
                "status": "failed",
                "error": "Failed to persist job submission",
            })
            continue

        try:
            created = await asyncio.to_thread(k8s.create_fournos_job, body)
            created_name = created.get("metadata", {}).get("name", job_name)
            results.append({
                "model": model_item.key,
                "job_name": created_name,
                "status": "created",
            })
        except Exception as exc:
            try:
                async with AsyncSessionLocal() as session, session.begin():
                    await db_svc.delete_job_by_name(session, job_name)
            except Exception as cleanup_exc:
                logger.error(
                    "Failed to remove provisional matrix job %s: %s",
                    job_name,
                    cleanup_exc,
                )
            results.append({
                "model": model_item.key,
                "status": "failed",
                "error": str(exc),
            })

    return {"status": "ok", "jobs": results, "total": len(results)}


# ─── routes: projects ───────────────────────────────────────────────────

@router.get("/projects", response_model=List[ProjectInfoResponse])
async def list_projects():
    projects = discover_projects()
    return [
        {
            "name": p.name,
            "cluster": p.cluster,
            "presets": p.presets,
            "config_keys": p.config_keys,
            "has_cli": p.has_cli,
        }
        for p in projects
    ]


@router.get("/projects/{project_name}", response_model=ProjectInfoResponse)
async def project_info(project_name: str):
    proj = get_project(project_name)
    if proj is None:
        return {"name": project_name, "presets": [], "cluster": ""}
    return {
        "name": proj.name,
        "cluster": proj.cluster,
        "presets": proj.presets,
        "config_keys": proj.config_keys,
        "has_cli": proj.has_cli,
    }


@router.get("/projects/{project_name}/ui-schema", response_model=ProjectUiSchemaResponse)
async def project_ui_schema_api(project_name: str):
    """Fetch a project's declarative submit-form schema
    (``projects/<name>/ui/submit.yaml`` in the Forge repo), if it publishes one.

    This is the generic, shared mechanism: any Forge project can add this
    file to get a fully dynamic submit form without project-specific code
    in the Control Center.
    """
    schema = await project_ui_schema.get_schema(project_name)
    return ProjectUiSchemaResponse(
        found=schema is not None, project=project_name, ui_schema=schema
    )


@router.post("/projects/{project_name}/ui-schema/refresh")
async def project_ui_schema_refresh(project_name: str, _=Depends(require_auth)):
    """Refresh GitHub caches through the shared deployment-wide cooldown."""
    try:
        await github_sync_service.refresh_now()
    except github_sync_service.GithubSyncError as exc:
        raise HTTPException(
            status_code=502,
            detail="GitHub sync failed: {}".format("; ".join(exc.errors)),
        )
    schema = await project_ui_schema.get_schema(project_name)
    return {"status": "ok", "found": schema is not None}


@router.get("/pipelines")
async def list_pipelines():
    return settings.FOURNOS_DEFAULT_PIPELINES.split(",")


# ─── routes: GitHub PRs ─────────────────────────────────────────────────
#
# Cached server-side (like Forge project discovery) so the open-PR list is
# fetched from GitHub once and reused for every user/poll, rather than on
# every request — GitHub's unauthenticated rate limit (60/hr per source IP)
# is shared across the whole deployment, not per-user.

_open_prs_cache: Optional[list] = None
_open_prs_inflight: Optional["asyncio.Future[list]"] = None
_releases_cache: Optional[list] = None
_releases_inflight: Optional["asyncio.Future[list]"] = None


def _fetch_github_open_prs_sync() -> list:
    url = (
        "https://api.github.com/repos/{}/pulls"
        "?state=open&per_page=100"
    ).format(settings.FORGE_GITHUB_REPO)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "psap-control-center",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = "Bearer {}".format(settings.GITHUB_TOKEN)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        prs = json.loads(resp.read())

    return [
        {
            "number": pr["number"],
            "title": pr["title"],
            "author": pr["user"]["login"],
            "head_sha": pr["head"]["sha"],
            "branch": pr["head"]["ref"],
            "repository": settings.FORGE_GITHUB_REPO,
            "url": pr.get("html_url") or "https://github.com/{}/pull/{}".format(
                settings.FORGE_GITHUB_REPO, pr["number"]
            ),
            "draft": pr["draft"],
        }
        for pr in prs
    ]


async def refresh_open_prs() -> list:
    """Public entry point for github_sync_service's periodic/manual refresh
    — same coalesced fetch the GET/refresh routes below use.
    """
    return await _fetch_open_prs_coalesced()


async def _fetch_open_prs_coalesced() -> list:
    """Fetch open PRs from GitHub, coalescing concurrent callers into a
    single outbound request (protects against a stampede on cold cache /
    simultaneous forced refreshes) and caching the result on success.
    """
    global _open_prs_cache, _open_prs_inflight

    if _open_prs_inflight is not None:
        return await _open_prs_inflight

    future: "asyncio.Future[list]" = asyncio.ensure_future(
        asyncio.to_thread(_fetch_github_open_prs_sync)
    )
    _open_prs_inflight = future
    try:
        prs = await future
    finally:
        _open_prs_inflight = None

    _open_prs_cache = prs
    return prs


@router.get("/github/open-prs", response_model=List[GitHubPR])
async def github_open_prs():
    """Return the cached open-PR list, fetching it from GitHub on first use.
    Never refetches once cached — call POST .../open-prs/refresh to force that.
    """
    if _open_prs_cache is not None:
        return _open_prs_cache

    try:
        return await _fetch_open_prs_coalesced()
    except Exception as exc:
        logger.error("Failed to fetch open PRs from GitHub: %s", exc)
        raise HTTPException(502, "GitHub API error: {}".format(exc))


@router.post("/github/open-prs/refresh", response_model=List[GitHubPR])
async def github_open_prs_refresh(_=Depends(require_auth)):
    """Force-refresh the open-PR list from GitHub. Auth-gated since, unlike
    the cached GET above, every call here is a real outbound GitHub request
    and could otherwise be used to exhaust the deployment-wide rate limit.
    On failure this surfaces a 502 rather than silently serving stale data,
    so the UI's Refresh action gives honest feedback.
    """
    try:
        await github_sync_service.refresh_now()
        return _open_prs_cache or []
    except github_sync_service.GithubSyncError as exc:
        logger.error("Failed to refresh GitHub caches: %s", exc)
        raise HTTPException(502, "GitHub sync failed: {}".format(exc))
    except Exception as exc:
        logger.error("Failed to refresh open PRs from GitHub: %s", exc)
        raise HTTPException(502, "GitHub API error: {}".format(exc))


def _fetch_github_releases_sync() -> list:
    url = "https://api.github.com/repos/{}/releases?per_page=100".format(
        settings.FORGE_GITHUB_REPO
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "psap-control-center",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = "Bearer {}".format(settings.GITHUB_TOKEN)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        releases = json.loads(resp.read())
    return [
        {
            "tag_name": release["tag_name"],
            "name": release.get("name") or release["tag_name"],
            "prerelease": bool(release.get("prerelease")),
            "published_at": release.get("published_at"),
            "html_url": release.get("html_url", ""),
        }
        for release in releases
        if isinstance(release, dict) and release.get("tag_name")
    ]


async def _fetch_releases_coalesced() -> list:
    global _releases_cache, _releases_inflight
    if _releases_cache is not None:
        return _releases_cache
    if _releases_inflight is not None:
        return await _releases_inflight
    future: "asyncio.Future[list]" = asyncio.ensure_future(
        asyncio.to_thread(_fetch_github_releases_sync)
    )
    _releases_inflight = future
    try:
        _releases_cache = await future
        return _releases_cache
    finally:
        _releases_inflight = None


@router.get("/github/releases", response_model=List[GitHubRelease])
async def github_releases():
    """Return published Forge release tags for the build-source picker."""
    try:
        return await _fetch_releases_coalesced()
    except Exception as exc:
        logger.error("Failed to fetch GitHub releases: %s", exc)
        raise HTTPException(502, "GitHub API error: {}".format(exc))


# ─── routes: GitHub sync status/refresh ─────────────────────────────────
#
# Manual counterpart to github_sync_service's configurable periodic refresh
# refresh — lets a user force an immediate update (e.g. right after
# merging a new preset) instead of waiting out the interval, subject to
# the same server-side cooldown (so it can't be spammed into exhausting
# GitHub's rate limit). Goes through the exact same coalesced
# refresh_now(), so simultaneous button presses from multiple people
# collapse into a single round of GitHub calls, and a failed refresh
# comes back as a real error (502) instead of a false "ok".

@router.get("/github/sync-status", response_model=GithubSyncStatusResponse)
async def github_sync_status():
    status = github_sync_service.get_status()
    last_synced = status.get("last_synced_at")
    return {
        "in_progress": status.get("in_progress", False),
        "last_synced_at": last_synced.isoformat() if last_synced else None,
        "last_error": status.get("last_error"),
        "project_count": status.get("project_count", 0),
    }


@router.post("/github/sync", response_model=GithubSyncStatusResponse)
async def github_sync_refresh(_=Depends(require_auth)):
    try:
        status = await github_sync_service.refresh_now()
    except github_sync_service.GithubSyncError as exc:
        # Surface this as a real failure (not a 200) so the frontend
        # mutation's onError — not onSuccess — fires, and the UI doesn't
        # claim data was just synced when it wasn't.
        raise HTTPException(
            status_code=502,
            detail="GitHub sync failed: {}".format("; ".join(exc.errors)),
        )
    last_synced = status.get("last_synced_at")
    return {
        "in_progress": status.get("in_progress", False),
        "last_synced_at": last_synced.isoformat() if last_synced else None,
        "last_error": status.get("last_error"),
        "project_count": status.get("project_count", 0),
    }


# ─── routes: recurring jobs ──────────────────────────────────────────────
#
# Native Fournos recurring jobs — a FournosJob with spec.schedule set, kept
# in "Recurring" phase by the operator as a template for child jobs (see
# fournos/fournos/handlers/lifecycle.py). No separate CRD, no Control
# Center-managed CronJobs — this reads/writes real FournosJob objects only.

def _recurring_job_to_response(job: dict, *, include_owner: bool = True) -> dict:
    meta = job.get("metadata", {})
    spec = job.get("spec", {})
    status = job.get("status", {})
    forge = spec.get("executionEngine", {}).get("forge", {})
    return {
        "name": meta.get("name", ""),
        "project": forge.get("project", ""),
        "cluster": spec.get("cluster", ""),
        "pipeline": spec.get("pipeline", ""),
        "preset": " ".join(forge.get("args", [])),
        "owner": spec.get("owner", "") if include_owner else "",
        "schedule": spec.get("schedule", ""),
        "phase": status.get("phase", ""),
        "message": status.get("message", ""),
        "last_scheduled_time": status.get("lastScheduledTime"),
        "created_at": meta.get("creationTimestamp", ""),
    }


@router.get("/recurring-jobs", response_model=List[RecurringJobResponse])
async def list_recurring_jobs(request: Request, cluster: str = Query("")):
    include_owner = get_current_user(request) is not None
    jobs = await asyncio.to_thread(k8s.list_recurring_jobs)
    if cluster:
        jobs = [j for j in jobs if j.get("spec", {}).get("cluster") == cluster]
    return _sort_latest(
        [
            _recurring_job_to_response(j, include_owner=include_owner)
            for j in jobs
        ],
        "last_scheduled_time",
        "created_at",
    )


@router.get(
    "/recurring-jobs/{name}/children",
    response_model=List[ScheduleChildJobResponse],
)
async def recurring_job_children(name: str):
    """Child jobs spawned by this recurring template, newest first.

    Sourced from the Control Center's own DB (populated by the watcher from
    the fournos.dev/recurring-parent label) — this is also what backs the
    "Triggered By" link on the History tab and the click-through from the
    Schedules tab.
    """
    async with AsyncSessionLocal() as session:
        jobs = await db_svc.list_jobs_by_schedule(session, name)
    return [
        {
            "name": j.name,
            "status": j.status,
            "trigger_type": j.trigger_type or "recurring",
            "duration_seconds": j.duration_seconds,
            "mlflow_url": j.mlflow_url or "",
            "created_at": j.created_at.isoformat() if j.created_at else "",
        }
        for j in jobs
    ]


@router.post("/recurring-jobs/{name}/trigger")
async def trigger_recurring_job(name: str, _=Depends(require_admin)):
    """Force an immediate off-cycle child run — annotates the parent job
    with fournos.dev/trigger-now=true, exactly what `kubectl annotate` would
    do; the operator picks it up on its next ~5s reconcile tick.
    """
    try:
        await asyncio.to_thread(k8s.trigger_recurring_now, name)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.delete("/recurring-jobs/{name}")
async def delete_recurring_job(name: str, _=Depends(require_admin)):
    try:
        await asyncio.to_thread(k8s.delete_fournos_job, name)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ─── routes: cluster locks ───────────────────────────────────────────────
#
# Also not a separate resource — a FournosJob with spec.lockOnly (or a bare
# spec.lockUntil) holds the cluster's full Kueue quota without running a
# pipeline (see fournos/fournos/handlers/execution.py::is_lock_only).

def _cluster_lock_to_response(job: dict, *, include_owner: bool = True) -> dict:
    meta = job.get("metadata", {})
    spec = job.get("spec", {})
    status = job.get("status", {})
    return {
        "name": meta.get("name", ""),
        "cluster": spec.get("cluster", ""),
        "owner": spec.get("owner", "") if include_owner else "",
        "reason": spec.get("displayName", ""),
        "phase": status.get("phase", ""),
        "lock_until": spec.get("lockUntil"),
        "scheduled_start_time": spec.get("scheduledStartTime"),
        "created_at": meta.get("creationTimestamp", ""),
    }


@router.get("/cluster-locks", response_model=List[ClusterLockResponse])
async def list_cluster_locks(request: Request, cluster: str = Query("")):
    include_owner = get_current_user(request) is not None
    locks = await asyncio.to_thread(k8s.list_cluster_locks, None, cluster or None)
    return _sort_latest(
        [
            _cluster_lock_to_response(j, include_owner=include_owner)
            for j in locks
        ],
        "scheduled_start_time",
        "created_at",
    )


@router.post("/cluster-locks", response_model=ClusterLockResponse)
async def create_cluster_lock(
    req: CreateClusterLockRequest, user=Depends(require_auth)
):
    spec: dict[str, Any] = {
        "cluster": req.cluster,
        "displayName": req.reason or "Cluster lock",
        "owner": _verified_owner(user),
        "exclusive": True,
        "lockOnly": True,
    }
    if req.lock_until:
        spec["lockUntil"] = req.lock_until
    if req.scheduled_start_time:
        spec["scheduledStartTime"] = req.scheduled_start_time

    job_name = k8s.sanitize_job_name("lock-{}".format(req.cluster))
    body = {
        "apiVersion": "{}/{}".format(
            settings.FOURNOS_API_GROUP, settings.FOURNOS_API_VERSION
        ),
        "kind": "FournosJob",
        "metadata": {
            "name": job_name,
            "namespace": settings.FOURNOS_NAMESPACE,
            "annotations": requester_annotations(user),
        },
        "spec": spec,
    }
    try:
        created = await asyncio.to_thread(k8s.create_fournos_job, body)
    except Exception as exc:
        raise HTTPException(500, "Failed to create cluster lock: {}".format(exc))
    return _cluster_lock_to_response(created)


@router.delete("/cluster-locks/{name}")
async def delete_cluster_lock(name: str, _=Depends(require_admin)):
    try:
        await asyncio.to_thread(k8s.delete_fournos_job, name)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ─── routes: per-cluster overview ────────────────────────────────────────

@router.get("/clusters/{cluster}/overview", response_model=ClusterOverviewResponse)
async def cluster_overview(cluster: str, request: Request):
    """Backs the "Defer / Recurring / Lock cluster" popup on the Submit
    page: what's running on this cluster now, what recurs on it, and what
    locks (active or scheduled) it has — all live from the fournos
    namespace, so it's always consistent with the Live Jobs and Schedules
    tabs.
    """
    all_jobs = await _get_live_jobs()
    current = [
        j for j in all_jobs if j.get("spec", {}).get("cluster") == cluster
    ]
    recurring = await asyncio.to_thread(k8s.list_recurring_jobs)
    recurring = [j for j in recurring if j.get("spec", {}).get("cluster") == cluster]
    locks = await asyncio.to_thread(k8s.list_cluster_locks, None, cluster)
    include_owner = get_current_user(request) is not None

    return {
        "cluster": cluster,
        "current_jobs": [
            _live_job_to_summary(j, include_owner=include_owner) for j in current
        ],
        "recurring_jobs": _sort_latest(
            [
                _recurring_job_to_response(j, include_owner=include_owner)
                for j in recurring
            ],
            "last_scheduled_time",
            "created_at",
        ),
        "locks": _sort_latest(
            [
                _cluster_lock_to_response(j, include_owner=include_owner)
                for j in locks
            ],
            "scheduled_start_time",
            "created_at",
        ),
    }


# ─── routes: calendar slot holds ─────────────────────────────────────────
#
# Ephemeral "someone else is booking this slot right now" markers for the
# scheduling calendar — see slot_hold_service.py. Not a Fournos concept, so
# there's nothing to reconcile with the live cluster state here.

def _slot_hold_to_response(hold) -> dict:
    return {
        "cluster": hold.cluster,
        "start_time": hold.start_time,
        "held_by": hold.held_by,
        "expires_at": hold.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.get("/clusters/{cluster}/slot-holds", response_model=List[SlotHoldResponse])
async def list_slot_holds(cluster: str):
    return [_slot_hold_to_response(h) for h in slot_holds.list_holds(cluster)]


@router.post("/clusters/{cluster}/slot-holds", response_model=SlotHoldResponse)
async def create_slot_hold(cluster: str, req: HoldSlotRequest, user=Depends(require_auth)):
    """Claim (or refresh, if you already hold it) a calendar slot. 409s if
    someone else is actively booking the same slot right now.
    """
    try:
        hold = slot_holds.hold_slot(cluster, req.start_time, user["username"])
    except slot_holds.SlotAlreadyHeldError as exc:
        raise HTTPException(
            409,
            f"This time slot is currently being booked by {exc.hold.held_by}. "
            "Try another slot or wait a moment.",
        )
    return _slot_hold_to_response(hold)


@router.delete("/clusters/{cluster}/slot-holds")
async def release_slot_hold(
    cluster: str, start_time: str = Query(...), user=Depends(require_auth)
):
    slot_holds.release_slot(
        cluster, start_time, user["username"], force=user["role"] == "admin"
    )
    return {"status": "ok"}
