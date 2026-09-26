"""Background K8s watcher that archives FournosJobs to the PSAP database."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from dateutil.parser import parse as parse_dt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.auth import (
    REQUESTER_EMAIL_ANNOTATION,
    REQUESTER_NAME_ANNOTATION,
    REQUESTER_PROVIDER_ANNOTATION,
    REQUESTER_SUBJECT_ANNOTATION,
)
from app.services import fournos_k8s_client as k8s_client
from app.services import fournos_db_service as db_svc
from app.services import pipeline_definitions
from app.services.failure_details import (
    first_actionable_failure,
    merge_caliper_failure,
)
from app.services.mlflow_failure_provider import fetch_caliper_completion
from app.services.forge_provenance import fetch_forge_versions
from app.services.source_provenance import extract_source_request_fields
from app.models.fournos_job import FournosJob

logger = logging.getLogger(__name__)

_watcher_engine = None
_watcher_session: Optional[async_sessionmaker] = None

SYNC_INTERVAL_SECONDS = 60
TERMINAL_PHASES = {"Succeeded", "Failed", "Stopped"}
STAGE_SNAPSHOT_MAX_ATTEMPTS = 3
STAGE_SNAPSHOT_RETRY_SECONDS = 60
FAILURE_ENRICHMENT_MAX_ATTEMPTS = 5
FAILURE_ENRICHMENT_RETRY_SECONDS = 60
FORGE_PROVENANCE_MAX_ATTEMPTS = 5
FORGE_PROVENANCE_RETRY_SECONDS = 60
FINAL_ENRICHMENT_STATES = {
    "complete",
    "disabled",
    "exhausted",
}


def _init_watcher_db(loop: asyncio.AbstractEventLoop) -> None:
    global _watcher_engine, _watcher_session
    _watcher_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=3,
        max_overflow=5,
    )
    _watcher_session = async_sessionmaker(
        _watcher_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _compute_terminal_stages(
    job_name: str, spec: dict, status: dict
) -> Optional[list]:
    """Snapshot the merged pipeline stage list for a terminal job.

    This runs on the transition and, if the K8s view is incomplete, through a
    short bounded retry window before the PipelineRun/TaskRuns are cleaned up.
    """
    try:
        pr_name = status.get("pipelineRun", "")
        pr = k8s_client.get_pipelinerun(pr_name) if pr_name else None
        if not pr:
            prs = k8s_client.list_pipelineruns_for_job(job_name)
            pr = prs[0] if prs else None
        if not pr:
            return None

        actual_stages = k8s_client.extract_pipeline_stages(
            pr, strict_lookup_errors=True
        )
        # A terminal PipelineRun should not have active/unknown TaskRuns. An
        # empty or Pending/Running result means the K8s snapshot was incomplete
        # (usually a transient lookup failure), so leave the DB value untouched
        # and let the bounded retry path try again.
        if not actual_stages or any(
            stage.get("status") in ("Pending", "Running")
            for stage in actual_stages
        ):
            return None

        pipeline_name = spec.get("pipeline", "")
        if pipeline_name:
            pipeline_def = pipeline_definitions.get_definition_sync(pipeline_name)
            actual_stages = pipeline_definitions.merge_pipeline_stages(
                pipeline_def, actual_stages
            )

        # Tasks with no TaskRun after the PipelineRun has terminated are no
        # longer queued. Keep that distinct from Tekton's explicit `Skipped`
        # status while ensuring History never presents terminal work as active.
        return [
            {
                **stage,
                "status": "NotRun",
                "outcome": "not_run",
                "reason": "Stage did not start before the pipeline terminated.",
                "reasonCode": "NOT_RUN",
                "reasonSource": "pipeline_definition",
                "failedStep": "",
                "exitCode": None,
            }
            if stage.get("status") == "Pending"
            else stage
            for stage in actual_stages
        ]
    except k8s_client.TaskRunLookupError:
        raise
    except Exception as exc:
        logger.warning(
            "Could not snapshot pipeline stages for %s: %s", job_name, exc
        )
        return None


def _has_usable_stage_snapshot(stages: Optional[list]) -> bool:
    return bool(stages) and all(
        isinstance(stage, dict)
        and stage.get("status") not in (None, "Pending", "Running")
        for stage in stages
    )


def _stage_snapshot_retry_due(
    attempts: int,
    attempted_at: Optional[datetime],
    now: datetime,
) -> bool:
    if attempts >= STAGE_SNAPSHOT_MAX_ATTEMPTS:
        return False
    if attempted_at is None:
        return True
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=timezone.utc)
    return (now - attempted_at).total_seconds() >= STAGE_SNAPSHOT_RETRY_SECONDS


def _failure_enrichment_retry_due(
    attempts: int,
    attempted_at: Optional[datetime],
    now: datetime,
) -> bool:
    if attempts >= FAILURE_ENRICHMENT_MAX_ATTEMPTS:
        return False
    if attempted_at is None:
        return True
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=timezone.utc)
    return (
        now - attempted_at
    ).total_seconds() >= FAILURE_ENRICHMENT_RETRY_SECONDS


def _forge_provenance_retry_due(
    attempts: int,
    attempted_at: Optional[datetime],
    now: datetime,
) -> bool:
    if attempts >= FORGE_PROVENANCE_MAX_ATTEMPTS:
        return False
    if attempted_at is None:
        return True
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=timezone.utc)
    return (
        now - attempted_at
    ).total_seconds() >= FORGE_PROVENANCE_RETRY_SECONDS


def _merge_forge_execution(
    existing: Optional[dict],
    *,
    images: Optional[list[dict]] = None,
    git_versions: Optional[list[dict]] = None,
    observed_at: Optional[datetime] = None,
) -> dict:
    merged = dict(existing or {})
    current_images = merged.get("images", []) or []
    current_versions = merged.get("gitVersions", []) or []
    image_map = {
        (item.get("image", ""), item.get("imageID", ""), item.get("container", "")): item
        for item in [*current_images, *(images or [])]
        if isinstance(item, dict)
    }
    version_map = {
        (item.get("version", ""), item.get("artifactPath", "")): item
        for item in [*current_versions, *(git_versions or [])]
        if isinstance(item, dict) and item.get("version")
    }
    merged["images"] = [image_map[key] for key in sorted(image_map)]
    merged["gitVersions"] = [version_map[key] for key in sorted(version_map)]
    if observed_at and (images or git_versions):
        merged["observedAt"] = observed_at.isoformat()
    return merged


def _forge_provenance_state(
    execution: dict,
    *,
    terminal: bool,
    mlflow_url: str,
    fetch_state: str,
    attempts: int,
) -> str:
    has_images = bool(execution.get("images"))
    has_versions = bool(execution.get("gitVersions"))
    if has_images and has_versions:
        return "complete"
    if not terminal:
        return "pending"
    if not mlflow_url:
        return "partial" if has_images else "unavailable"
    if has_images or has_versions:
        return "partial"
    if fetch_state == "disabled":
        return "disabled"
    if attempts >= FORGE_PROVENANCE_MAX_ATTEMPTS:
        return "exhausted"
    return "pending"


def _should_fetch_forge_provenance(
    *,
    mlflow_url: str,
    state: str,
    retry_due: bool,
    transitioning_into_terminal: bool,
    attempts: int,
) -> bool:
    if not mlflow_url or state in {"complete", "disabled", "exhausted"}:
        return False
    if not retry_due:
        return False
    # As with failure enrichment, additive migration defaults must not trigger
    # an unbounded historical MLflow backfill on the first deployment.
    return (
        transitioning_into_terminal
        or attempts > 0
        or state in {"unavailable", "partial"}
    )


def _should_enrich_failure(
    *,
    mlflow_url: str,
    enrichment_state: str,
    enrichment_due: bool,
    transitioning_into_terminal: bool,
    enrichment_attempts: int,
) -> bool:
    if not mlflow_url or enrichment_state in FINAL_ENRICHMENT_STATES:
        return False
    if not enrichment_due:
        return False
    # Existing rows receive `pending` from the additive migration. Do not turn
    # the first post-deploy full sync into a credentialed MLflow backfill. New
    # terminal transitions and jobs whose URL arrived late are enriched.
    return (
        transitioning_into_terminal
        or enrichment_attempts > 0
        or enrichment_state == "unavailable"
    )


def _extract_forge_fields(job: dict) -> dict:
    meta = job.get("metadata", {})
    spec = job.get("spec", {})
    forge = spec.get("executionEngine", {}).get("forge", {})
    status = job.get("status", {})

    mlflow_info = (
        status.get("engineStatus", {})
        .get("forge", {})
        .get("exportArtifacts", {})
        .get("caliper_artifacts_export", {})
        .get("backends", {})
        .get("mlflow", {})
    )
    mlflow_url = mlflow_info.get("run_url", "") if mlflow_info else ""

    args = forge.get("args", [])
    preset = " ".join(args) if args else ""

    created_str = meta.get("creationTimestamp", "")
    created_at = None
    if created_str:
        try:
            created_at = parse_dt(created_str)
        except Exception:
            pass

    completed_at = None
    duration_seconds = None
    conditions = status.get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "PipelineRunReady" and cond.get("status") in (
            "True",
            "False",
        ):
            try:
                completed_at = parse_dt(cond["lastTransitionTime"])
            except Exception:
                pass

    if created_at and completed_at:
        duration_seconds = (completed_at - created_at).total_seconds()

    labels = meta.get("labels", {})
    annotations = meta.get("annotations", {})
    # Native Fournos recurring-job label (fournos.dev/recurring-parent) —
    # set by the operator itself on every child it stamps out from a
    # recurring template (see fournos/fournos/handlers/lifecycle.py). A job
    # with no parent but its own spec.schedule *is* the recurring template;
    # one with spec.scheduledStartTime is a one-off deferred job.
    schedule_name = labels.get(k8s_client.LABEL_RECURRING_PARENT, "")
    if schedule_name:
        trigger_type = "recurring"
    elif spec.get("scheduledStartTime"):
        trigger_type = "deferred"
    elif spec.get("schedule"):
        trigger_type = "recurring-parent"
    else:
        trigger_type = "manual"

    job_name = meta.get("name", "")
    phase = status.get("phase", "Unknown")

    return {
        "name": job_name,
        "project": forge.get("project", ""),
        "preset": preset,
        "cluster": spec.get("cluster", ""),
        "pipeline": spec.get("pipeline", ""),
        "owner": spec.get("owner", ""),
        "requester_subject": annotations.get(REQUESTER_SUBJECT_ANNOTATION, ""),
        "requester_email": annotations.get(REQUESTER_EMAIL_ANNOTATION, ""),
        "requester_name": annotations.get(REQUESTER_NAME_ANNOTATION, ""),
        "auth_provider": annotations.get(REQUESTER_PROVIDER_ANNOTATION, ""),
        **extract_source_request_fields(spec),
        "status": phase,
        "message": status.get("message", ""),
        "created_at": created_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
        # Deliberately *not* set here — see _archive_job, which owns the
        # bounded terminal-snapshot retry state and never replaces good data
        # with an incomplete K8s response.
        "mlflow_url": mlflow_url,
        "config_overrides": forge.get("configOverrides", {}),
        "fjob_spec": spec,
        "fjob_status": status,
        "triggered_by_schedule": schedule_name or None,
        "trigger_type": trigger_type,
        "is_lock": bool(spec.get("lockOnly")),
    }


def _inherit_requester_fields(fields: dict, parent: Optional[FournosJob]) -> None:
    """Fill an operator-created recurring child from its archived parent."""
    if parent is None or fields.get("requester_subject"):
        return
    fields["requester_subject"] = parent.requester_subject or ""
    fields["requester_email"] = parent.requester_email or ""
    fields["requester_name"] = parent.requester_name or ""
    fields["auth_provider"] = parent.auth_provider or ""


async def _archive_job(job: dict) -> None:
    if _watcher_session is None:
        logger.warning("Watcher DB not initialised — skipping archive")
        return
    fields = _extract_forge_fields(job)
    job_name = fields.get("name")
    if not job_name:
        logger.warning("Skipping FournosJob with missing name")
        return

    async with _watcher_session() as session, session.begin():
        existing = await db_svc.get_job_by_name(session, job_name)
        schedule_parent = fields.get("triggered_by_schedule")
        parent = None
        if schedule_parent:
            parent = await db_svc.get_job_by_name(session, schedule_parent)
        if parent and not fields.get("requester_subject"):
            _inherit_requester_fields(fields, parent)
        previous_phase = existing.status if existing else None
        previous_message = existing.message if existing else None
        existing_stages = existing.stages if existing else None
        snapshot_attempts = existing.stage_snapshot_attempts if existing else 0
        snapshot_attempted_at = (
            existing.stage_snapshot_attempted_at if existing else None
        )
        enrichment_state = (
            existing.failure_enrichment_state if existing else "pending"
        ) or "pending"
        enrichment_attempts = (
            existing.failure_enrichment_attempts if existing else 0
        ) or 0
        enrichment_attempted_at = (
            existing.failure_enrichment_attempted_at if existing else None
        )
        forge_execution = dict(
            (existing.forge_execution if existing else None) or {}
        )
        provenance_state = (
            existing.forge_provenance_state if existing else "pending"
        ) or "pending"
        provenance_attempts = (
            existing.forge_provenance_attempts if existing else 0
        ) or 0
        provenance_attempted_at = (
            existing.forge_provenance_attempted_at if existing else None
        )

        phase = fields["status"]
        # Observe the runtime-resolved image while the Tekton pod still
        # exists. Terminal collection below owns its bounded retry budget.
        if phase not in TERMINAL_PHASES and not forge_execution.get("images"):
            observed_images = await asyncio.to_thread(
                k8s_client.get_forge_execution_images, job_name
            )
            if observed_images:
                forge_execution = _merge_forge_execution(
                    forge_execution,
                    images=observed_images,
                    observed_at=datetime.now(timezone.utc),
                )
                fields["forge_execution"] = forge_execution

        if phase in TERMINAL_PHASES:
            transitioning_into_terminal = previous_phase not in TERMINAL_PHASES
            now = datetime.now(timezone.utc)
            snapshot_complete = _has_usable_stage_snapshot(existing_stages)
            retry_due = _stage_snapshot_retry_due(
                snapshot_attempts or 0, snapshot_attempted_at, now
            )
            within_retry_budget = (
                snapshot_attempts or 0
            ) < STAGE_SNAPSHOT_MAX_ATTEMPTS
            if (
                not snapshot_complete
                and within_retry_budget
                and (transitioning_into_terminal or retry_due)
            ):
                consume_snapshot_attempt = True
                try:
                    new_stages = _compute_terminal_stages(
                        job_name,
                        fields.get("fjob_spec") or {},
                        fields.get("fjob_status") or {},
                    )
                except k8s_client.TaskRunLookupError as exc:
                    new_stages = None
                    # Authorization is a configuration problem, not evidence
                    # that TaskRuns disappeared. Preserve the retry budget so
                    # archiving recovers after RBAC is corrected.
                    consume_snapshot_attempt = exc.status not in (401, 403)
                    logger.warning(
                        "Could not snapshot stages for %s: %s",
                        job_name,
                        exc,
                    )
                if consume_snapshot_attempt:
                    fields["stage_snapshot_attempts"] = (
                        snapshot_attempts or 0
                    ) + 1
                    fields["stage_snapshot_attempted_at"] = now
                if new_stages is not None:
                    fields["stages"] = new_stages
            effective_stages = fields.get("stages", existing_stages or [])
            failure_summary = first_actionable_failure(
                effective_stages,
                job_phase=phase,
                job_message=fields.get("message", ""),
            )

            if (
                enrichment_state == "complete"
                and existing
                and existing.failure_summary
                and existing.failure_summary.get("source") == "mlflow_caliper"
            ):
                failure_summary = existing.failure_summary

            mlflow_url = fields.get("mlflow_url") or (
                existing.mlflow_url if existing else ""
            )
            enrichment_due = _failure_enrichment_retry_due(
                enrichment_attempts, enrichment_attempted_at, now
            )
            if _should_enrich_failure(
                mlflow_url=mlflow_url,
                enrichment_state=enrichment_state,
                enrichment_due=enrichment_due,
                transitioning_into_terminal=transitioning_into_terminal,
                enrichment_attempts=enrichment_attempts,
            ):
                try:
                    completion = await asyncio.wait_for(
                        fetch_caliper_completion(mlflow_url),
                        timeout=settings.MLFLOW_REQUEST_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    completion = None
                enrichment_attempts += 1
                fields["failure_enrichment_attempts"] = enrichment_attempts
                fields["failure_enrichment_attempted_at"] = now
                enrichment_state = completion.state if completion else "pending"
                if enrichment_attempts >= FAILURE_ENRICHMENT_MAX_ATTEMPTS and (
                    enrichment_state in {"pending", "malformed", "invalid_reference"}
                ):
                    enrichment_state = "exhausted"
                failure_summary = merge_caliper_failure(
                    failure_summary,
                    completion.first_failure if completion else None,
                )

            if not mlflow_url and enrichment_state == "pending":
                enrichment_state = "unavailable"
            fields["failure_enrichment_state"] = enrichment_state
            fields["failure_summary"] = failure_summary or {}
            fields["failure_outcome"] = (
                failure_summary.get("outcome", "") if failure_summary else ""
            )

            provenance_due = _forge_provenance_retry_due(
                provenance_attempts, provenance_attempted_at, now
            )
            fetch_state = "pending"
            provenance_eligible = provenance_due and (
                transitioning_into_terminal
                or provenance_attempts > 0
                or provenance_state in {"unavailable", "partial"}
            )
            attempted_provenance = False
            if provenance_eligible and not forge_execution.get("images"):
                attempted_provenance = True
                observed_images = await asyncio.to_thread(
                    k8s_client.get_forge_execution_images, job_name
                )
                if observed_images:
                    forge_execution = _merge_forge_execution(
                        forge_execution,
                        images=observed_images,
                        observed_at=now,
                    )
                    fields["forge_execution"] = forge_execution

            if (
                not forge_execution.get("gitVersions")
                and _should_fetch_forge_provenance(
                    mlflow_url=mlflow_url,
                    state=provenance_state,
                    retry_due=provenance_due,
                    transitioning_into_terminal=transitioning_into_terminal,
                    attempts=provenance_attempts,
                )
            ):
                attempted_provenance = True
                try:
                    provenance = await asyncio.wait_for(
                        fetch_forge_versions(mlflow_url),
                        timeout=settings.MLFLOW_REQUEST_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    provenance = None
                fetch_state = provenance.state if provenance else "pending"
                if provenance and provenance.versions:
                    forge_execution = _merge_forge_execution(
                        forge_execution,
                        git_versions=provenance.versions,
                        observed_at=now,
                    )
                    fields["forge_execution"] = forge_execution

            if attempted_provenance:
                provenance_attempts += 1
                fields["forge_provenance_attempts"] = provenance_attempts
                fields["forge_provenance_attempted_at"] = now

            provenance_state = _forge_provenance_state(
                forge_execution,
                terminal=True,
                mlflow_url=mlflow_url,
                fetch_state=fetch_state,
                attempts=provenance_attempts,
            )
            fields["forge_provenance_state"] = provenance_state
        elif forge_execution:
            fields["forge_provenance_state"] = "pending"
        # else: job isn't terminal — omit "stages" entirely so upsert_job's
        # on_conflict update leaves whatever's already stored untouched.

        db_job = await db_svc.upsert_job(session, **fields)
        if (
            parent
            and parent.work_items
            and (existing is None or not existing.work_items)
        ):
            await db_svc.copy_work_items(
                session,
                parent,
                db_job,
                created_by_subject=parent.requester_subject or "",
            )

        if (
            fields["status"] != previous_phase
            or fields["message"] != previous_message
        ):
            await db_svc.add_job_event(
                session,
                job_id=db_job.id,
                phase=fields["status"],
                message=fields["message"],
            )

    logger.info(
        "Archived FournosJob %s (phase=%s)", job_name, fields["status"]
    )


async def _full_sync() -> None:
    if _watcher_session is None:
        return
    try:
        all_jobs = k8s_client.list_fournos_jobs()
    except Exception as exc:
        logger.warning("Full sync: failed to list FournosJobs: %s", exc)
        return

    if not all_jobs:
        return

    synced = 0
    errors = 0
    for job in all_jobs:
        try:
            await _archive_job(job)
            synced += 1
        except Exception as exc:
            name = job.get("metadata", {}).get("name", "?")
            logger.warning(
                "Full sync: failed to archive %s: %s", name, exc
            )
            errors += 1

    logger.info(
        "Full sync complete: %d synced, %d errors (out of %d)",
        synced,
        errors,
        len(all_jobs),
    )


def _run_watch_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    _init_watcher_db(loop)

    try:
        loop.run_until_complete(_full_sync())
    except Exception as exc:
        logger.warning("Initial full sync failed: %s", exc)

    resource_version = ""
    last_sync = time.monotonic()

    while True:
        try:
            logger.info(
                "Starting FournosJob watch (rv=%s)",
                resource_version or "latest",
            )
            for event in k8s_client.watch_fournos_jobs(
                resource_version=resource_version, timeout=SYNC_INTERVAL_SECONDS
            ):
                obj = event.get("object", {})
                rv = obj.get("metadata", {}).get("resourceVersion", "")
                if rv:
                    resource_version = rv

                event_type = event.get("type", "")
                if event_type in ("ADDED", "MODIFIED"):
                    try:
                        loop.run_until_complete(_archive_job(obj))
                    except Exception as exc:
                        name = obj.get("metadata", {}).get("name", "?")
                        logger.error(
                            "Failed to archive event for %s (type=%s): %s",
                            name,
                            event_type,
                            exc,
                        )
                elif event_type == "DELETED":
                    name = obj.get("metadata", {}).get("name", "")
                    logger.info(
                        "FournosJob %s deleted from cluster", name
                    )

                if time.monotonic() - last_sync > SYNC_INTERVAL_SECONDS:
                    try:
                        loop.run_until_complete(_full_sync())
                    except Exception as exc:
                        logger.warning("Periodic sync failed: %s", exc)
                    last_sync = time.monotonic()

            # A quiet cluster produces no events, so the in-loop timer above
            # never fires. The watch server closes the stream at the timeout;
            # use that boundary to keep full syncs (including bounded stage-
            # snapshot retries) running once per interval even when idle.
            if time.monotonic() - last_sync >= SYNC_INTERVAL_SECONDS:
                try:
                    loop.run_until_complete(_full_sync())
                except Exception as exc:
                    logger.warning("Periodic sync failed: %s", exc)
                last_sync = time.monotonic()

        except Exception as exc:
            reason = str(exc)
            if "Forbidden" in reason or "Unauthorized" in reason:
                logger.warning(
                    "Watch stream auth error (will retry in 60s): %s", exc
                )
                time.sleep(60)
            else:
                logger.warning(
                    "Watch stream error (will restart in 5s): %s", exc
                )
                time.sleep(5)
            resource_version = ""

        try:
            loop.run_until_complete(_full_sync())
        except Exception as exc:
            logger.warning("Reconnect sync failed: %s", exc)
        last_sync = time.monotonic()


_watch_thread: Optional[threading.Thread] = None


def start_watcher() -> None:
    global _watch_thread
    if _watch_thread is not None and _watch_thread.is_alive():
        return

    if not k8s_client.is_connected():
        logger.warning("K8s not connected — fournos watcher not started")
        return

    loop = asyncio.new_event_loop()
    _watch_thread = threading.Thread(
        target=_run_watch_loop,
        args=(loop,),
        daemon=True,
        name="fjob-watcher",
    )
    _watch_thread.start()
    logger.info("FournosJob watcher started")
