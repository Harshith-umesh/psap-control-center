"""Read bounded RHAIIS inference-server log tails from a target cluster."""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Callable

import yaml
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

from app.core.config import settings

logger = logging.getLogger(__name__)

_KSERVE_GROUP = "serving.kserve.io"
_KSERVE_VERSION = "v1beta1"
_KSERVE_PLURAL = "inferenceservices"
_INFERENCE_CONTAINER = "kserve-container"
_INFERENCE_POD_LABEL = "serving.kserve.io/inferenceservice"
_RUN_LABEL = "deployment_uuid"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_K8S_LABEL = re.compile(r"^[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$")


class InferenceLogsError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _valid_dns_name(value: str, max_length: int = 253) -> bool:
    return bool(value and len(value) <= max_length) and all(
        label and len(label) <= 63 and _DNS_LABEL.fullmatch(label)
        for label in value.split(".")
    )


def get_inference_service_reference(job: dict) -> dict | None:
    """Read only Forge's active KServe reference, never accept a UI-supplied name."""
    forge_status = (job.get("status") or {}).get("engineStatus", {}).get("forge", {})
    references = forge_status.get("relevantDeployments") or []
    if not isinstance(references, list):
        return None
    job_run_uuid = str(
        ((job.get("metadata") or {}).get("annotations") or {}).get("rhaiis.run-uuid") or ""
    )
    for reference in references:
        if not isinstance(reference, dict) or reference.get("kind") != "InferenceService":
            continue
        api_version = reference.get("apiVersion")
        if api_version and api_version != f"{_KSERVE_GROUP}/{_KSERVE_VERSION}":
            continue
        name = str(reference.get("name") or "")
        namespace = str(reference.get("namespace") or "")
        run_uuid = str(reference.get("runUUID") or "")
        if not _valid_dns_name(name) or not _valid_dns_name(namespace, 63):
            continue
        if len(run_uuid) > 63 or not _K8S_LABEL.fullmatch(run_uuid):
            continue
        if job_run_uuid and run_uuid != job_run_uuid:
            continue
        return {"name": name, "namespace": namespace, "run_uuid": run_uuid}
    return None


def _decode_kubeconfig(secret: dict | None) -> dict:
    encoded = ((secret or {}).get("data") or {}).get("kubeconfig")
    if not encoded:
        raise InferenceLogsError(503, "Target-cluster credentials are unavailable or malformed.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        kubeconfig = yaml.safe_load(raw)
    except Exception as exc:
        raise InferenceLogsError(503, "Target-cluster credentials are unavailable or malformed.") from exc
    if not isinstance(kubeconfig, dict):
        raise InferenceLogsError(503, "Target-cluster credentials are unavailable or malformed.")
    if not kubeconfig.get("current-context"):
        contexts = kubeconfig.get("contexts") or []
        if not contexts or not isinstance(contexts[0], dict) or not contexts[0].get("name"):
            raise InferenceLogsError(503, "Target-cluster credentials are unavailable or malformed.")
        kubeconfig["current-context"] = contexts[0]["name"]
    return kubeconfig


def _waiting(message: str) -> dict:
    return {
        "pod": None,
        "phase": "Waiting",
        "container": _INFERENCE_CONTAINER,
        "logs": "",
        "message": message,
    }


def get_inference_server_logs(
    job: dict,
    secret_reader: Callable[[str, str], dict | None],
    *,
    tail_lines: int = 300,
) -> dict:
    spec = job.get("spec") or {}
    cluster = str(spec.get("cluster") or "")
    reference = get_inference_service_reference(job)
    if len(cluster) > 63 or not _DNS_LABEL.fullmatch(cluster):
        raise InferenceLogsError(409, "The FournosJob has no valid target cluster.")
    if reference is None:
        raise InferenceLogsError(409, "Forge has not published an active inference-service reference.")

    try:
        secret = secret_reader(
            f"kubeconfig-{cluster}", settings.TARGET_CLUSTER_SECRETS_NAMESPACE
        )
    except ApiException as exc:
        logger.warning("Cannot read target cluster credentials (status=%s)", exc.status)
        raise InferenceLogsError(503, "Target-cluster credentials could not be read.") from exc
    except RuntimeError as exc:
        logger.warning("Cannot access management-cluster Secret API: %s", exc)
        raise InferenceLogsError(503, "Target-cluster credentials could not be read.") from exc

    kubeconfig = _decode_kubeconfig(secret)
    try:
        target_client = k8s_config.new_client_from_config_dict(kubeconfig)
    except Exception as exc:
        logger.warning("Could not initialize target-cluster client", exc_info=True)
        raise InferenceLogsError(502, "Could not connect to the target cluster.") from exc

    try:
        target_custom = client.CustomObjectsApi(target_client)
        try:
            isvc = target_custom.get_namespaced_custom_object(
                _KSERVE_GROUP, _KSERVE_VERSION, reference["namespace"],
                _KSERVE_PLURAL, reference["name"], _request_timeout=15,
            )
        except ApiException as exc:
            if exc.status == 404:
                return _waiting("Waiting for the InferenceService to appear on the target cluster.")
            logger.warning("Could not read target InferenceService (status=%s)", exc.status)
            raise InferenceLogsError(502, "Could not read the target InferenceService.") from exc

        labels = (isvc.get("metadata") or {}).get("labels") or {}
        if labels.get(_RUN_LABEL) != reference["run_uuid"]:
            raise InferenceLogsError(409, "The InferenceService does not match the active FournosJob run.")

        target_core = client.CoreV1Api(target_client)
        try:
            pods = target_core.list_namespaced_pod(
                reference["namespace"],
                label_selector=f"{_INFERENCE_POD_LABEL}={reference['name']}",
                limit=100,
                _request_timeout=15,
            )
        except ApiException as exc:
            logger.warning("Could not list target predictor pods (status=%s)", exc.status)
            raise InferenceLogsError(502, "Could not list target inference-server pods.") from exc

        candidates = [
            pod for pod in pods.items
            if _INFERENCE_CONTAINER in {
                container.name for container in (getattr(getattr(pod, "spec", None), "containers", None) or [])
            }
        ]
        candidates.sort(
            key=lambda pod: (
                (getattr(getattr(pod, "status", None), "phase", None) or "") == "Running",
                getattr(getattr(pod, "metadata", None), "creation_timestamp", None).isoformat()
                if getattr(getattr(pod, "metadata", None), "creation_timestamp", None) else "",
            ),
            reverse=True,
        )
        if not candidates:
            return _waiting("Waiting for the inference-server container to start.")

        pod = candidates[0]
        try:
            logs = target_core.read_namespaced_pod_log(
                pod.metadata.name,
                reference["namespace"],
                container=_INFERENCE_CONTAINER,
                tail_lines=max(1, min(int(tail_lines), 300)),
                limit_bytes=1_000_000,
                timestamps=True,
                _request_timeout=30,
            ) or ""
        except ApiException as exc:
            logger.warning("Could not read target inference logs (status=%s)", exc.status)
            raise InferenceLogsError(502, "Could not read inference-server logs.") from exc
        return {
            "pod": pod.metadata.name,
            "phase": getattr(getattr(pod, "status", None), "phase", None) or "Unknown",
            "container": _INFERENCE_CONTAINER,
            "logs": logs,
            "message": "",
        }
    finally:
        try:
            target_client.close()
        except Exception:
            logger.debug("Could not close target cluster client", exc_info=True)
