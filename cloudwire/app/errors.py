"""Shared error types and helpers for the CloudWire API."""

from __future__ import annotations

import logging
from typing import Any, Dict, NoReturn, Optional

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

logger = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_payload(code: str, message: str, details: Optional[Any] = None) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


def friendly_exception_message(exc: Exception) -> str:
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)):
        return "AWS credentials were not found. Set AWS credentials or run saml2aws login before scanning."
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return "Unable to reach the AWS API endpoint for the selected region."
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}:
            return "Your AWS session has expired. Refresh credentials and try again."
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return "AWS access was denied for this operation. Verify the assumed role permissions."
        logger.warning("AWS ClientError [%s]: %s", code, exc.response.get("Error", {}).get("Message", ""))
        return f"AWS API request failed ({code or 'ClientError'})."
    if isinstance(exc, BotoCoreError):
        return "The AWS SDK failed to complete the request."
    return "Unexpected server error."


def handle_tagging_error(exc: Exception, region: str, operation: str) -> NoReturn:
    """Convert AWS errors from tagging API to APIError. Always raises."""
    from fastapi import status

    logger.warning("Tag API error in %s (region=%s): %s: %s", operation, region, type(exc).__name__, exc)
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)):
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="aws_credentials_missing",
            message=friendly_exception_message(exc),
        ) from exc
    if isinstance(exc, ClientError):
        aws_code = exc.response.get("Error", {}).get("Code", "")
        if aws_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess", "UnauthorizedOperation"):
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="tags_access_denied",
                message=f"Access denied for {operation}. Ensure the IAM role has tag:GetTagKeys, tag:GetTagValues, and tag:GetResources permissions.",
                details={"aws_error_code": aws_code, "region": region},
            ) from exc
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="tags_api_error",
            message=f"AWS tagging API request failed ({aws_code or 'ClientError'}).",
            details={"aws_error_code": aws_code, "region": region},
        ) from exc
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_endpoint_unreachable",
            message=friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    if isinstance(exc, BotoCoreError):
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="tags_api_error",
            message=friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    raise APIError(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="unexpected_error",
        message=friendly_exception_message(exc),
    ) from exc
