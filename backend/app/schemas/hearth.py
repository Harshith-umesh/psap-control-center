from pydantic import BaseModel, Field
from typing import Optional, List


class HearthGPU(BaseModel):
    vendor: str
    model: str
    short_name: str
    count: int
    node_count: Optional[int] = None


class HearthHardware(BaseModel):
    gpus: List[HearthGPU] = []
    total_gpus: int = 0
    last_discovery: Optional[str] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None


class HearthCondition(BaseModel):
    type: str
    status: str
    last_transition_time: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None


class HearthClusterResponse(BaseModel):
    name: str
    kubeconfig_secret: str
    owner: Optional[str] = None
    ttl: Optional[str] = None
    gpu_discovery_interval: Optional[str] = None
    hardware: Optional[HearthHardware] = None
    kubeconfig_status: Optional[str] = None
    locked: bool = False
    lock_expires_at: Optional[str] = None
    owner_set_at: Optional[str] = None
    lock_job_name: Optional[str] = None
    gpu_summary: Optional[str] = None
    conditions: List[HearthCondition] = []
    created_at: Optional[str] = None


class HearthClusterListResponse(BaseModel):
    clusters: List[HearthClusterResponse]
    total: int
    available: bool = True


class HearthStatusResponse(BaseModel):
    available: bool
    configured: bool = False
    cluster_count: int = 0
    total_gpus: int = 0
    locked_clusters: int = 0
    error: Optional[str] = None


class HearthConnectResponse(BaseModel):
    success: bool
    message: str


class HearthCredentialConnectRequest(BaseModel):
    api_server_url: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
