import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import fournos, reservations
from app.schemas.fournos import CreateClusterLockRequest
from app.schemas.reservation import ModificationRequestCreate, ReservationUpdate


def _reservation(*, creator="google:owner", user_name="editable-beneficiary"):
    return SimpleNamespace(
        created_by_subject=creator,
        user_name=user_name,
    )


def test_reservation_ownership_uses_immutable_subject():
    reservation = _reservation(user_name="attacker@example.com")

    with pytest.raises(HTTPException) as exc_info:
        reservations._require_owner_or_admin(
            reservation,
            {
                "subject": "google:attacker",
                "username": "attacker@example.com",
                "email": "attacker@example.com",
                "name": "attacker@example.com",
                "role": "user",
            },
            "cancel",
        )

    assert exc_info.value.status_code == 403

    reservations._require_owner_or_admin(
        reservation,
        {"subject": "google:owner", "role": "user"},
        "cancel",
    )
    reservations._require_owner_or_admin(
        _reservation(creator=None),
        {"subject": "google:admin", "role": "admin"},
        "cancel",
    )


def test_non_owner_cannot_mutate_reservation(monkeypatch):
    service = SimpleNamespace(
        get_reservation=AsyncMock(return_value=_reservation()),
        update_reservation=AsyncMock(),
        delete_reservation=AsyncMock(),
        cancel_reservation=AsyncMock(),
        request_modification=AsyncMock(),
    )
    monkeypatch.setattr(reservations, "ReservationService", lambda _db: service)
    attacker = {"subject": "google:attacker", "role": "user"}

    async def exercise_routes():
        calls = [
            reservations.update_reservation(
                "reservation-id",
                ReservationUpdate(title="renamed"),
                _user=attacker,
                db=object(),
            ),
            reservations.delete_reservation(
                "reservation-id", _user=attacker, db=object()
            ),
            reservations.cancel_reservation(
                "reservation-id", _user=attacker, db=object()
            ),
            reservations.request_modification(
                "reservation-id",
                ModificationRequestCreate(title="renamed"),
                BackgroundTasks(),
                _user=attacker,
                db=object(),
            ),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as exc_info:
                await call
            assert exc_info.value.status_code == 403

    asyncio.run(exercise_routes())
    service.update_reservation.assert_not_awaited()
    service.delete_reservation.assert_not_awaited()
    service.cancel_reservation.assert_not_awaited()
    service.request_modification.assert_not_awaited()


def test_public_job_metadata_removes_requester_identity_without_mutation():
    metadata = {
        "name": "job-name",
        "annotations": {
            fournos.REQUESTER_SUBJECT_ANNOTATION: "google:12345",
            fournos.REQUESTER_EMAIL_ANNOTATION: "person@example.com",
            fournos.REQUESTER_NAME_ANNOTATION: "Example Person",
            fournos.REQUESTER_PROVIDER_ANNOTATION: "google",
            "example.com/safe": "retained",
        },
    }

    public_metadata = fournos._public_job_metadata(metadata)

    assert public_metadata["annotations"] == {"example.com/safe": "retained"}
    assert fournos.REQUESTER_EMAIL_ANNOTATION in metadata["annotations"]


def test_public_job_spec_hides_owner_without_mutation():
    spec = {"cluster": "test-cluster", "owner": "Example Person"}

    public_spec = fournos._public_job_spec(spec, include_owner=False)

    assert public_spec == {"cluster": "test-cluster"}
    assert spec["owner"] == "Example Person"
    assert (
        fournos._public_job_spec(spec, include_owner=True)["owner"]
        == "Example Person"
    )


def test_testing_owner_prefers_verified_display_name():
    user = {
        "subject": "google:12345",
        "username": "person@example.com",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
        "role": "user",
    }

    assert fournos._verified_owner(user) == "Example Person"
    assert fournos._verified_owner({**user, "name": ""}) == "person@example.com"


def test_requester_scope_uses_only_authenticated_subject():
    user = {"subject": "google:12345"}

    assert fournos._requester_subject_for_scope("all", None) is None
    assert fournos._requester_subject_for_scope("mine", user) == "google:12345"

    with pytest.raises(HTTPException) as exc_info:
        fournos._requester_subject_for_scope("mine", None)
    assert exc_info.value.status_code == 401


def test_live_requester_filter_reads_immutable_annotation():
    job = {
        "metadata": {
            "annotations": {
                fournos.REQUESTER_SUBJECT_ANNOTATION: "google:12345",
            }
        },
        "spec": {"owner": "Editable Display Name"},
    }

    assert fournos._live_job_requester_subject(job) == "google:12345"
    assert fournos._live_job_requester_subject({"metadata": {}}) == ""


def test_recurring_child_inherits_requester_for_live_filter_without_mutation():
    child = {
        "metadata": {
            "labels": {fournos.k8s.LABEL_RECURRING_PARENT: "nightly-parent"}
        }
    }

    resolved = fournos._inherit_live_requester(
        child, {"nightly-parent": "google:12345"}
    )

    assert fournos._live_job_requester_subject(resolved) == "google:12345"
    assert fournos._live_job_requester_subject(child) == ""


def test_cluster_lock_ignores_client_supplied_owner(monkeypatch):
    captured = {}

    def create_fournos_job(body):
        captured["body"] = body
        return body

    monkeypatch.setattr(fournos.k8s, "create_fournos_job", create_fournos_job)
    user = {
        "subject": "google:12345",
        "username": "person@example.com",
        "email": "person@example.com",
        "name": "Example Person",
        "auth_provider": "google",
        "role": "user",
    }
    request = CreateClusterLockRequest(
        cluster="test-cluster",
        owner="Spoofed Owner",
        reason="Maintenance",
    )

    response = asyncio.run(fournos.create_cluster_lock(request, user=user))

    assert captured["body"]["spec"]["owner"] == "Example Person"
    assert response["owner"] == "Example Person"
