"""Normalize execution and test failure details for Fournos jobs.

The Kubernetes/Tekton state remains authoritative for whether pipeline work
ran, while optional result providers (currently Caliper metadata in MLflow)
can add the test-level reason.  Keeping this logic independent of projects
prevents the Testing UI from growing product-specific failure handling.
"""

from __future__ import annotations

import re
from typing import Any, Optional


OUTCOME_BY_STATUS = {
    "Succeeded": "succeeded",
    "Failed": "failed",
    "Cancelled": "cancelled",
    "Skipped": "skipped",
    "NotRun": "not_run",
    "Unknown": "unknown",
}

# Deliberately conservative.  Generic command failures, OOMs, and application
# timeouts can be caused by the test itself and must not automatically be
# presented as infrastructure failures.
INFRASTRUCTURE_REASON_CODES = {
    "CreateContainerConfigError",
    "CreateContainerError",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "NodeLost",
    "Evicted",
    "FailedScheduling",
    "TaskRunTimeout",
    "PipelineRunTimeout",
    "CouldntGetTask",
    "CouldntGetPipeline",
    "TaskRunResolutionFailed",
    "PipelineRunResolutionFailed",
    "ResourceVerificationFailed",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|access[_-]?token)"
    r"(\s*[:=]\s*)([^&\s]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/=-]{12,})")
_URL_USERINFO = re.compile(r"(https?://)([^/@\s]+)@", re.IGNORECASE)


def normalize_reason(value: Any, *, limit: int = 2000) -> str:
    """Return a bounded, display-safe reason without changing its meaning."""
    if value is None:
        return ""
    reason = _CONTROL_CHARS.sub("", str(value)).strip()
    reason = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", reason)
    reason = _BEARER_TOKEN.sub(r"\1\2[REDACTED]", reason)
    reason = _URL_USERINFO.sub(r"\1[REDACTED]@", reason)
    if len(reason) <= limit:
        return reason
    return reason[: limit - 1].rstrip() + "…"


def outcome_for_status(status: str) -> str:
    return OUTCOME_BY_STATUS.get(status, "unknown" if status else "")


def is_infrastructure_reason(*reason_codes: str) -> bool:
    return any(code in INFRASTRUCTURE_REASON_CODES for code in reason_codes if code)


def first_actionable_failure(
    stages: list[dict],
    *,
    job_phase: str = "",
    job_message: str = "",
) -> Optional[dict]:
    """Build a stable job-level summary from ordered stage evidence."""
    for stage in stages:
        outcome = stage.get("outcome") or outcome_for_status(stage.get("status", ""))
        if outcome not in ("failed", "infrastructure_error"):
            continue
        return {
            "outcome": outcome,
            "stage": stage.get("name", ""),
            "stageDisplayName": stage.get("displayName", stage.get("name", "")),
            "step": stage.get("failedStep", ""),
            "reason": normalize_reason(stage.get("reason"))
            or "The stage failed without a reported reason.",
            "reasonCode": stage.get("reasonCode", ""),
            "source": stage.get("reasonSource", "unknown"),
            "artifactPath": "",
        }

    cancelled = next(
        (
            stage for stage in stages
            if (stage.get("outcome") or outcome_for_status(stage.get("status", "")))
            == "cancelled"
        ),
        None,
    )
    if cancelled or job_phase == "Stopped":
        return {
            "outcome": "cancelled",
            "stage": (cancelled or {}).get("name", ""),
            "stageDisplayName": (cancelled or {}).get(
                "displayName", (cancelled or {}).get("name", "")
            ),
            "step": (cancelled or {}).get("failedStep", ""),
            "reason": normalize_reason(
                (cancelled or {}).get("reason") or job_message
            ) or "The job was stopped.",
            "reasonCode": (cancelled or {}).get("reasonCode", ""),
            "source": (cancelled or {}).get("reasonSource", "fournos_status"),
            "artifactPath": "",
        }

    if job_phase == "Failed":
        unknown_stage = next(
            (
                stage for stage in stages
                if (stage.get("outcome") or outcome_for_status(stage.get("status", "")))
                == "unknown"
            ),
            None,
        )
        return {
            "outcome": "unknown",
            "stage": (unknown_stage or {}).get("name", ""),
            "stageDisplayName": (unknown_stage or {}).get(
                "displayName", (unknown_stage or {}).get("name", "")
            ),
            "step": (unknown_stage or {}).get("failedStep", ""),
            "reason": normalize_reason(
                (unknown_stage or {}).get("reason") or job_message
            )
            or "The job failed after detailed execution resources were unavailable.",
            "reasonCode": (unknown_stage or {}).get("reasonCode", ""),
            "source": (unknown_stage or {}).get("reasonSource")
            or ("fournos_status" if job_message else "unknown"),
            "artifactPath": "",
        }

    return None


def select_failure_summary(
    live_summary: Optional[dict],
    archived_summary: Optional[dict],
) -> Optional[dict]:
    """Choose the most useful summary without letting stale history win."""

    def _quality(summary: Optional[dict]) -> int:
        if not summary:
            return 0
        outcome = summary.get("outcome", "")
        if outcome in ("infrastructure_error", "cancelled"):
            return 60
        if summary.get("source") == "mlflow_caliper":
            return 50
        if outcome == "failed" and (
            summary.get("stage")
            or summary.get("step")
            or summary.get("reasonCode")
        ):
            return 30
        if outcome == "failed":
            return 20
        if outcome == "unknown":
            return 10
        return 5

    # Prefer current execution evidence on equal quality. The one archived
    # signal intentionally ranked above it is richer provider enrichment.
    if _quality(archived_summary) > _quality(live_summary):
        return archived_summary
    return live_summary or archived_summary


def merge_caliper_failure(
    execution_summary: Optional[dict],
    caliper_failure: Optional[dict],
) -> Optional[dict]:
    """Add a test-level failure without hiding stronger platform evidence."""
    if not caliper_failure:
        return execution_summary
    if execution_summary and execution_summary.get("outcome") in (
        "infrastructure_error",
        "cancelled",
    ):
        return execution_summary

    summary = dict(execution_summary or {})
    if execution_summary:
        summary["executionReason"] = execution_summary.get("reason", "")
        summary["executionSource"] = execution_summary.get("source", "")
    summary.update(
        {
            "outcome": "failed",
            "reason": normalize_reason(caliper_failure.get("reason"))
            or "The test reported an unsuccessful completion.",
            "reasonCode": "CALIPER_TEST_FAILED",
            "source": "mlflow_caliper",
            "artifactPath": caliper_failure.get("artifactPath", ""),
        }
    )
    if not summary.get("stage"):
        summary["stage"] = caliper_failure.get("stage", "test")
        summary["stageDisplayName"] = caliper_failure.get(
            "stageDisplayName", "Test"
        )
    summary.setdefault("step", "")
    return summary
