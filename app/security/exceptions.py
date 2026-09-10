from app.exceptions import AuthenticationError, ConflictError

class InvalidTokenError(AuthenticationError):
    default_message = "Token could not be verified."

class AlreadyLoggedInError(ConflictError):
    default_message = "You are already logged in. Please log out first."