from app.exceptions import DuplicateResourceError

class UserAlreadyExistsError(DuplicateResourceError):
    """Raised when signup is attempted with an email that's already registered."""
