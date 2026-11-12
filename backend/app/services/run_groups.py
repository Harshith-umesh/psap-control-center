"""Validation helpers for generic, reusable run groupings."""

from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID


RUN_GROUP_TYPES = ("experiment", "workload", "campaign", "cohort")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class RunGroupValidationError(ValueError):
    pass


def normalize_group_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in RUN_GROUP_TYPES:
        raise RunGroupValidationError(
            "Group type must be one of: {}".format(
                ", ".join(RUN_GROUP_TYPES)
            )
        )
    return normalized


def normalize_group_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _KEY_RE.fullmatch(normalized):
        raise RunGroupValidationError(
            "Group key must start with a letter or number and contain only "
            "lowercase letters, numbers, dots, underscores, or dashes"
        )
    return normalized


def normalize_display_name(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized or len(normalized) > 255:
        raise RunGroupValidationError(
            "Group display name must be between 1 and 255 characters"
        )
    return normalized


def normalize_description(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > 2000:
        raise RunGroupValidationError(
            "Group description must be at most 2000 characters"
        )
    return normalized


def normalize_group_ids(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            group_id = str(UUID(str(value or "").strip()))
        except (TypeError, ValueError, AttributeError) as exc:
            raise RunGroupValidationError("Invalid run group identifier") from exc
        if group_id not in seen:
            seen.add(group_id)
            normalized.append(group_id)
    if len(normalized) > 20:
        raise RunGroupValidationError(
            "A run may belong to at most 20 groups"
        )
    return normalized


def can_manage_group(group, user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    subject = str(user.get("subject") or "")
    return bool(subject and subject == (group.created_by_subject or ""))

