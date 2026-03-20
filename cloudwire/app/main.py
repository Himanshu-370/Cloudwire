"""CloudWire API — application assembly and middleware."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from cloudwire import __version__ as _app_version
from .errors import APIError, error_payload
from .scan_jobs import ScanJobStore

from .routes import scan as scan_routes
from .routes import tags as tag_routes
from .routes import terraform as terraform_routes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static-file directory (cloudwire/static/ relative to this package)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent.parent / "static"

# ---------------------------------------------------------------------------
# Application-wide singleton
# ---------------------------------------------------------------------------
job_store = ScanJobStore(max_workers=4)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    job_store.shutdown()


app = FastAPI(title="CloudWire API", version=_app_version, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Middleware — registered last-in first-out (outermost executes first)
# ---------------------------------------------------------------------------

_MAX_JSON_BODY_BYTES = 2 * 1024 * 1024  # 2 MB


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized JSON request bodies before Pydantic validation."""

    async def dispatch(self, request: Request, call_next):
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            # Fast path: reject immediately if Content-Length header exceeds limit
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > _MAX_JSON_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content=error_payload(
                        "payload_too_large",
                        f"Request body exceeds the {_MAX_JSON_BODY_BYTES // (1024 * 1024)} MB limit.",
                    ),
                )
            # Also check actual body size (handles chunked transfers / missing header)
            body = await request.body()
            if len(body) > _MAX_JSON_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content=error_payload(
                        "payload_too_large",
                        f"Request body exceeds the {_MAX_JSON_BODY_BYTES // (1024 * 1024)} MB limit.",
                    ),
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, exc.details),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        payload = detail
    elif isinstance(detail, str):
        payload = error_payload("http_error", detail)
    else:
        payload = error_payload("http_error", "Request failed.", detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_payload(
            "validation_error",
            "Request validation failed.",
            exc.errors(),
        ),
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_payload("internal_error", "Unexpected server error."),
    )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

api = APIRouter(prefix="/api")
scan_routes.register_routes(api, job_store)
tag_routes.register_routes(api)
terraform_routes.register_routes(api, job_store)
app.include_router(api)


# ---------------------------------------------------------------------------
# Static file serving — must be registered AFTER all API routes
# ---------------------------------------------------------------------------

if _STATIC_DIR.is_dir() and ((_STATIC_DIR / "assets").is_dir()):
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="assets")


@app.api_route("/api/{api_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], include_in_schema=False)
def api_not_found(api_path: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_payload("not_found", f"API endpoint '/api/{api_path}' not found."),
    )


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(
            status_code=503,
            content=error_payload(
                "frontend_not_built",
                "Frontend assets not found. Run `make build` to compile the UI.",
            ),
        )
    return FileResponse(str(index))
