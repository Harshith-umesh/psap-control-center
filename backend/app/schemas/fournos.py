"""Pydantic schemas for the Fournos Testing Tab API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# -- Job schemas --

class FournosJobSummary(BaseModel):
    name: str
    project: str = ""
    preset: str = ""
    cluster: str = ""
    pipeline: str = ""
    owner: str = ""
    status: str = "Pending"
    message: str = ""
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    mlflow_url: str = ""
    trigger_type: str = "manual"
    triggered_by_schedule: Optional[str] = None
    # When set (deferred one-off jobs only), the actual planned start —
    # distinct from created_at, which is just when the CR object was
    # created. Powers the cluster calendar's day view.
    scheduled_start_time: Optional[str] = None
    source: str = "live"
    source_repository: str = ""
    source_pr_number: Optional[int] = None
    source_pr_url: str = ""
    source_head_branch: str = ""
    source_requested_sha: str = ""
    source_resolved_sha: str = ""
    forge_git_version: str = ""
    forge_image_digest: str = ""
    forge_provenance_state: str = "pending"
    work_items: List["WorkItemReference"] = Field(default_factory=list)
    run_groups: List["RunGroupReference"] = Field(default_factory=list)

    class Config:
        from_attributes = True


class FournosJobDetail(BaseModel):
    metadata: Dict[str, Any] = Field(default_factory=dict)
    spec: Dict[str, Any] = Field(default_factory=dict)
    status: Dict[str, Any] = Field(default_factory=dict)
    source: str = "live"
    duration_seconds: Optional[float] = None
    mlflow_url: str = ""
    ci_artifacts_url: str = ""


class PipelineStage(BaseModel):
    name: str
    displayName: str = ""
    status: str = "Pending"
    startTime: Optional[str] = None
    completionTime: Optional[str] = None
    is_finally: bool = Field(False, alias="finally")
    outcome: str = ""
    reason: str = ""
    reasonCode: str = ""
    reasonSource: str = ""
    failedStep: str = ""
    exitCode: Optional[int] = None

    class Config:
        populate_by_name = True


class TaskProgress(BaseModel):
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    incomplete: int = 0
    skipped: int = 0
    total: int = 0


class FournosPod(BaseModel):
    name: str
    phase: str = "Unknown"
    container: str = "unknown"
    ready: bool = False
    restarts: int = 0
    age_minutes: int = 0
    exit_code: Optional[int] = None
    term_reason: str = ""
    term_message: str = ""


class CurrentStep(BaseModel):
    name: str
    displayName: str = ""
    startTime: Optional[str] = None


class FailureSummary(BaseModel):
    outcome: Literal["failed", "cancelled", "infrastructure_error", "unknown"]
    stage: str = ""
    stageDisplayName: str = ""
    step: str = ""
    reason: str = ""
    reasonCode: str = ""
    source: str = "unknown"
    artifactPath: str = ""
    executionReason: str = ""
    executionSource: str = ""


class ForgeInfo(BaseModel):
    project: str = ""
    args: List[str] = Field(default_factory=list)
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    pr_number: str = ""
    pr_title: str = ""
    pr_url: str = ""
    repository: str = ""
    head_branch: str = ""
    requested_sha: str = ""
    resolved_sha: str = ""


class ForgeExecutionImage(BaseModel):
    image: str = ""
    image_id: str = Field("", alias="imageID")
    container: str = "step-forge"

    class Config:
        populate_by_name = True


class ForgeGitVersion(BaseModel):
    version: str
    artifact_path: str = Field("", alias="artifactPath")

    class Config:
        populate_by_name = True


class ForgeExecutionProvenance(BaseModel):
    images: List[ForgeExecutionImage] = Field(default_factory=list)
    git_versions: List[ForgeGitVersion] = Field(
        default_factory=list, alias="gitVersions"
    )
    observed_at: Optional[str] = Field(None, alias="observedAt")

    class Config:
        populate_by_name = True


class FournosJobDetailResponse(BaseModel):
    job: FournosJobDetail
    pods: List[FournosPod] = Field(default_factory=list)
    stages: List[PipelineStage] = Field(default_factory=list)
    current_step: Optional[CurrentStep] = None
    forge_info: ForgeInfo = Field(default_factory=ForgeInfo)
    task_progress: Optional[TaskProgress] = None
    failure_summary: Optional[FailureSummary] = None
    failure_enrichment_state: str = "not_applicable"
    forge_execution: ForgeExecutionProvenance = Field(
        default_factory=ForgeExecutionProvenance
    )
    forge_provenance_state: str = "not_applicable"
    work_items: List["WorkItemReference"] = Field(default_factory=list)
    can_edit_work_items: bool = False
    run_groups: List["RunGroupReference"] = Field(default_factory=list)
    can_edit_run_groups: bool = False

    @field_validator("failure_summary", mode="before")
    @classmethod
    def empty_failure_summary_is_absent(cls, value):
        """Successful jobs persist `{}`; expose that as no failure."""
        return None if value == {} else value


# -- Job event schemas --

class JobEventResponse(BaseModel):
    id: str
    phase: str
    message: str = ""
    timestamp: Optional[datetime] = None

    class Config:
        from_attributes = True


# -- Job list response --

class JobListResponse(BaseModel):
    jobs: List[FournosJobSummary]
    total: int
    page: int
    per_page: int


class HistoryViewState(BaseModel):
    """Validated, versioned state for one user's latest History view."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field("", max_length=200)
    project: str = Field("", max_length=255)
    cluster: str = Field("", max_length=255)
    status: str = Field("", max_length=50)
    requester_scope: Literal["all", "mine"] = "all"
    identity: str = Field("", max_length=255)
    failure_outcome: str = Field("", max_length=50)
    repository: str = Field("", max_length=255)
    pr_number: Optional[int] = Field(None, ge=1, le=2147483647)
    source_sha: str = Field(
        "", max_length=64, pattern=r"^$|^[0-9a-fA-F]{4,64}$"
    )
    forge: str = Field("", max_length=255)
    work_item_provider: str = Field("", max_length=50)
    work_item_key: str = Field("", max_length=100)
    tags: List[str] = Field(default_factory=list, max_length=20)
    history_date: str = Field("", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    from_time: str = Field("00:00", pattern=r"^\d{2}:\d{2}$")
    to_time: str = Field("23:59", pattern=r"^\d{2}:\d{2}$")
    sort_by: Literal[
        "name", "project", "cluster", "status", "owner", "date",
        "duration", "triggered_by",
    ] = "date"
    sort_dir: Literal["asc", "desc"] = "desc"
    per_page: Literal[25, 50, 100, 200] = 50

    @field_validator(
        "query", "project", "cluster", "status", "identity",
        "failure_outcome", "repository", "source_sha", "forge",
        "work_item_provider", "work_item_key",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("work_item_provider")
    @classmethod
    def normalize_work_item_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("work_item_key")
    @classmethod
    def normalize_work_item_key(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"", "Succeeded", "Failed", "Stopped"}:
            raise ValueError("unsupported History status")
        return value

    @field_validator("failure_outcome")
    @classmethod
    def validate_failure_outcome(cls, value: str) -> str:
        if value not in {
            "", "failed", "cancelled", "infrastructure_error", "unknown",
        }:
            raise ValueError("unsupported failure outcome")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: List[str]) -> List[str]:
        normalized = []
        for value in values:
            tag = value.strip()
            if not tag:
                continue
            if len(tag) > 100:
                raise ValueError("tags must be at most 100 characters")
            if tag not in normalized:
                normalized.append(tag)
        return normalized

    @field_validator("history_date")
    @classmethod
    def validate_history_date(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value

    @field_validator("from_time", "to_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value


class HistoryPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: HistoryViewState


class HistoryPreferenceResponse(BaseModel):
    schema_version: int = 1
    state: HistoryViewState = Field(default_factory=HistoryViewState)
    updated_at: Optional[datetime] = None


class HistoryFilterOptionsResponse(BaseModel):
    """Known values available to History's editable filter controls."""

    identities: List[str] = Field(default_factory=list)
    repositories: List[str] = Field(default_factory=list)
    pr_numbers: List[int] = Field(default_factory=list)
    source_shas: List[str] = Field(default_factory=list)
    forge: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    work_item_providers: List[str] = Field(default_factory=list)
    work_item_keys: List[str] = Field(default_factory=list)


# -- Submit job --

class PullRequestSelection(BaseModel):
    """Immutable PR snapshot selected by the user in Control Center."""

    repository: str
    number: int = Field(..., ge=1)
    url: str
    head_branch: str
    requested_sha: str


class WorkItemReference(BaseModel):
    provider: str = "jira"
    key: str = ""
    url: str = ""


class WorkItemUpdate(BaseModel):
    work_items: List[WorkItemReference] = Field(default_factory=list, max_length=20)


class WorkItemConfigResponse(BaseModel):
    enabled: bool = False
    providers: List[str] = Field(default_factory=list)


class RunGroupReference(BaseModel):
    id: str
    group_type: Literal["experiment", "workload", "campaign", "cohort"]
    key: str
    display_name: str
    description: str = ""
    archived: bool = False


class RunGroupCreate(BaseModel):
    group_type: str
    key: str
    display_name: str
    description: str = ""


class RunGroupUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None


class RunGroupMembershipUpdate(BaseModel):
    group_ids: List[str] = Field(default_factory=list, max_length=20)

class SubmitJobRequest(BaseModel):
    project: str
    cluster: str
    pipeline: str = "forge-test-only"
    preset: str = ""
    # Generic multi-arg support for schema-driven forms (see ui_schema.py):
    # a project can declare several "arg fields" (e.g. model + deployment
    # preset + cluster config), each contributing one or more preset keys
    # here, all passed through to Forge as CLI args. Takes precedence over
    # `preset` when non-empty.
    args: List[str] = Field(default_factory=list)
    version: str = ""
    # Accepted for backward-compatible clients; the API replaces this with
    # the authenticated requester's verified display name.
    owner: str = ""
    exclusive: bool = False
    config_overrides: Dict[str, str] = Field(default_factory=dict)
    pull_request: Optional[PullRequestSelection] = None
    pull_sha: str = ""
    # RHAIIS requires either a pinned commit/release/PR SHA or an explicit
    # opt-in to the moving main branch. The backend validates this rather
    # than relying only on the browser form.
    use_latest_main: bool = False
    priority: str = "manual"
    gpu_type: str = ""
    gpu_count: int = 1
    # Scheduling — mirrors FournosJob's own spec.schedule / scheduledStartTime
    # (see fournos/manifests/crd.yaml) exactly; the Control Center is just a
    # UTC-converting UI on top, mutually exclusive same as the CRD itself.
    scheduled_start_time: Optional[str] = None
    schedule: str = ""
    work_items: List[WorkItemReference] = Field(default_factory=list, max_length=20)
    run_group_ids: List[str] = Field(default_factory=list, max_length=20)


class SubmitJobResponse(BaseModel):
    status: str = "ok"
    job_name: str
    redirect: str = ""


# -- Matrix (pipeline/CPT-style) submission --
#
# Generic multi-job submission for a schema-driven "matrix" mode (see
# app/schemas/ui_schema.py): one job per selected model, matrixed against
# the selected workloads. Not project-specific — any project whose
# ui/submit.yaml declares a `kind: matrix` mode can use this.

class SubmitMatrixModelInput(BaseModel):
    key: str
    overrides: Dict[str, Any] = Field(default_factory=dict)
    gpu_count: Optional[int] = None


class SubmitMatrixRequest(BaseModel):
    project: str
    cluster: str
    pipeline: str = "forge-full"
    # Shared args/overrides applied to every generated job (e.g. the
    # mode's own accelerator/engine/cluster field selections).
    args: List[str] = Field(default_factory=list)
    config_overrides: Dict[str, str] = Field(default_factory=dict)
    models: List[SubmitMatrixModelInput]
    workloads: List[str]
    # Accepted for backward-compatible clients; ignored by the API.
    owner: str = "fournos-dashboard"
    priority: str = "manual"
    exclusive: bool = False
    pull_request: Optional[PullRequestSelection] = None
    pull_sha: str = ""
    use_latest_main: bool = False
    gpu_type: str = ""
    scheduled_start_time: Optional[str] = None
    schedule: str = ""
    work_items: List[WorkItemReference] = Field(default_factory=list, max_length=20)
    run_group_ids: List[str] = Field(default_factory=list, max_length=20)


class SubmitMatrixResultItem(BaseModel):
    model: str
    job_name: Optional[str] = None
    status: str
    error: Optional[str] = None


class SubmitMatrixResponse(BaseModel):
    status: str = "ok"
    jobs: List[SubmitMatrixResultItem]
    total: int


# -- Recurring jobs --
#
# A "recurring job" is not a separate resource — it's a FournosJob whose
# spec.schedule is set, which the operator keeps in "Recurring" phase and
# uses as a template to stamp out child FournosJobs on each cron tick (see
# fournos/fournos/handlers/lifecycle.py). Children carry the
# fournos.dev/recurring-parent label pointing back at this job's name.

class RecurringJobResponse(BaseModel):
    name: str
    project: str = ""
    cluster: str = ""
    pipeline: str = ""
    preset: str = ""
    owner: str = ""
    schedule: str = ""  # cron expression, UTC
    phase: str = ""
    message: str = ""
    last_scheduled_time: Optional[str] = None
    created_at: str = ""


class ScheduleChildJobResponse(BaseModel):
    name: str
    status: str = "Pending"
    trigger_type: str = "recurring"
    duration_seconds: Optional[float] = None
    mlflow_url: str = ""
    created_at: str = ""


# -- Cluster locks --
#
# Also not a separate resource — a FournosJob with spec.lockOnly (or a bare
# spec.lockUntil) is a sentinel that holds the cluster's exclusive Kueue
# quota without running a pipeline (see fournos/fournos/handlers/
# execution.py::is_lock_only). Can be scheduled to start later via the same
# spec.scheduledStartTime every other FournosJob supports.

class ClusterLockResponse(BaseModel):
    name: str
    cluster: str = ""
    owner: str = ""
    reason: str = ""
    phase: str = ""
    lock_until: Optional[str] = None
    scheduled_start_time: Optional[str] = None
    created_at: str = ""


class CreateClusterLockRequest(BaseModel):
    cluster: str
    # Accepted for backward-compatible clients; ignored by the API.
    owner: str = ""
    reason: str = ""
    lock_until: Optional[str] = None  # ISO 8601 UTC; omit = held indefinitely
    scheduled_start_time: Optional[str] = None  # ISO 8601 UTC; omit = now


class ClusterOverviewResponse(BaseModel):
    """Combined per-cluster view backing the "Defer / Recurring / Lock"
    popup on the Submit page: what's running now, what recurs, and what's
    locked (now or scheduled for later) — all sourced live from the fournos
    namespace, the same data the Schedules tab and Live Jobs tab use.
    """

    cluster: str
    current_jobs: List[FournosJobSummary] = Field(default_factory=list)
    recurring_jobs: List[RecurringJobResponse] = Field(default_factory=list)
    locks: List[ClusterLockResponse] = Field(default_factory=list)


# -- Calendar slot holds --
#
# Purely a Control Center UX nicety, not a Fournos concept: a short-lived,
# in-memory claim on a (cluster, start_time) slot in the scheduling
# calendar so two users can't both be mid-way through booking the exact
# same slot at once. Fournos itself is happy to queue multiple jobs
# targeting the same cluster/time regardless — see slot_hold_service.py.

class SlotHoldResponse(BaseModel):
    cluster: str
    start_time: str  # ISO 8601 UTC, truncated to the slot granularity
    held_by: str
    expires_at: str


class HoldSlotRequest(BaseModel):
    start_time: str  # ISO 8601 UTC


# -- Project info --

class ProjectInfoResponse(BaseModel):
    name: str
    cluster: str = ""
    presets: List[str] = Field(default_factory=list)
    config_keys: List[str] = Field(default_factory=list)
    has_cli: bool = False


# -- GitHub PR --

class GitHubPR(BaseModel):
    number: int
    title: str
    author: str
    head_sha: str
    branch: str
    repository: str
    url: str
    draft: bool = False


class GitHubRelease(BaseModel):
    tag_name: str
    name: str
    prerelease: bool = False
    published_at: Optional[str] = None
    html_url: str = ""


# -- GitHub sync status --
#
# Everything sourced from the Forge GitHub repo (project discovery,
# ui/submit.yaml schemas, pipeline definitions, open PRs) is refreshed on
# one shared schedule/lock (see github_sync_service.py) instead of on every
# page load, to stay well under GitHub's unauthenticated 60 req/hr limit.

class GithubSyncStatusResponse(BaseModel):
    in_progress: bool = False
    last_synced_at: Optional[str] = None
    last_error: Optional[str] = None
    project_count: int = 0
