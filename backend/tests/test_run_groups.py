from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import fournos as fournos_api
from app.services import fournos_db_service as db_service
from app.services import run_groups


def test_group_fields_are_generic_and_normalized():
    assert run_groups.normalize_group_type(" Campaign ") == "campaign"
    assert run_groups.normalize_group_key(" CPT.2026_09 ") == "cpt.2026_09"
    assert run_groups.normalize_display_name("  September   CPT  ") == "September CPT"
    assert run_groups.RUN_GROUP_TYPES == (
        "experiment", "workload", "campaign", "cohort"
    )


@pytest.mark.parametrize(
    "value",
    ["product", "rhaiis", ""],
)
def test_invalid_group_types_are_rejected(value):
    with pytest.raises(run_groups.RunGroupValidationError):
        run_groups.normalize_group_type(value)


@pytest.mark.parametrize("value", ["", "campaign/type", "UPPER CASE"])
def test_invalid_group_keys_are_rejected(value):
    with pytest.raises(run_groups.RunGroupValidationError):
        run_groups.normalize_group_key(value)


def test_group_ids_are_uuid_normalized_and_deduplicated():
    first = str(uuid4())
    second = str(uuid4())
    assert run_groups.normalize_group_ids([first, first, second]) == [
        first, second
    ]
    with pytest.raises(run_groups.RunGroupValidationError):
        run_groups.normalize_group_ids(["not-a-uuid"])


def test_group_management_and_membership_authorization():
    group = SimpleNamespace(created_by_subject="google:owner")
    job = SimpleNamespace(requester_subject="google:requester")

    assert run_groups.can_manage_group(
        group, {"subject": "google:owner", "role": "user"}
    )
    assert run_groups.can_manage_group(
        group, {"subject": "google:admin", "role": "admin"}
    )
    assert not run_groups.can_manage_group(
        group, {"subject": "google:other", "role": "user"}
    )
    assert fournos_api._can_edit_run_groups(
        job, {"subject": "google:requester", "role": "user"}
    )
    assert not fournos_api._can_edit_run_groups(
        job, {"subject": "google:other", "role": "user"}
    )


def test_run_group_serialization_is_stable_and_product_neutral():
    campaign = SimpleNamespace(
        id="campaign-id",
        group_type="campaign",
        key="cpt-2026-09",
        display_name="September CPT",
        description="Monthly cycle",
        archived=False,
    )
    workload = SimpleNamespace(
        id="workload-id",
        group_type="workload",
        key="large-language-model",
        display_name="Large language model",
        description="",
        archived=True,
    )
    job = SimpleNamespace(group_memberships=[
        SimpleNamespace(group=workload),
        SimpleNamespace(group=campaign),
    ])

    serialized = db_service.serialize_run_groups(job)
    assert [item["group_type"] for item in serialized] == [
        "campaign", "workload"
    ]
    assert serialized[1]["archived"] is True
    assert all("owner" not in item for item in serialized)
