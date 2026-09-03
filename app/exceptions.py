from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import Request
import structlog

logger = structlog.get_logger()


# HTTP status codes
INTERNAL_SERVER_ERROR = 500
CONFLICT = 409
UNPROCESSABLE_ENTITY = 422
RESOURCE_NOT_FOUND = 404
UNAUTHORIZED = 401  


# core exceptions
class DuplicateResourceError(Exception):
    """Raised when attempting to create a resource that already exists."""

class ResourceDoesNotExistError(Exception):
    """Raised when attempting to create a resource that already exists."""

class AuthTokenError(Exception):
    """Base class for any token-related authentication failure."""

class ConflictLoggingIn(Exception):
    "Base class for any logged in authentication failure"

class AuthenticationError(Exception):
    """Raised when authentication fails due to invalid credentials or other reasons."""

class TooManyRequestsError(Exception):
    """Raised when a user exceeds the allowed number of requests in a given time frame."""

# Handle exceptions
async def handle_duplicate_resource_error(request: Request, exc: DuplicateResourceError):
    return JSONResponse(
        status_code= CONFLICT,
        content={
            "status_code": CONFLICT,
            "error": str(exc),
        }
    )

async def handle_unprocessable_entity_exception(request: Request, exc:RequestValidationError):
    return JSONResponse(
        status_code=UNPROCESSABLE_ENTITY,
        content={
            "status_code": UNPROCESSABLE_ENTITY,
            "error": "Invalid request data",
            "details": exc.errors(),
        }
    )

async def handle_resource_does_not_exist_exception(request: Request, exc:ResourceDoesNotExistError):
    return JSONResponse(
        status_code=RESOURCE_NOT_FOUND,
        content={
            "status_code": RESOURCE_NOT_FOUND,
            "error": "Resource not found",
        }
    )

async def handle_auth_token_error(request: Request, exc:AuthTokenError):
    logger.warning("auth_token_error", error_type=type(exc).__name__, path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=UNAUTHORIZED,
        content={
            "status_code": UNAUTHORIZED,
            "error": "unauthorized access",
        }
    )

async def handle_authentication_error(request: Request, exc: AuthenticationError):
    logger.warning("authentication_error", error_type=type(exc).__name__, path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=UNAUTHORIZED,
        content={
            "status_code": UNAUTHORIZED,
            "error": str(exc),
        }
    )

async def handle_already_logged_in_error(request: Request, exc: ConflictLoggingIn):
    return JSONResponse(
        status_code=CONFLICT,
        content={"status_code": CONFLICT, "error": str(exc)},
    )

async def handle_too_many_requests(request: Request, exc: TooManyRequestsError):
    return JSONResponse(
        status_code=429,
        content={"status_code": 429, "error": str(exc)},
    )

async def handle_internal_exception(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=INTERNAL_SERVER_ERROR,
        content={
            "status_code": INTERNAL_SERVER_ERROR,
            "error": "An unexpected error occurred. Please try again later.",
        }
    )




