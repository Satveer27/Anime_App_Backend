from app.exceptions import AuthenticationError, ConflictError

class InvalidTokenError(AuthenticationError):
    default_message = "Token could not be verified."

class TokenDoesNotExist(AuthenticationError):
    default_message = "Token does not exist or has already been used."

class ReusingToken(AuthenticationError):
    default_message = "Token reuse detected; all sessions have been revoked."

class AlreadyLoggedInError(ConflictError):
    default_message = "You are already logged in. Please log out first."