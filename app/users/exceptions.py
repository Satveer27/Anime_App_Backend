from app.exceptions import DuplicateResourceError

class UserAlreadyExistsError(DuplicateResourceError):
    default_message = "A user with this email already exists."
