import os
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .services import (  # noqa: F401  — re-exported for backward compatibility
    DEFAULT_SERVICES,
    SERVICE_ALIASES,
    normalize_service_name,
)

_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")
ScanMode = Literal["quick", "deep"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


_ARN_RE = re.compile(r"^arn:aws[a-z\-]*:[a-z0-9\-]+:[a-z0-9\-]*:(\d{12}|):.{0,1024}$")


class ScanRequest(BaseModel):
    region: str = "us-east-1"
    services: List[str] = Field(default_factory=lambda: DEFAULT_SERVICES.copy())
    mode: ScanMode = "quick"
    force_refresh: bool = False
    include_iam_inference: Optional[bool] = None
    include_resource_describes: Optional[bool] = None
    include_costs: bool = Field(default_factory=lambda: os.environ.get("CLOUDWIRE_INCLUDE_COSTS") == "1")
    tag_arns: Optional[List[str]] = Field(default=None, max_length=5000)

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or not _REGION_RE.match(cleaned):
            raise ValueError(f"'{cleaned}' is not a valid AWS region identifier (e.g. us-east-1)")
        return cleaned

    @field_validator("services")
    @classmethod
    def validate_services(cls, value: List[str]) -> List[str]:
        cleaned = [service.strip() for service in value if service and service.strip()]
        if not cleaned:
            raise ValueError("at least one AWS service must be selected")
        if len(cleaned) > 50:
            raise ValueError("at most 50 services may be selected per scan")
        return cleaned

    @field_validator("tag_arns")
    @classmethod
    def validate_tag_arns(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        for i, arn in enumerate(value):
            if not isinstance(arn, str) or len(arn) > 2048:
                raise ValueError(f"tag_arns[{i}] must be a string of at most 2048 characters")
            if not _ARN_RE.match(arn):
                raise ValueError(f"tag_arns[{i}] is not a valid ARN format")
        return value


class GraphResponse(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class ResourceResponse(BaseModel):
    node: Dict[str, Any]
    incoming: List[Dict[str, Any]]
    outgoing: List[Dict[str, Any]]


class ScanJobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    cached: bool
    status_url: str
    graph_url: str


class ScanJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    cancellation_requested: bool = False
    mode: ScanMode
    region: str
    services: List[str]
    progress_percent: int
    current_service: Optional[str] = None
    services_done: int
    services_total: int
    node_count: int
    edge_count: int
    warnings: List[str]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


class TagKeysResponse(BaseModel):
    keys: List[str]


class TagValuesResponse(BaseModel):
    key: str
    values: List[str]


class TagResourcesResponse(BaseModel):
    arns: List[str]
    services: List[str]


class APIErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class APIErrorResponse(BaseModel):
    error: APIErrorDetail
