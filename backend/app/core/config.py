from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    PROJECT_NAME: str = "PSAP Control Center"
    VERSION: str = "1.5.0"  # x-release-please-version
    API_V1_PREFIX: str = "/api/v1"
    
    DATABASE_URL: str = "sqlite+aiosqlite:///./psap_control_center.db"
    
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours
    
    KUBECONFIG_STORAGE_PATH: str = "./kubeconfigs"
    
    MLFLOW_BASE_URL: Optional[str] = None
    MLFLOW_TRACKING_URI: Optional[str] = None
    MLFLOW_TRACKING_USERNAME: Optional[str] = None
    MLFLOW_TRACKING_PASSWORD: Optional[str] = None
    MLFLOW_WORKSPACE: Optional[str] = None
    MLFLOW_TRACKING_INSECURE_TLS: bool = False
    MLFLOW_FAILURE_ENRICHMENT_ENABLED: bool = True
    MLFLOW_REQUEST_TIMEOUT_SECONDS: float = 10.0
    
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD: str = ""

    USER_USERNAME: str = ""
    USER_PASSWORD: str = ""

    # Control Center sign-in. Local accounts remain available as a
    # development/emergency fallback while Google Workspace SSO is trialled.
    LOCAL_LOGIN_ENABLED: bool = True
    GOOGLE_OAUTH_ENABLED: bool = False
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    GOOGLE_ALLOWED_DOMAIN: Optional[str] = None
    GOOGLE_ADMIN_EMAILS: str = ""

    HEARTH_ENABLED: bool = True
    HEARTH_NAMESPACE: str = "hearth"
    HEARTH_KUBECONFIG_PATH: Optional[str] = None

    BILLING_CSV_STORAGE_PATH: str = "./billing_csvs"

    # Fournos Testing Tab
    FOURNOS_NAMESPACE: str = "psap-automation"
    FOURNOS_API_GROUP: str = "fournos.dev"
    FOURNOS_API_VERSION: str = "v1"
    FOURNOS_JOB_PLURAL: str = "fournosjobs"
    FOURNOS_K8S_TIMEOUT: int = 30

    # Tekton CRD settings
    TEKTON_API_GROUP: str = "tekton.dev"
    TEKTON_API_VERSION: str = "v1"

    # Forge project discovery
    FORGE_REPO_PATH: Optional[str] = None
    FORGE_PROJECTS_CONFIG_PATH: str = "/etc/fournos-dashboard/projects.yaml"
    FORGE_GITHUB_REPO: str = "openshift-psap/forge"
    FORGE_GITHUB_REF: str = "main"
    # Optional authentication raises GitHub's API allowance, but the sync
    # path also consolidates repository discovery into a shared Git Trees
    # snapshot so an unauthenticated deployment remains safe.
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_SYNC_INTERVAL_SECONDS: int = 60 * 60
    GITHUB_SYNC_FAILURE_BACKOFF_SECONDS: int = 5 * 60

    FOURNOS_DEFAULT_PIPELINES: str = "forge-full,forge-prepare-test,forge-test-only,forge-prepare-only,forge-replot,nightly"

    # Optional, read-only external work-item associations for test runs.
    # Hosts are deployment configuration rather than UI constants so each
    # environment can explicitly choose which Jira deployment it trusts.
    WORK_ITEM_ALLOWED_HOSTS: str = ""
    WORK_ITEM_JIRA_BASE_URL: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # e.g. LOG_LEVEL, which logger.py reads directly from os.environ


settings = Settings()


_INSECURE_SECRET_KEYS = {
    "dev-secret-key",
    "change-this-in-production",
    "your-secret-key-change-in-production",
    "your-super-secret-key-change-in-production",
}


def validate_secret_key(secret_key: str) -> None:
    """Reject missing, documented, or undersized session-signing keys."""
    value = secret_key.strip()
    if not value:
        raise RuntimeError("SECRET_KEY is required")
    if value in _INSECURE_SECRET_KEYS or (
        value.startswith("<") and value.endswith(">")
    ):
        raise RuntimeError("SECRET_KEY must not use a documented placeholder")
    if len(value) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters")


validate_secret_key(settings.SECRET_KEY)

_missing = []
if settings.LOCAL_LOGIN_ENABLED:
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        _missing.append("ADMIN_USERNAME / ADMIN_PASSWORD")
    if not settings.USER_USERNAME or not settings.USER_PASSWORD:
        _missing.append("USER_USERNAME / USER_PASSWORD")
if settings.GOOGLE_OAUTH_ENABLED:
    for field_name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "GOOGLE_ALLOWED_DOMAIN",
    ):
        if not getattr(settings, field_name):
            _missing.append(field_name)
if _missing:
    raise RuntimeError(
        f"Required authentication settings not set via environment variables: {', '.join(_missing)}. "
        "Set them in your .env file or environment."
    )

os.makedirs(settings.KUBECONFIG_STORAGE_PATH, exist_ok=True)
os.makedirs(settings.BILLING_CSV_STORAGE_PATH, exist_ok=True)
