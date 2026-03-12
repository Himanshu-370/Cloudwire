import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")

SERVICE_ALIASES: Dict[str, str] = {
    "api-gateway": "apigateway",
    "apigw": "apigateway",
    "event-bridge": "eventbridge",
    "events": "eventbridge",
}


def normalize_service_name(service: str) -> str:
    key = service.lower().strip()
    return SERVICE_ALIASES.get(key, key)


DEFAULT_SERVICES = ["apigateway", "lambda", "sqs", "eventbridge", "dynamodb"]
ScanMode = Literal["quick", "deep"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class ScanRequest(BaseModel):
    region: str = "us-east-1"
    services: List[str] = Field(default_factory=lambda: DEFAULT_SERVICES.copy())
    mode: ScanMode = "quick"
    force_refresh: bool = False
    include_iam_inference: Optional[bool] = None
    include_resource_describes: Optional[bool] = None

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
        return cleaned


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


class APIErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class APIErrorResponse(BaseModel):
    error: APIErrorDetail
