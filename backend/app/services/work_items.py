"""Validation and canonicalization for external work-item references.

Control Center stores links only. It never calls Jira, follows redirects, or
stores Jira credentials, so an unavailable tracker cannot block execution.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from app.core.config import settings


JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}-[1-9][0-9]*$")
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,49}$")


class WorkItemValidationError(ValueError):
    pass


def _configured_hosts() -> set[str]:
    hosts = {
        value.strip().lower().rstrip(".")
        for value in settings.WORK_ITEM_ALLOWED_HOSTS.split(",")
        if value.strip()
    }
    if settings.WORK_ITEM_JIRA_BASE_URL:
        parsed = urlsplit(settings.WORK_ITEM_JIRA_BASE_URL.strip())
        if parsed.hostname:
            hosts.add(parsed.hostname.lower().rstrip("."))
    return hosts


def work_items_enabled() -> bool:
    return bool(_configured_hosts())


def _safe_jira_base() -> str:
    raw = (settings.WORK_ITEM_JIRA_BASE_URL or "").strip().rstrip("/")
    if not raw:
        hosts = _configured_hosts()
        if len(hosts) == 1:
            return "https://{}".format(next(iter(hosts)))
        raise WorkItemValidationError(
            "A full Jira URL is required when no single Jira host is configured"
        )
    parsed = urlsplit(raw)
    _validate_url_parts(parsed)
    return "https://{}".format(parsed.hostname.lower())


def _validate_url_parts(parsed) -> None:
    if parsed.scheme.lower() != "https":
        raise WorkItemValidationError("Work-item URLs must use HTTPS")
    if not parsed.hostname:
        raise WorkItemValidationError("Work-item URL has no host")
    if parsed.username or parsed.password:
        raise WorkItemValidationError("Work-item URLs must not contain credentials")
    try:
        if parsed.port not in (None, 443):
            raise WorkItemValidationError("Work-item URLs must use the HTTPS port")
    except ValueError as exc:
        raise WorkItemValidationError("Work-item URL has an invalid port") from exc
    if parsed.query or parsed.fragment:
        raise WorkItemValidationError(
            "Work-item URLs must not contain a query string or fragment"
        )
    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise WorkItemValidationError("Work-item hosts must not be IP addresses")
    if host not in _configured_hosts():
        raise WorkItemValidationError("Work-item host is not allowed")


def normalize_work_item(value) -> dict[str, str]:
    provider = str(getattr(value, "provider", "") or "jira").strip().lower()
    if not PROVIDER_RE.fullmatch(provider):
        raise WorkItemValidationError("Work-item provider is invalid")
    if provider != "jira":
        raise WorkItemValidationError("Unsupported work-item provider")

    supplied_key = str(getattr(value, "key", "") or "").strip().upper()
    supplied_url = str(getattr(value, "url", "") or "").strip()

    if supplied_url:
        parsed = urlsplit(supplied_url)
        _validate_url_parts(parsed)
        match = re.fullmatch(r"/browse/([^/]+)", parsed.path.rstrip("/"))
        if not match:
            raise WorkItemValidationError(
                "Jira URLs must use the canonical /browse/KEY path"
            )
        url_key = match.group(1).upper()
        if supplied_key and supplied_key != url_key:
            raise WorkItemValidationError("Work-item key does not match its URL")
        key = supplied_key or url_key
        base = "https://{}".format(parsed.hostname.lower())
    else:
        key = supplied_key
        base = _safe_jira_base()

    if not JIRA_KEY_RE.fullmatch(key):
        raise WorkItemValidationError("Jira key must look like PROJECT-123")

    return {
        "provider": provider,
        "key": key,
        "url": "{}/browse/{}".format(base, key),
    }


def normalize_work_items(values) -> list[dict[str, str]]:
    if len(values) > 20:
        raise WorkItemValidationError("At most 20 work items may be associated")
    normalized = []
    seen = set()
    for value in values:
        item = normalize_work_item(value)
        identity = (item["provider"], item["key"])
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(item)
    return normalized
