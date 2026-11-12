"""Short-lived cache for the latest completed per-cluster GPU snapshot."""

from copy import deepcopy
from threading import Lock
from time import monotonic
from typing import Any, Optional


# Keep snapshots through the 3-minute refresh cadence plus 3 minutes of grace
# for a refresh that runs long or fails once.
GPU_STATUS_CACHE_TTL_SECONDS = 6 * 60

_lock = Lock()
_entries: dict[str, tuple[float, dict[str, Any]]] = {}


def store(cluster_id: str, status: dict[str, Any]) -> None:
    """Store a completed snapshot without retaining mutable caller data."""
    with _lock:
        _entries[cluster_id] = (monotonic(), deepcopy(status))


def get(cluster_id: str) -> Optional[dict[str, Any]]:
    """Return a fresh-enough snapshot, or None when a live read is needed."""
    with _lock:
        entry = _entries.get(cluster_id)
        if entry is None:
            return None
        created_at, status = entry
        if monotonic() - created_at > GPU_STATUS_CACHE_TTL_SECONDS:
            _entries.pop(cluster_id, None)
            return None
        return deepcopy(status)
