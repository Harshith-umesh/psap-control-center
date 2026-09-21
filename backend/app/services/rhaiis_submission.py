"""RHAIIS-specific submission compatibility for the Control Center.

Forge remains the source of truth for RHAIIS presets.  These helpers only
translate the Control Center's legacy-friendly UI values into the keys and
shapes expected by the RHAIIS orchestration code.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException


def resolve_build_source(pull_sha: str, use_latest_main: bool) -> str:
    """Validate and normalize the RHAIIS build-source controls."""
    pull_sha = pull_sha.strip()
    if use_latest_main and pull_sha:
        raise HTTPException(
            400, "Choose either a pinned build source or latest main, not both."
        )
    if not use_latest_main and not pull_sha:
        raise HTTPException(
            400,
            "RHAIIS build source is required: choose a PR, commit SHA, release tag, or latest main.",
        )
    return "main" if use_latest_main else pull_sha


def normalize_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Translate schema-facing RHAIIS keys to Forge's real config keys."""
    normalized = dict(overrides)
    slack_member = normalized.pop("tests.rhaiis.slack_member_id", None)
    if slack_member and "tests.rhaiis.slack_user" not in normalized:
        normalized["tests.rhaiis.slack_user"] = slack_member

    # This is a UI-only toggle in the Forge schema. The orchestration code
    # reads compare_version, not a nonexistent compare_versions.enabled key.
    normalized.pop("rhaiis.compare_versions.enabled", None)

    workload_key = normalized.pop("tests.rhaiis.workload_key", None)
    if workload_key is not None and "tests.rhaiis.workload_keys" not in normalized:
        try:
            parsed = (
                json.loads(workload_key)
                if isinstance(workload_key, str)
                else workload_key
            )
        except (TypeError, ValueError):
            parsed = workload_key
        normalized["tests.rhaiis.workload_keys"] = (
            json.dumps(parsed) if isinstance(parsed, list) else parsed
        )
    return normalized


def add_matrix_workload_override(
    overrides: dict[str, Any], workloads: list[str]
) -> None:
    """Add the RHAIIS plural workload override for matrix submissions."""
    if workloads:
        overrides.setdefault("tests.rhaiis.workload_keys", json.dumps(workloads))
