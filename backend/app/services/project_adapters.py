"""Optional project-specific adapters for the generic Forge integration.

The default path is deliberately a no-op.  A project gets an adapter only
when it opts into Control Center compatibility behavior; its implementation
lives in a project-specific module.
"""

from __future__ import annotations

from typing import Any, Callable

from app.schemas.ui_schema import ProjectUiSchema
from app.services import rhaiis_submission, rhaiis_ui_schema

_SCHEMA_AUGMENTERS: dict[str, Callable[[ProjectUiSchema], None]] = {
    "rhaiis": rhaiis_ui_schema.augment_schema,
}


def augment_schema(project: str, schema: ProjectUiSchema) -> None:
    augmenter = _SCHEMA_AUGMENTERS.get(project)
    if augmenter is not None:
        augmenter(schema)


def resolve_build_source(project: str, pull_sha: str, use_latest_main: bool) -> str:
    if project == "rhaiis":
        return rhaiis_submission.resolve_build_source(pull_sha, use_latest_main)
    return pull_sha.strip()


def normalize_overrides(project: str, overrides: dict[str, Any]) -> dict[str, Any]:
    if project == "rhaiis":
        return rhaiis_submission.normalize_overrides(overrides)
    return overrides


def add_matrix_workload_override(
    project: str, overrides: dict[str, Any], workloads: list[str]
) -> None:
    if project == "rhaiis":
        rhaiis_submission.add_matrix_workload_override(overrides, workloads)
