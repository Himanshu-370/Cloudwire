"""Shared AWS client factories and helpers."""

from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)
from fastapi import status

from .errors import APIError, friendly_exception_message
from .models import _REGION_RE

# Shared config for lightweight API calls (STS, Tagging API)
_LIGHT_CLIENT_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 10},
    max_pool_connections=8,
    connect_timeout=3,
    read_timeout=10,
)


def tagging_client(region: str):
    session = boto3.session.Session(region_name=region)
    return session.client("resourcegroupstaggingapi", config=_LIGHT_CLIENT_CONFIG)


def validate_region(region: str) -> str:
    cleaned = region.strip()
    if not cleaned or not _REGION_RE.match(cleaned):
        raise APIError(
            status_code=422,
            code="validation_error",
            message=f"'{cleaned}' is not a valid AWS region identifier (e.g. us-east-1)",
        )
    return cleaned


def resolve_account_id(region: str) -> str:
    session = boto3.session.Session(region_name=region)
    client = session.client("sts", config=_LIGHT_CLIENT_CONFIG)
    try:
        identity = client.get_caller_identity()
        return str(identity.get("Account", "unknown"))
    except (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError) as exc:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="aws_credentials_missing",
            message=friendly_exception_message(exc),
        ) from exc
    except ClientError as exc:
        aws_code = exc.response.get("Error", {}).get("Code", "")
        status_code = (
            status.HTTP_403_FORBIDDEN
            if aws_code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
            else status.HTTP_401_UNAUTHORIZED
            if aws_code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}
            else status.HTTP_502_BAD_GATEWAY
        )
        raise APIError(
            status_code=status_code,
            code="aws_account_lookup_failed",
            message=friendly_exception_message(exc),
            details={"aws_error_code": aws_code or None, "region": region},
        ) from exc
    except (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_endpoint_unreachable",
            message=friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    except BotoCoreError as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_client_error",
            message=friendly_exception_message(exc),
            details={"region": region},
        ) from exc
