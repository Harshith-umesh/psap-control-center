"""Database operations for FournosJob history — uses the shared PSAP database."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Tuple
from uuid import uuid4

from sqlalchemy import Text, and_, cast, delete, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.fournos_job import (
    FournosHistoryPreference,
    FournosJob,
    FournosJobEvent,
    FournosJobGroupMembership,
    FournosJobWorkItem,
    FournosRunGroup,
)

logger = logging.getLogger(__name__)

# History is meant to be "jobs that ran end to end" — recurring *templates*
# and cluster locks have their own tabs (Schedules/Locks) and never belong
# here, and a job that's still live shouldn't show up twice (once live,
# once mid-run in history) before it actually finishes.
TERMINAL_STATUSES = ("Succeeded", "Failed", "Stopped")

# Maps the sortable keys the frontend's table headers use to actual
# columns — "date" means "when it finished" (completed_at), which is what
# history is naturally ordered by; duration/name/etc. are exposed too so
# every column header can drive the sort.
_SORT_COLUMNS = {
    "name": FournosJob.name,
    "project": FournosJob.project,
    "cluster": FournosJob.cluster,
    "status": FournosJob.status,
    "owner": FournosJob.owner,
    # Some legacy/aborted jobs have no completed_at even though they are
    # terminal. Treat their creation time as the best available date so NULL
    # values do not float to the top of a descending PostgreSQL result.
    "date": func.coalesce(FournosJob.completed_at, FournosJob.created_at),
    "duration": FournosJob.duration_seconds,
    "triggered_by": FournosJob.triggered_by_schedule,
}

_PUBLIC_SEARCH_COLUMNS = (
    FournosJob.name,
    FournosJob.project,
    FournosJob.cluster,
    FournosJob.pipeline,
    FournosJob.preset,
    FournosJob.source_repository,
    FournosJob.source_pr_url,
    FournosJob.source_head_branch,
    FournosJob.source_requested_sha,
    FournosJob.source_resolved_sha,
    FournosJob.status,
    FournosJob.failure_outcome,
)

_IDENTITY_SEARCH_COLUMNS = (
    FournosJob.owner,
    FournosJob.requester_name,
    FournosJob.requester_email,
)


def _normalized_options(values: Sequence[Any], limit: int) -> list[str]:
    """Return sorted, unique non-empty strings without exposing DB nulls."""
    normalized = {
        str(value).strip()
        for value in values
        if str(value or "").strip()
    }
    return sorted(normalized)[:limit]


def _text_search_expression(columns, query: str, *, postgres: bool):
    """Build indexed PostgreSQL token search with a portable test fallback."""
    if postgres:
        empty = literal_column("''")
        space = literal_column("' '")
        document = func.coalesce(columns[0], empty)
        for column in columns[1:]:
            document = document + space + func.coalesce(column, empty)
        config = literal_column("'simple'::regconfig")
        return func.to_tsvector(config, document).op("@@")(
            func.plainto_tsquery(config, query)
        )

    tokens = [token.lower() for token in query.split() if token]
    return and_(
        *[
            or_(
                *[
                    func.lower(func.coalesce(column, "")).contains(
                        token, autoescape=True
                    )
                    for column in columns
                ]
            )
            for token in tokens
        ]
    )


async def upsert_job(session: AsyncSession, **kwargs: Any) -> FournosJob:
    if "id" not in kwargs:
        kwargs["id"] = str(uuid4())

    update_cols = {
        k: v for k, v in kwargs.items()
        if k not in ("id", "name") and v is not None
    }

    stmt = (
        pg_insert(FournosJob)
        .values(**kwargs)
        .on_conflict_do_update(index_elements=["name"], set_=update_cols)
        .returning(FournosJob)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def add_job_event(
    session: AsyncSession,
    job_id: str,
    phase: str,
    message: str = "",
) -> FournosJobEvent:
    event = FournosJobEvent(job_id=job_id, phase=phase, message=message)
    session.add(event)
    await session.flush()
    return event


async def get_job_by_name(
    session: AsyncSession, name: str
) -> Optional[FournosJob]:
    result = await session.execute(
        select(FournosJob).where(FournosJob.name == name)
    )
    return result.scalar_one_or_none()


def serialize_work_items(job: FournosJob) -> list[dict[str, str]]:
    return [
        {"provider": item.provider, "key": item.key, "url": item.url}
        for item in sorted(
            job.work_items or [], key=lambda item: (item.provider, item.key)
        )
    ]


async def replace_work_items(
    session: AsyncSession,
    job: FournosJob,
    items: Sequence[dict[str, str]],
    *,
    created_by_subject: str,
) -> None:
    await session.execute(
        delete(FournosJobWorkItem).where(FournosJobWorkItem.job_id == job.id)
    )
    for item in items:
        session.add(FournosJobWorkItem(
            job_id=job.id,
            provider=item["provider"],
            key=item["key"],
            url=item["url"],
            created_by_subject=created_by_subject,
        ))
    await session.flush()


async def copy_work_items(
    session: AsyncSession,
    source: Optional[FournosJob],
    target: FournosJob,
    *,
    created_by_subject: str,
) -> None:
    if source is None:
        return
    await replace_work_items(
        session,
        target,
        serialize_work_items(source),
        created_by_subject=created_by_subject,
    )


async def get_work_items_by_job_names(
    session: AsyncSession, names: Sequence[str]
) -> dict[str, list[dict[str, str]]]:
    if not names:
        return {}
    result = await session.execute(
        select(FournosJob.name, FournosJobWorkItem)
        .join(
            FournosJobWorkItem,
            FournosJobWorkItem.job_id == FournosJob.id,
        )
        .where(FournosJob.name.in_(names))
        .order_by(
            FournosJob.name,
            FournosJobWorkItem.provider,
            FournosJobWorkItem.key,
        )
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    for name, item in result.all():
        grouped.setdefault(name, []).append({
            "provider": item.provider,
            "key": item.key,
            "url": item.url,
        })
    return grouped


def serialize_run_group(group: FournosRunGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "group_type": group.group_type,
        "key": group.key,
        "display_name": group.display_name,
        "description": group.description or "",
        "archived": bool(group.archived),
    }


def serialize_run_groups(job: FournosJob) -> list[dict[str, Any]]:
    groups = [membership.group for membership in job.group_memberships or []]
    return [
        serialize_run_group(group)
        for group in sorted(groups, key=lambda item: (item.group_type, item.key))
    ]


async def list_run_groups(
    session: AsyncSession,
    *,
    include_archived: bool = False,
    group_type: Optional[str] = None,
    query: Optional[str] = None,
) -> list[FournosRunGroup]:
    filters = []
    if not include_archived:
        filters.append(FournosRunGroup.archived.is_(False))
    if group_type:
        filters.append(FournosRunGroup.group_type == group_type)
    if query:
        needle = query.strip().lower()
        filters.append(or_(
            func.lower(FournosRunGroup.key).contains(needle, autoescape=True),
            func.lower(FournosRunGroup.display_name).contains(
                needle, autoescape=True
            ),
        ))
    result = await session.execute(
        select(FournosRunGroup)
        .where(*filters)
        .order_by(
            FournosRunGroup.group_type,
            FournosRunGroup.display_name,
            FournosRunGroup.key,
        )
    )
    return list(result.scalars().all())


async def get_run_group(
    session: AsyncSession, group_id: str
) -> Optional[FournosRunGroup]:
    result = await session.execute(
        select(FournosRunGroup).where(FournosRunGroup.id == group_id)
    )
    return result.scalar_one_or_none()


async def validate_active_run_group_ids(
    session: AsyncSession, group_ids: Sequence[str]
) -> None:
    unique_ids = list(dict.fromkeys(group_ids))
    if not unique_ids:
        return
    result = await session.execute(
        select(FournosRunGroup.id).where(
            FournosRunGroup.id.in_(unique_ids),
            FournosRunGroup.archived.is_(False),
        )
    )
    found = set(result.scalars().all())
    if found != set(unique_ids):
        raise ValueError("One or more run groups do not exist or are archived")


async def create_run_group(
    session: AsyncSession,
    *,
    group_type: str,
    key: str,
    display_name: str,
    description: str,
    created_by_subject: str,
) -> FournosRunGroup:
    group = FournosRunGroup(
        group_type=group_type,
        key=key,
        display_name=display_name,
        description=description,
        created_by_subject=created_by_subject,
    )
    session.add(group)
    await session.flush()
    return group


async def replace_run_groups(
    session: AsyncSession,
    job: FournosJob,
    group_ids: Sequence[str],
    *,
    created_by_subject: str,
    allow_archived: bool = False,
) -> None:
    unique_ids = list(dict.fromkeys(group_ids))
    existing_result = await session.execute(
        select(FournosJobGroupMembership.group_id).where(
            FournosJobGroupMembership.job_id == job.id
        )
    )
    existing_ids = set(existing_result.scalars().all())
    groups: list[FournosRunGroup] = []
    if unique_ids:
        result = await session.execute(
            select(FournosRunGroup).where(FournosRunGroup.id.in_(unique_ids))
        )
        groups = list(result.scalars().all())
        found = {group.id for group in groups}
        missing = [group_id for group_id in unique_ids if group_id not in found]
        if missing:
            raise ValueError("One or more run groups do not exist")
        if not allow_archived and any(
            group.archived and group.id not in existing_ids for group in groups
        ):
            raise ValueError("Archived run groups cannot be newly associated")

    await session.execute(
        delete(FournosJobGroupMembership).where(
            FournosJobGroupMembership.job_id == job.id
        )
    )
    for group_id in unique_ids:
        session.add(FournosJobGroupMembership(
            job_id=job.id,
            group_id=group_id,
            created_by_subject=created_by_subject,
        ))
    await session.flush()


async def copy_run_groups(
    session: AsyncSession,
    source: Optional[FournosJob],
    target: FournosJob,
    *,
    created_by_subject: str,
) -> None:
    if source is None:
        return
    await replace_run_groups(
        session,
        target,
        [membership.group_id for membership in source.group_memberships or []],
        created_by_subject=created_by_subject,
        allow_archived=True,
    )


async def get_requester_subjects_by_names(
    session: AsyncSession,
    names: Sequence[str],
) -> dict[str, str]:
    if not names:
        return {}
    result = await session.execute(
        select(FournosJob.name, FournosJob.requester_subject).where(
            FournosJob.name.in_(names)
        )
    )
    return {
        name: subject
        for name, subject in result.all()
        if subject
    }


async def list_jobs(
    session: AsyncSession,
    *,
    project: Optional[str] = None,
    cluster: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    requester_subject: Optional[str] = None,
    query: Optional[str] = None,
    identity: Optional[str] = None,
    failure_outcome: Optional[str] = None,
    repository: Optional[str] = None,
    pr_number: Optional[int] = None,
    source_sha: Optional[str] = None,
    forge: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    work_item_provider: Optional[str] = None,
    work_item_key: Optional[str] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[FournosJob], int]:
    is_pg = settings.DATABASE_URL.startswith("postgresql")
    filters = [
        FournosJob.status.in_(TERMINAL_STATUSES),
        FournosJob.is_lock.is_(False),
        FournosJob.trigger_type != "recurring-parent",
    ]
    if project:
        filters.append(FournosJob.project == project)
    if cluster:
        filters.append(FournosJob.cluster == cluster)
    if status:
        filters.append(FournosJob.status == status)
    if owner:
        filters.append(FournosJob.owner == owner)
    if requester_subject:
        filters.append(FournosJob.requester_subject == requester_subject)
    if query:
        filters.append(
            _text_search_expression(
                _PUBLIC_SEARCH_COLUMNS, query, postgres=is_pg
            )
        )
    if identity:
        filters.append(
            _text_search_expression(
                _IDENTITY_SEARCH_COLUMNS, identity, postgres=is_pg
            )
        )
    if failure_outcome:
        filters.append(FournosJob.failure_outcome == failure_outcome)
    if repository:
        filters.append(
            func.lower(FournosJob.source_repository) == repository.lower()
        )
    if pr_number:
        filters.append(FournosJob.source_pr_number == pr_number)
    if source_sha:
        normalized_sha = source_sha.lower()
        filters.append(or_(
            func.lower(FournosJob.source_requested_sha).startswith(
                normalized_sha, autoescape=True
            ),
            func.lower(FournosJob.source_resolved_sha).startswith(
                normalized_sha, autoescape=True
            ),
        ))
    if forge:
        normalized_forge = forge.lower()
        if is_pg:
            git_version = func.lower(cast(
                FournosJob.forge_execution.op("#>>")(
                    literal_column("'{gitVersions,0,version}'")
                ),
                Text,
            ))
            image_id = cast(
                FournosJob.forge_execution.op("#>>")(
                    literal_column("'{images,0,imageID}'")
                ),
                Text,
            )
            image_digest = func.lower(func.split_part(
                image_id, literal_column("'@'"), literal_column("2")
            ))
            filters.append(or_(
                git_version.startswith(normalized_forge, autoescape=True),
                image_digest.startswith(normalized_forge, autoescape=True),
            ))
        else:
            filters.append(
                func.lower(cast(FournosJob.forge_execution, Text)).contains(
                    normalized_forge, autoescape=True
                )
            )
    if tags:
        if is_pg:
            filters.append(FournosJob.tags.contains(list(tags)))
        else:
            for tag in tags:
                filters.append(
                    func.lower(cast(FournosJob.tags, Text)).contains(
                        tag.lower(), autoescape=True
                    )
                )
    if work_item_provider or work_item_key:
        work_item_filters = []
        if work_item_provider:
            work_item_filters.append(
                FournosJobWorkItem.provider == work_item_provider.lower()
            )
        if work_item_key:
            work_item_filters.append(
                FournosJobWorkItem.key == work_item_key.upper()
            )
        filters.append(FournosJob.work_items.any(and_(*work_item_filters)))
    if created_after:
        filters.append(FournosJob.created_at >= created_after)
    if created_before:
        filters.append(FournosJob.created_at <= created_before)

    sort_col = _SORT_COLUMNS.get(sort_by or "date", _SORT_COLUMNS["date"])
    order_by = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    tie_breakers = (FournosJob.created_at.desc(), FournosJob.name.desc())

    if is_pg:
        # Postgres: get the page of rows and the total count in a single
        # query via a window function, instead of running the same
        # filtered query twice (once for rows, once for COUNT(*)).
        stmt = (
            select(FournosJob, func.count().over().label("total_count"))
            .where(*filters)
            .order_by(order_by, *tie_breakers)
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        rows = result.all()
        jobs = [row[0] for row in rows]
        total = rows[0].total_count if rows else 0
        if not rows and offset == 0:
            total = 0
        elif not rows:
            # Page beyond the last row — window function can't tell us the
            # total, so fall back to a plain count for this edge case.
            count_result = await session.execute(
                select(func.count(FournosJob.id)).where(*filters)
            )
            total = count_result.scalar() or 0
        return jobs, total

    # SQLite (and any other non-window-function-friendly backend): fall
    # back to the original two-query approach.
    stmt = (
        select(FournosJob)
        .where(*filters)
        .order_by(order_by, *tie_breakers)
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count(FournosJob.id)).where(*filters)

    result = await session.execute(stmt)
    jobs = result.scalars().all()

    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    return jobs, total


async def get_history_preference(
    session: AsyncSession, subject: str
) -> Optional[FournosHistoryPreference]:
    result = await session.execute(
        select(FournosHistoryPreference).where(
            FournosHistoryPreference.subject == subject
        )
    )
    return result.scalar_one_or_none()


async def get_history_filter_options(
    session: AsyncSession,
    *,
    include_identity: bool,
    limit: int = 200,
) -> dict[str, list[Any]]:
    """Discover bounded combobox choices from the complete durable History.

    These are suggestions, not an allow-list. Identity values are only queried
    for authenticated callers because public History deliberately redacts them.
    """
    filters = [
        FournosJob.status.in_(TERMINAL_STATUSES),
        FournosJob.is_lock.is_(False),
        FournosJob.trigger_type != "recurring-parent",
    ]

    async def distinct_values(expression) -> list[Any]:
        result = await session.execute(
            select(expression)
            .where(*filters, expression.is_not(None), expression != "")
            .distinct()
            .order_by(expression)
            .limit(limit)
        )
        return list(result.scalars().all())

    identities: list[str] = []
    if include_identity:
        identity_values: list[Any] = []
        for expression in _IDENTITY_SEARCH_COLUMNS:
            identity_values.extend(await distinct_values(expression))
        identities = _normalized_options(identity_values, limit)

    repositories = _normalized_options(
        await distinct_values(FournosJob.source_repository), limit
    )

    pr_result = await session.execute(
        select(FournosJob.source_pr_number)
        .where(*filters, FournosJob.source_pr_number.is_not(None))
        .distinct()
        .order_by(FournosJob.source_pr_number.desc())
        .limit(limit)
    )
    pr_numbers = list(pr_result.scalars().all())

    sha_values: list[Any] = []
    for expression in (
        FournosJob.source_requested_sha,
        FournosJob.source_resolved_sha,
    ):
        sha_values.extend(await distinct_values(expression))

    forge_values: list[Any] = []
    if settings.DATABASE_URL.startswith("postgresql"):
        forge_version = cast(
            FournosJob.forge_execution.op("#>>")(
                literal_column("'{gitVersions,0,version}'")
            ),
            Text,
        )
        forge_image_id = cast(
            FournosJob.forge_execution.op("#>>")(
                literal_column("'{images,0,imageID}'")
            ),
            Text,
        )
        forge_digest = func.split_part(
            forge_image_id, literal_column("'@'"), literal_column("2")
        )
        forge_values.extend(await distinct_values(forge_version))
        forge_values.extend(await distinct_values(forge_digest))

    tag_values: list[Any] = []
    if settings.DATABASE_URL.startswith("postgresql"):
        tag = func.unnest(FournosJob.tags).label("tag")
        tag_result = await session.execute(
            select(tag)
            .where(*filters)
            .distinct()
            .order_by(tag)
            .limit(limit)
        )
        tag_values = list(tag_result.scalars().all())

    work_item_providers: list[str] = []
    work_item_keys: list[str] = []
    if include_identity:
        provider_result = await session.execute(
            select(FournosJobWorkItem.provider)
            .join(FournosJob, FournosJob.id == FournosJobWorkItem.job_id)
            .where(*filters)
            .distinct()
            .order_by(FournosJobWorkItem.provider)
            .limit(limit)
        )
        key_result = await session.execute(
            select(FournosJobWorkItem.key)
            .join(FournosJob, FournosJob.id == FournosJobWorkItem.job_id)
            .where(*filters)
            .distinct()
            .order_by(FournosJobWorkItem.key)
            .limit(limit)
        )
        work_item_providers = list(provider_result.scalars().all())
        work_item_keys = list(key_result.scalars().all())

    return {
        "identities": identities,
        "repositories": repositories,
        "pr_numbers": pr_numbers,
        "source_shas": _normalized_options(sha_values, limit),
        "forge": _normalized_options(forge_values, limit),
        "tags": _normalized_options(tag_values, limit),
        "work_item_providers": _normalized_options(
            work_item_providers, limit
        ),
        "work_item_keys": _normalized_options(work_item_keys, limit),
    }


async def save_history_preference(
    session: AsyncSession,
    *,
    subject: str,
    schema_version: int,
    state: dict,
) -> FournosHistoryPreference:
    if settings.DATABASE_URL.startswith("postgresql"):
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(FournosHistoryPreference)
            .values(
                subject=subject,
                schema_version=schema_version,
                state=state,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["subject"],
                set_={
                    "schema_version": schema_version,
                    "state": state,
                    "updated_at": now,
                },
            )
            .returning(FournosHistoryPreference)
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    preference = await get_history_preference(session, subject)
    if preference is None:
        preference = FournosHistoryPreference(
            subject=subject,
            schema_version=schema_version,
            state=state,
        )
        session.add(preference)
    else:
        preference.schema_version = schema_version
        preference.state = state
        preference.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return preference


async def delete_history_preference(
    session: AsyncSession, subject: str
) -> bool:
    preference = await get_history_preference(session, subject)
    if preference is None:
        return False
    await session.delete(preference)
    await session.flush()
    return True


async def list_jobs_by_schedule(
    session: AsyncSession, schedule_name: str
) -> Sequence[FournosJob]:
    result = await session.execute(
        select(FournosJob)
        .where(FournosJob.triggered_by_schedule == schedule_name)
        .order_by(FournosJob.created_at.desc())
    )
    return result.scalars().all()


async def delete_job_by_name(
    session: AsyncSession, name: str
) -> bool:
    job = await get_job_by_name(session, name)
    if job is None:
        return False
    await session.delete(job)
    await session.flush()
    return True


async def get_job_events(
    session: AsyncSession, job_id: str
) -> Sequence[FournosJobEvent]:
    result = await session.execute(
        select(FournosJobEvent)
        .where(FournosJobEvent.job_id == job_id)
        .order_by(FournosJobEvent.timestamp)
    )
    return result.scalars().all()
