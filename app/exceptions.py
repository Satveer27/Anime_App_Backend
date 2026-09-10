from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import Request, status
import structlog

logger = structlog.get_logger()


# HTTP status codes
INTERNAL_SERVER_ERROR = 500
CONFLICT = 409
UNPROCESSABLE_ENTITY = 422
RESOURCE_NOT_FOUND = 404
UNAUTHORIZED = 401  
FORBIDDEN = 403 


# core exceptions
class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)

class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "Bad request."


class AuthenticationError(AppError):
    """The caller isn't properly authenticated — missing, invalid, expired,
    or revoked credentials/tokens."""
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "Authentication failed."


class ForbiddenError(AppError):
    """The caller is authenticated but not allowed to do this."""
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "You do not have permission to perform this action."


class ResourceDoesNotExistError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_message = "Resource not found."


class ConflictError(AppError):
    """The request conflicts with the resource's current state."""
    status_code = status.HTTP_409_CONFLICT
    default_message = "Conflict with current state."


class DuplicateResourceError(ConflictError):
    default_message = "Resource already exists."


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_message = "Too many requests. Please try again later."


# Handle exceptions
async def handle_app_error(request: Request, exc: AppError):
    logger.warning(
        "app_error",
        error_type=type(exc).__name__,
        status_code=exc.status_code,
        path=str(request.url),
        error=exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"status_code": exc.status_code, "error": exc.message},
    )

async def handle_validation_error(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        error.pop("ctx", None)
    logger.warning("validation_error", path=str(request.url), errors=errors)
    return JSONResponse(
        status_code=422,
        content={"status_code": 422, "error": "Invalid request data", "details": errors},
    )

async def handle_internal_exception(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"status_code": 500, "error": "An unexpected error occurred. Please try again later."},
    )



