"""SQLAlchemy models for FournosJob history archival (PostgreSQL-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, Index,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class FournosJob(Base):
    __tablename__ = "fournos_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String(255), unique=True, nullable=False, index=True)
    project = Column(String(255), nullable=False, index=True)
    preset = Column(String(255), default="")
    cluster = Column(String(255), nullable=False, index=True)
    pipeline = Column(String(255), default="")
    owner = Column(String(255), default="", index=True)
    requester_subject = Column(String(255), default="", index=True)
    requester_email = Column(String(255), default="", index=True)
    requester_name = Column(String(255), default="")
    auth_provider = Column(String(50), default="")
    source_repository = Column(String(255), default="", index=True)
    source_pr_number = Column(Integer, nullable=True, index=True)
    source_pr_url = Column(String(1024), default="")
    source_head_branch = Column(String(255), default="")
    source_requested_sha = Column(String(64), default="", index=True)
    source_resolved_sha = Column(String(64), default="", index=True)
    # Observed execution evidence, kept separate from the source revision the
    # requester selected.  A merged-code run normally executes the Forge
    # commit baked into the container image; MLflow and the pod image digest
    # are therefore the authoritative record of what actually ran.
    forge_execution = Column(JSONB, default=dict)
    forge_provenance_state = Column(
        String(50), default="pending", nullable=False
    )
    forge_provenance_attempts = Column(Integer, default=0, nullable=False)
    forge_provenance_attempted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="Pending", index=True)
    message = Column(Text, default="")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    mlflow_url = Column(String(1024), default="")
    ci_artifacts_url = Column(String(1024), default="")
    config_overrides = Column(JSONB, default=dict)
    tags = Column(ARRAY(String), default=list)
    fjob_spec = Column(JSONB, default=dict)
    fjob_status = Column(JSONB, default=dict)
    # Snapshot of the merged pipeline stage list (same shape the live
    # job-detail endpoint builds via pipeline_definitions.merge_pipeline_
    # stages) taken when the job reaches a terminal phase (with a small retry
    # window for transient K8s failures). The PipelineRun/TaskRuns themselves
    # may be gone by the time a job is opened from History, so this is the
    # durable copy used to render the timeline and failed step.
    stages = Column(JSONB, default=list)
    # Failed/missing PipelineRun snapshots are retried a small, bounded number
    # of times. Keeping this state in the DB prevents every watcher restart or
    # full-sync pass from creating another Kubernetes API storm.
    stage_snapshot_attempts = Column(Integer, default=0, nullable=False)
    stage_snapshot_attempted_at = Column(DateTime(timezone=True), nullable=True)
    failure_outcome = Column(String(50), default="", index=True)
    failure_summary = Column(JSONB, default=dict)
    failure_enrichment_state = Column(
        String(50), default="pending", nullable=False
    )
    failure_enrichment_attempts = Column(Integer, default=0, nullable=False)
    failure_enrichment_attempted_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, default="")
    triggered_by_schedule = Column(String(255), nullable=True, index=True)
    trigger_type = Column(String(50), default="manual")
    # A cluster lock is just a FournosJob with spec.lockOnly=True — tracked
    # explicitly (rather than inferred from name/trigger_type, both of which
    # can collide with real deferred jobs) so the History tab can exclude
    # locks the same way it excludes recurring-parent templates.
    is_lock = Column(Boolean, default=False, index=True)

    events = relationship(
        "FournosJobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    work_items = relationship(
        "FournosJobWorkItem",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_fournos_jobs_created", "created_at"),
        # History tab defaults to sorting by completed_at (most recently
        # finished first) and always filters on status/is_lock/trigger_type
        # — without an index on completed_at, that ORDER BY forces a full
        # table scan + filesort that gets slower as history grows.
        Index("ix_fournos_jobs_completed", "completed_at"),
        # History uses creation time only when a terminal job has no recorded
        # completion time. Match that effective-date expression so the default
        # newest-first query can remain an index scan as the table grows.
        Index(
            "ix_fournos_jobs_effective_date",
            func.coalesce(completed_at, created_at),
        ),
        Index("ix_fournos_jobs_trigger_type", "trigger_type"),
        Index(
            "ix_fournos_jobs_source_pr",
            "source_repository", "source_pr_number",
        ),
        # Composite index matching the History query's WHERE + ORDER BY
        # shape (status IN (...) AND is_lock = false AND trigger_type != ...
        # ORDER BY completed_at DESC) so it can be satisfied with an index
        # scan instead of a scan over the whole table.
        Index(
            "ix_fournos_jobs_history",
            "status", "is_lock", "trigger_type", "completed_at",
        ),
    )

    def __repr__(self):
        return f"<FournosJob({self.name} status={self.status})>"


class FournosJobEvent(Base):
    __tablename__ = "fournos_job_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id = Column(
        String(36),
        ForeignKey("fournos_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase = Column(String(50), nullable=False)
    message = Column(Text, default="")
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    job = relationship("FournosJob", back_populates="events")

    def __repr__(self):
        return f"<FournosJobEvent({self.phase} @ {self.timestamp})>"


class FournosJobWorkItem(Base):
    """A safe, read-only reference to work tracked outside Control Center."""

    __tablename__ = "fournos_job_work_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id = Column(
        String(36),
        ForeignKey("fournos_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(50), nullable=False, index=True)
    key = Column(String(100), nullable=False, index=True)
    url = Column(String(1024), nullable=False)
    created_by_subject = Column(String(255), default="")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    job = relationship("FournosJob", back_populates="work_items")

    __table_args__ = (
        UniqueConstraint(
            "job_id", "provider", "key",
            name="uq_fournos_job_work_item",
        ),
        Index(
            "ix_fournos_job_work_items_provider_key",
            "provider", "key",
        ),
    )


class FournosHistoryPreference(Base):
    """Latest History view saved for one authenticated principal."""

    __tablename__ = "fournos_history_preferences"

    subject = Column(String(255), primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    state = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
