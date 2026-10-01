from types import SimpleNamespace

import pytest

from app.api import fournos as fournos_api
from app.services import work_items


def _item(*, key="", url="", provider="jira"):
    return SimpleNamespace(provider=provider, key=key, url=url)


def test_jira_key_uses_configured_base_and_is_canonical(monkeypatch):
    monkeypatch.setattr(
        work_items.settings,
        "WORK_ITEM_ALLOWED_HOSTS",
        "jira.example.com",
    )
    monkeypatch.setattr(
        work_items.settings,
        "WORK_ITEM_JIRA_BASE_URL",
        "https://jira.example.com/",
    )

    assert work_items.normalize_work_item(_item(key="psap-58")) == {
        "provider": "jira",
        "key": "PSAP-58",
        "url": "https://jira.example.com/browse/PSAP-58",
    }


def test_jira_url_must_be_allowlisted_and_credential_free(monkeypatch):
    monkeypatch.setattr(
        work_items.settings,
        "WORK_ITEM_ALLOWED_HOSTS",
        "jira.example.com",
    )
    monkeypatch.setattr(work_items.settings, "WORK_ITEM_JIRA_BASE_URL", None)

    assert work_items.normalize_work_item(_item(
        url="https://jira.example.com/browse/PSAP-58"
    ))["key"] == "PSAP-58"

    unsafe = [
        "http://jira.example.com/browse/PSAP-58",
        "https://user:secret@jira.example.com/browse/PSAP-58",
        "https://other.example.com/browse/PSAP-58",
        "https://jira.example.com/browse/PSAP-58?token=secret",
        "https://jira.example.com/issues/PSAP-58",
    ]
    for url in unsafe:
        with pytest.raises(work_items.WorkItemValidationError):
            work_items.normalize_work_item(_item(url=url))


def test_duplicate_work_items_are_collapsed(monkeypatch):
    monkeypatch.setattr(
        work_items.settings,
        "WORK_ITEM_JIRA_BASE_URL",
        "https://jira.example.com",
    )
    monkeypatch.setattr(
        work_items.settings,
        "WORK_ITEM_ALLOWED_HOSTS",
        "jira.example.com",
    )
    normalized = work_items.normalize_work_items([
        _item(key="PSAP-58"),
        _item(url="https://jira.example.com/browse/PSAP-58"),
    ])
    assert len(normalized) == 1


def test_only_requester_or_admin_can_edit_work_items():
    job = SimpleNamespace(requester_subject="google:owner")
    assert fournos_api._can_edit_work_items(
        job, {"subject": "google:owner", "role": "user"}
    )
    assert fournos_api._can_edit_work_items(
        job, {"subject": "google:admin", "role": "admin"}
    )
    assert not fournos_api._can_edit_work_items(
        job, {"subject": "google:other", "role": "user"}
    )
    assert not fournos_api._can_edit_work_items(job, None)
