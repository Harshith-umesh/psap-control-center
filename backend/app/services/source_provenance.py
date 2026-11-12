"""Helpers for Control Center's immutable test-request provenance."""

from __future__ import annotations

from typing import Any


def extract_source_request_fields(spec: dict) -> dict[str, Any]:
    """Extract the PR snapshot that Control Center submitted to Fournos.

    ``source_resolved_sha`` is the server-validated SHA placed in
    ``PULL_PULL_SHA`` at submission time. It is not a reconciliation claim
    about the commit Forge ultimately executed.
    """
    env = spec.get("env", {}) or {}
    repo_owner = str(env.get("REPO_OWNER", "")).strip()
    repo_name = str(env.get("REPO_NAME", "")).strip()
    repository = "/".join(part for part in (repo_owner, repo_name) if part)
    number_raw = str(env.get("PULL_NUMBER", "")).strip()
    try:
        pr_number = int(number_raw) if number_raw else None
    except ValueError:
        pr_number = None
    resolved_sha = str(env.get("PULL_PULL_SHA", "")).strip()
    requested_sha = str(
        env.get("CONTROL_CENTER_REQUESTED_SHA", resolved_sha)
    ).strip()
    pr_url = str(env.get("CONTROL_CENTER_PR_URL", "")).strip()
    if not pr_url and repository and pr_number:
        pr_url = "https://github.com/{}/pull/{}".format(
            repository, pr_number
        )
    return {
        "source_repository": repository,
        "source_pr_number": pr_number,
        "source_pr_url": pr_url,
        "source_head_branch": str(env.get("PULL_HEAD_REF", "")).strip(),
        "source_requested_sha": requested_sha,
        "source_resolved_sha": resolved_sha,
    }
